#!/usr/bin/env python3
"""抓取机器人串口日志，实时提取 pipe: 管线遥测；--assert 时对位点做断言。

用法:
  server/.venv/bin/python tools/serial_telemetry.py [秒数，默认 120]
  server/.venv/bin/python tools/serial_telemetry.py 25 --assert [--start N] [--live]
      [--min-lines K] [--offset-tol S]

插上 USB 后自动探测 /dev/cu.usbmodem*，全量日志存 /tmp/serial_full.log。

断言模式（issue #3 验收缝）——抓取前先开始，再触发播放（起流锚点必须被抓到）：
  - position_monotonic          位点单调不减（同一会话内不回退）
  - position_not_below_start    位点下限 ≥ 起点（带 ss 起流时是绝对位点，不是相对位点）
  - position_tracks_wall_clock  位点不领先挂钟（上四分位偏差 ≤ 容差）——ring 预读
                                （约 0.8s）若被计入位点会恒定领先约 +0.8s，此
                                断言即「位点只算推出的帧」的串口佐证；取 q75
                                而非中位数：网络卡顿产生负偏差，不会误触
  - position_advance_rate       位点总推进 / 挂钟 ≥ 0.7（帧时长折算错误的兜底）
  - live_records_no_position    直播流只有 pos=live 标记，不出现数字位点

一个抓取窗口里可能连播多首（每首一条起流锚点行），断言**按会话分段**跑：位点
单调、起点下限、挂钟偏差都只在同一会话内比较，换歌不误报。起点取自锚点行
（`start=Ns`，设备自己的口径）——CLI `--start` 只在没有锚点行时兜底；两者都在
且不一致会单独报一条 `cli_matches_anchor`，免得断言在参数漏传时静默放行。
"""
import glob
import re
import statistics
import sys
import time

PIPE_RE = re.compile(
    r"pipe:\s+ring=(\d+)/(\d+)\s+in_buf=(\d+)B\s+read=(\d+)B/2s"
    r"\s+pushed=(\d+)\s+fail=(\d+)(?:\s+pos=(?P<pos>\d+s|live))?"
    r"(?P<flags>.*)$")
SESSION_RE = re.compile(
    r"Music stream started: title='(?P<title>.*?)' author='(?P<author>.*?)' "
    r"duration=(?P<duration>\d+)s form=(?P<form>\w+) start=(?P<start>\d+)s")

DEFAULT_OFFSET_TOL = 0.6  # 位点-挂钟偏差容差（秒）：ring 预读 ≈ +0.8s 必越界


def parse_pipe_line(text):
    """解析 pipe: 行。命中返回 dict，否则 None。

    pos 字段：有限内容为整数秒（绝对位点 = 起点 + 已推帧折算）；直播流为
    字面 'live'；旧固件无 pos 字段时为 None。
    """
    m = PIPE_RE.search(text)
    if not m:
        return None
    pos_raw = m.group("pos")
    if pos_raw is None:
        pos = None
    elif pos_raw == "live":
        pos = "live"
    else:
        # 固件格式 pos=13s：数字 + 单位后缀（沿用管线遥测字段风格）。
        try:
            pos = int(pos_raw.rstrip("s"))
        except ValueError:
            pos = None
    flags = m.group("flags") or ""
    return {
        "ring": int(m.group(1)),
        "ring_max": int(m.group(2)),
        "in_buf": int(m.group(3)),
        "read": int(m.group(4)),
        "pushed": int(m.group(5)),
        "fail": int(m.group(6)),
        "pos": pos,
        "eof": "EOF" in flags,
        "decode_error": "DECERR" in flags,
    }


def parse_session_line(text):
    """解析起流锚点行（Music stream started: …）。未命中返回 None。"""
    m = SESSION_RE.search(text)
    if not m:
        return None
    return {
        "title": m.group("title"),
        "author": m.group("author"),
        "duration_s": int(m.group("duration")),
        "form": m.group("form"),
        "start_s": int(m.group("start")),
    }


class AssertionResult:
    def __init__(self, name, ok, detail):
        self.name = name
        self.ok = ok
        self.detail = detail

    def __repr__(self):
        return f"AssertionResult({self.name!r}, ok={self.ok}, detail={self.detail!r})"


def effective_start(session, cli_start):
    """本会话的起点：锚点行自报的 start= 优先，CLI --start 只在无锚点时兜底。"""
    if session["session"] is not None:
        return session["session"]["start_s"]
    return cli_start if cli_start is not None else 0


def evaluate_capture(samples, *, start_s=None, live=None, min_lines=3,
                     offset_tol=DEFAULT_OFFSET_TOL):
    """对抓到的采样做断言。samples = [{'t': 抓到时刻, 'line': 原始行}, …]。

    返回 [AssertionResult, …]；全部 .ok 为 True 才算验收通过。

    按会话分段：起流锚点行（Music stream started）开一段，其后到下一个锚点
    之间的 pipe: 行都属于该段。起点与形态取自该段的锚点，CLI 参数只作兜底与
    核对——这样「位点是绝对值」「直播不记位点」不靠调用者记得传参。
    """
    results = []

    def add(name, ok, detail):
        results.append(AssertionResult(name, bool(ok), detail))

    sessions = []  # [{'t', 'session', 'events': [...]}, …]
    orphan_events = []  # 锚点缺失时的兜底容器
    for s in samples:
        session = parse_session_line(s["line"])
        if session is not None:
            sessions.append({"t": s["t"], "session": session, "events": []})
            continue
        parsed = parse_pipe_line(s["line"])
        if parsed is None:
            continue
        event = {"t": s["t"], "parsed": parsed}
        if sessions:
            sessions[-1]["events"].append(event)
        else:
            orphan_events.append(event)
    if orphan_events:
        # 没抓到锚点的抓取（旧固件或起播早于开抓）：整段当一次会话，起点靠 CLI。
        sessions.insert(0, {"t": None, "session": None, "events": orphan_events})

    events = [e for s in sessions for e in s["events"]]
    add("enough_position_lines", len(events) >= min_lines,
        f"{len(events)} 行 pipe: 遥测（要求 ≥ {min_lines}）")

    anchored = [s for s in sessions if s["session"] is not None]
    add("session_anchor_seen", bool(anchored),
        f"{len(anchored)} 条起流锚点（Music stream started）"
        + ("" if anchored else "——请先开始抓取再触发播放"))

    is_live = live if live is not None else (
        anchored[0]["session"]["form"] == "live" if anchored else False)

    if is_live:
        bad = [e for e in events if isinstance(e["parsed"]["pos"], int)]
        add("live_records_no_position", len(bad) == 0 and len(events) > 0,
            f"数字位点 {len(bad)} 处（直播流必须全部 pos=live）")
        return results

    # ── 有限内容：逐会话断言 ──────────────────────────────────────
    monotonic_ok = True
    below_start = []
    for s in sessions:
        numeric = [(e["t"], e["parsed"]["pos"]) for e in s["events"]
                   if isinstance(e["parsed"]["pos"], int)]
        if len(numeric) >= 2 and not all(
                numeric[i][1] <= numeric[i + 1][1] for i in range(len(numeric) - 1)):
            monotonic_ok = False
        seg_start = effective_start(s, start_s)
        for _, pos in numeric:
            if pos < seg_start - 1:
                below_start.append((seg_start, pos))
    add("position_monotonic", monotonic_ok, "同一会话内位点单调不减"
        if monotonic_ok else "某会话内位点出现回退（续播/暂停会跳词）")

    # 起点核对：锚点行是设备自报口径，CLI 只是兜底。两者都在却不一致 =
    # 调用方传错了参数，断言会静默放行——单独报出来。
    if start_s is not None and anchored:
        mismatch = [s for s in anchored if s["session"]["start_s"] != start_s]
        add("cli_matches_anchor", not mismatch,
            f"CLI --start={start_s}s 与锚点 start={anchored[0]['session']['start_s']}s "
            + ("一致" if not mismatch else f"不一致（{len(mismatch)} 条锚点）"))

    if not any(isinstance(e["parsed"]["pos"], int) for e in events):
        add("position_not_below_start", False, "没有数字位点样本")
    else:
        add("position_not_below_start", not below_start,
            "最小位点均不低于各自会话起点"
            if not below_start else
            "位点低于起点：" + ", ".join(f"起点{st}s 处见 {p}s"
                                        for st, p in below_start[:3])
            + "（位点像相对位点，起点丢了）")

    # 位点-挂钟：以本会话锚点为 0 点。位点若含 ring 预读会恒定领先（+0.8s 量
    # 级）；网络卡顿只会造成负偏差——故领先检测取上四分位（q75），卡顿免疫。
    offsets = []
    advances = []
    for s in sessions:
        numeric = [(e["t"], e["parsed"]["pos"]) for e in s["events"]
                   if isinstance(e["parsed"]["pos"], int)]
        if s["t"] is None or len(numeric) < 2:
            continue
        seg_start = effective_start(s, start_s)
        offsets += [p - seg_start - (t - s["t"]) for t, p in numeric]
        elapsed = numeric[-1][0] - numeric[0][0]
        if elapsed > 0:
            advances.append((numeric[-1][1] - numeric[0][1]) / elapsed)

    if not offsets:
        add("position_tracks_wall_clock", False,
            "可分段的数字位点样本不足（每个会话需 ≥ 2 行，且要有起流锚点）")
        add("position_advance_rate", False, "同上，无从计算推进率")
    else:
        q75 = statistics.quantiles(offsets, n=4)[1] if len(offsets) >= 4 else max(offsets)
        add("position_tracks_wall_clock", q75 <= offset_tol,
            f"位点-挂钟上四分位偏差 {q75:+.2f}s（容差 +{offset_tol}s）；"
            f"恒定领先 ≈ +0.8s 即 ring 预读被计入位点")
        worst = min(advances) if advances else 0.0
        add("position_advance_rate", bool(advances) and worst >= 0.7,
            f"各会话位点推进率最低 {worst:.2f}（下限 0.7）")
    return results


def find_port():
    ports = (glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
             + glob.glob("/dev/cu.SLAB*"))
    return ports[0] if ports else None


def capture_with_log(port, duration, log_file):
    """抓串口 duration 秒，原始字节写进 log_file。

    返回 (samples, 关键行文本列表)。samples 供 --assert 断言；
    pyserial 延迟到调用才导入：纯逻辑测试不依赖串口库。
    """
    import serial

    samples = []
    key_lines = []
    ser = serial.Serial(port, 115200, timeout=1)
    try:
        buf = b""
        start = time.time()
        while time.time() - start < duration:
            chunk = ser.read(4096)
            if not chunk:
                continue
            log_file.write(chunk)
            log_file.flush()
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                t = time.time()
                if "pipe:" in text or "Music" in text or "music" in text:
                    ts = time.strftime("%H:%M:%S", time.localtime(t))
                    tagged = f"[{ts}] {text}"
                    key_lines.append(tagged)
                    print(tagged)
                    samples.append({"t": t, "line": text})
    finally:
        ser.close()
    return samples, key_lines


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    # start/live 缺省为 None（未指定）：起流锚点自报的 start=/form= 优先，
    # CLI 只在没抓到锚点时兜底。
    flags = {"assert": False, "start": None, "live": None, "min-lines": 3,
             "offset-tol": DEFAULT_OFFSET_TOL}
    positional = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--assert":
            flags["assert"] = True
        elif a == "--live":
            flags["live"] = True
        elif a == "--start":
            i += 1
            flags["start"] = int(args[i])
        elif a == "--min-lines":
            i += 1
            flags["min-lines"] = int(args[i])
        elif a == "--offset-tol":
            i += 1
            flags["offset-tol"] = float(args[i])
        else:
            positional.append(a)
        i += 1
    duration = int(positional[0]) if positional else 120

    port = find_port()
    if not port:
        print("❌ 没找到 USB 串口（/dev/cu.usbmodem*）。确认 USB 线已插好。")
        return 1
    print(f"✅ 打开 {port}，抓取 {duration}s，全量日志 → /tmp/serial_full.log")
    print("   关注行: pipe: ring=…(ring水位) in_buf=…(解码缓冲) read=…/2s(网络读) "
          "pushed=… fail=… pos=…(位点)")

    with open("/tmp/serial_full.log", "ab") as f:
        f.write(f"\n==== capture {time.strftime('%Y-%m-%d %H:%M:%S')} ====\n".encode())
        samples, key_lines = capture_with_log(port, duration, f)

    if not flags["assert"]:
        print(f"\n==== 完成，共 {len(key_lines)} 行关键日志，全量见 /tmp/serial_full.log ====")
        return 0

    print(f"\n==== 位点断言（start={flags['start']}s live={flags['live']}）====")
    results = evaluate_capture(
        samples, start_s=flags["start"], live=flags["live"],
        min_lines=flags["min-lines"], offset_tol=flags["offset-tol"])
    ok = True
    for r in results:
        mark = "✅" if r.ok else "❌"
        print(f"  {mark} {r.name}: {r.detail}")
        ok = ok and r.ok
    print(f"==== 断言{'全部通过' if ok else '未通过'} ====")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
