#!/usr/bin/env python3
"""抓取机器人串口日志，实时提取 pipe: 管线遥测；--assert 时对位点做断言。

用法:
  server/.venv/bin/python tools/serial_telemetry.py [秒数，默认 120]
  server/.venv/bin/python tools/serial_telemetry.py 25 --assert [--start N] [--live]
      [--min-lines K] [--offset-tol S] [--expect-restart] [--expect-auto-resume]

插上 USB 后自动探测 /dev/cu.usbmodem*，全量日志存 /tmp/serial_full.log。

断言模式（issue #3 验收缝）——抓取前先开始，再触发播放（起流锚点必须被抓到）：
  - position_monotonic          位点单调不减（同一会话内不回退；重启式续播的
                                ±2s 受控回退由「Music resume: mode=restart」
                                标记放行）
  - position_not_below_start    位点下限 ≥ 起点（带 ss 起流时是绝对位点）
  - position_tracks_wall_clock  位点不领先挂钟（上四分位偏差 ≤ 容差）——ring 预读
                                （约 0.8s）若被计入位点会恒定领先约 +0.8s
  - position_advance_rate       位点总推进 / 净播放时长 ≥ 0.7（暂停不开销）
  - live_records_no_position    直播流只有 pos=live 标记，不出现数字位点

暂停/续播（issue #4）——暂停期间位点必须冻结，续播必须接在原处：
  - pause_freezes_position      暂停跨度内位点不再增长
  - pause_kind_recorded         暂停样本带暂停种类（PAUSED_CONV / PAUSED_USER）
  - resume_continues_position   续播接缝：continue 接在冻结位点 ±0.5s（spec 的
                                验收缝）、restart 回退不超过锚点自报 margin+0.5s
                                且不前进超过 0.5s
  - user_pause_never_auto_resumes  用户暂停的跨度内不得出现 Music auto-resume:（说
                                「暂停」后随便聊一句，音乐不自动响）
  - 可选 --expect-restart       本次抓取要覆盖「原连接不可用」分支
  - 可选 --expect-auto-resume   本次抓取要覆盖「静默数秒自动续播」

一个抓取窗口里可能连播多首（每首一条起流锚点行），断言**按会话分段**跑：位点
单调、起点下限、挂钟偏差都只在同一会话内比较，换歌不误报。起点取自锚点行
（`start=Ns`，设备自己的口径）——CLI `--start` 只在没有锚点行时兜底；两者都在
且不一致会单独报一条 `cli_matches_anchor`，免得断言在参数漏传时静默放行。

位点精度：`pipe:` 周期行是**整秒**（`pos=73s`，2 秒一拍的采样不该伪造精度）；
三条锚点行（`Music pause: pos=`、`Music resume: mode=continue at=`、
`mode=restart at=/from=`）是**一位小数**（`73.4s`）——spec 的「恢复后位点从原处
继续（±0.5s）」用整秒量化够不着。margin= 仍是整秒。
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
# 锚点行的位点带**一位小数**（`73.4s`）：spec 的「恢复后位点从原处继续（±0.5s）」
# 用整秒量化够不着，故 pause/resume 三条锚点行改用一位小数；`pipe:` 周期行继续
# 整秒（2 秒一拍的采样不该伪造精度）。两条正则因此分开写，别互相串精度。
_NUM_POS = r"(?P<pos>\d+(?:\.\d+)?s|live)"
# 暂停锚点：设备进暂停时打一次，带冻结位点与两种语义中的哪一种。
PAUSE_RE = re.compile(r"Music pause:\s+kind=(?P<kind>user|conversation)\s+pos=" + _NUM_POS)
# 续播锚点：两段式恢复走了哪一段（continue = 原连接接着读，restart = 按位点重起流）。
# continue 的 at= 是接缝处的位点（暂停时冻结的那个）；restart 的 at= 是**请求的
# 重起位点**、from= 才是暂停时的位点，margin= 是安全余量秒数——用来核对「宁可
# 重复一小段，不跳词」。
RESUME_RE = re.compile(
    r"Music resume:\s+mode=(?P<mode>continue|restart)\s+at=" + _NUM_POS +
    r"(?:\s+from=(?P<from>\d+(?:\.\d+)?s|live))?(?:\s+margin=(?P<margin>\d+)s)?")
# 自动续播：静默计时到阈值（issue #4 的会话性暂停由它驱动）。
AUTO_RESUME_RE = re.compile(r"Music auto-resume:\s+quiet=(?P<quiet>\d+)s")

DEFAULT_OFFSET_TOL = 0.6  # 位点-挂钟偏差容差（秒）：ring 预读 ≈ +0.8s 必越界
# 续播接缝容差（spec 的 ±0.5s）：锚点行带一位小数，接缝「接在原处」判据是
# |at= - 冻结位点| ≤ 0.5s；重启式续播的回退按锚点自报的 margin 放行
# （回退 ≤ margin + 0.5s），且不许前进超过 0.5s。
RESUME_SEAM_TOL = 0.5
RESTART_MARGIN_FALLBACK = 2.0  # 锚点没带 margin= 时的兜底安全余量（固件常量）

def parse_pipe_line(text):
    """解析 pipe: 行。命中返回 dict，否则 None。

    pos 字段：有限内容为整数秒（绝对位点 = 起点 + 已推帧折算）；直播流为
    字面 'live'；旧固件无 pos 字段时为 None。
    paused：None（在播）/ 'conversation' / 'user'（两种暂停语义可区分）。
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
        # 固件格式 pos=13s：数字 + 单位后缀（沿用管线遥测字段风格）。周期行
        # 是整秒，解析口与锚点行共用（_parse_pos），这里收回整数。
        try:
            pos = int(_parse_pos(pos_raw))
        except ValueError:
            pos = None
    flags = m.group("flags") or ""
    if "PAUSED_USER" in flags:
        paused = "user"
    elif "PAUSED_CONV" in flags:
        paused = "conversation"
    else:
        paused = None
    return {
        "ring": int(m.group(1)),
        "ring_max": int(m.group(2)),
        "in_buf": int(m.group(3)),
        "read": int(m.group(4)),
        "pushed": int(m.group(5)),
        "fail": int(m.group(6)),
        "pos": pos,
        "paused": paused,
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


def parse_pause_line(text):
    """解析暂停锚点行（Music pause: kind=… pos=…）。未命中返回 None。"""
    m = PAUSE_RE.search(text)
    if not m:
        return None
    return {"kind": m.group("kind"), "pos": _parse_pos(m.group("pos"))}


def parse_resume_line(text):
    """解析续播锚点行（Music resume: mode=… at=…）。未命中返回 None。

    continue：at= 是接缝处的位点（暂停时冻结的那个），没有 from/margin。
    restart：at= 是请求的重起位点，from= 是暂停时的位点，margin= 是回退余量。
    """
    m = RESUME_RE.search(text)
    if not m:
        return None
    return {
        "mode": m.group("mode"),
        "pos": _parse_pos(m.group("pos")),
        "from_s": _parse_pos(m.group("from")),
        "margin_s": int(m.group("margin")) if m.group("margin") is not None else None,
    }


def parse_auto_resume_line(text):
    """解析自动续播锚点行（Music auto-resume: quiet=Ns）。未命中返回 None。"""
    m = AUTO_RESUME_RE.search(text)
    if not m:
        return None
    return {"quiet_s": int(m.group("quiet"))}


def _parse_pos(raw):
    """位点原始字段 → 数值（'live' / None 原样返回）。

    单一解析口：`pipe:` 行（整秒）与三条锚点行（一位小数）都经它。单位后缀
    只在这里 strip 一次，别各处再 spread 出一份 rstrip("s")。
    """
    if raw is None or raw == "live":
        return raw
    return float(raw.rstrip("s"))


def _is_num(value):
    """数字位点（int/float 都算；'live' / None 不算）。

    pipe: 行是整秒（int），锚点行是一位小数（float）——两处都算数字。
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _add_pause_kind_assertion(add, paused_events, pause_markers, live):
    """暂停种类断言（issue #4）：live 与非 live 逐字同一段，别抄两遍。

    取值来自两个来源（暂停期间的 pipe: 标记 + 暂停锚点自报的 kind），
    只允许 'user' / 'conversation' 两个值，且至少要出现一个。
    """
    kinds = sorted({e["parsed"]["paused"] for e in paused_events} |
                   {m["pause"]["kind"] for m in pause_markers})
    bad_kinds = [k for k in kinds if k not in ("user", "conversation")]
    label = "直播流暂停种类：" if live else "暂停种类："
    add("pause_kind_recorded", not bad_kinds and bool(kinds),
        label + ", ".join(kinds) + (f"（非法：{bad_kinds}）" if bad_kinds else ""))


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
                     offset_tol=DEFAULT_OFFSET_TOL, expect_restart=False,
                     expect_auto_resume=False):
    """对抓到的采样做断言。samples = [{'t': 抓到时刻, 'line': 原始行}, …]。

    返回 [AssertionResult, …]；全部 .ok 为 True 才算验收通过。

    按会话分段：起流锚点行（Music stream started）开一段，其后到下一个锚点
    之间的 pipe: 行都属于该段。起点与形态取自该段的锚点，CLI 参数只作兜底与
    核对——这样「位点是绝对值」「直播不记位点」不靠调用者记得传参。

    暂停/续播（issue #4）：暂停跨度的样本带 PAUSED_CONV / PAUSED_USER 标记，
    位点在那里必须冻结；续播锚点（Music resume: mode=continue|restart）标出
    接缝位置——continue 必须接在冻结位点 ±0.5s 内，restart 按锚点自报的
    margin 放行回退（≤ margin+0.5s）且不许前进超过 0.5s；用户暂停的跨度内
    不许出现 auto-resume。**暂停过的会话，推进率与挂钟偏差按净播放时长折算**
    ——否则一段正常录音只要含暂停，今天必然误报。
    """
    results = []

    def add(name, ok, detail):
        results.append(AssertionResult(name, bool(ok), detail))

    sessions = []  # [{'t', 'session', 'events': [...], 'markers': [...]}, …]
    orphan_events = []  # 锚点缺失时的兜底容器
    orphan_markers = []
    for s in samples:
        line = s["line"]
        session = parse_session_line(line)
        if session is not None:
            sessions.append({"t": s["t"], "session": session, "events": [],
                             "markers": []})
            continue
        pause = parse_pause_line(line)
        resume = parse_resume_line(line)
        auto_resume = parse_auto_resume_line(line)
        if pause is not None or resume is not None or auto_resume is not None:
            marker = {"t": s["t"], "line": line, "pause": pause, "resume": resume,
                      "auto_resume": auto_resume}
            if sessions:
                sessions[-1]["markers"].append(marker)
            else:
                orphan_markers.append(marker)
            continue
        parsed = parse_pipe_line(line)
        if parsed is None:
            continue
        event = {"t": s["t"], "parsed": parsed}
        if sessions:
            sessions[-1]["events"].append(event)
        else:
            orphan_events.append(event)
    if orphan_events or orphan_markers:
        # 没抓到锚点的抓取（旧固件或起播早于开抓）：整段当一次会话，起点靠 CLI。
        sessions.insert(0, {"t": None, "session": None, "events": orphan_events,
                            "markers": orphan_markers})

    events = [e for s in sessions for e in s["events"]]
    markers = [m for s in sessions for m in s["markers"]]
    add("enough_position_lines", len(events) >= min_lines,
        f"{len(events)} 行 pipe: 遥测（要求 ≥ {min_lines}）")

    anchored = [s for s in sessions if s["session"] is not None]
    add("session_anchor_seen", bool(anchored),
        f"{len(anchored)} 条起流锚点（Music stream started）"
        + ("" if anchored else "——请先开始抓取再触发播放"))

    is_live = live if live is not None else (
        anchored[0]["session"]["form"] == "live" if anchored else False)

    # ── 暂停 / 续播断言（两种内容形态都适用）────────────────────────
    # 直播流位点无意义（pos=live），冻结断言不适用——只核对暂停种类与续播路径。
    _add_pause_assertions(add, events, markers, expect_restart, expect_auto_resume,
                          live=is_live)

    if is_live:
        bad = [e for e in events if _is_num(e["parsed"]["pos"])]
        add("live_records_no_position", len(bad) == 0 and len(events) > 0,
            f"数字位点 {len(bad)} 处（直播流必须全部 pos=live）")
        return results

    # ── 有限内容：逐会话断言 ──────────────────────────────────────
    # 暂停的处置：暂停期间挂钟在走、位点不动。若把暂停跨度按在播算，一段
    # 正常的「播→暂停→继续」录音必然失败（推进率被拉低、挂钟偏差变负数）。
    # 故：跨过暂停的样本对不参与单调性；挂钟/推进率按净播放时长折算。
    monotonic_ok = True
    monotonic_detail = "同一会话内位点单调不减"
    below_start = []
    # 重启式续播的安全余量：位点回退 1–2s 是「宁可重复不跳词」的既定代价，
    # 由续播锚点（mode=restart）放行，其余任何回退仍是失败。
    restart_positions = [m["resume"]["pos"] for m in markers
                         if m["resume"] is not None and m["resume"]["mode"] == "restart"
                         and _is_num(m["resume"]["pos"])]
    for s in sessions:
        numeric = [e for e in s["events"] if _is_num(e["parsed"]["pos"])]
        seg_start = effective_start(s, start_s)
        for e in numeric:
            if e["parsed"]["pos"] < seg_start - 1:
                below_start.append((seg_start, e["parsed"]["pos"]))
        for a, b in zip(numeric, numeric[1:]):
            if a["parsed"]["paused"] is not None or b["parsed"]["paused"] is not None:
                continue  # 暂停跨度的接缝：由续播断言单独负责
            if b["parsed"]["pos"] < a["parsed"]["pos"]:
                if any(b["parsed"]["pos"] <= rp <= a["parsed"]["pos"]
                       for rp in restart_positions):
                    continue
                monotonic_ok = False
                monotonic_detail = (f"{a['parsed']['pos']}s → "
                                    f"{b['parsed']['pos']}s 出现回退")
    add("position_monotonic", monotonic_ok, monotonic_detail)

    # 起点核对：锚点行是设备自报口径，CLI 只是兜底。两者都在却不一致 =
    # 调用方传错了参数，断言会静默放行——单独报出来。
    if start_s is not None and anchored:
        mismatch = [s for s in anchored if s["session"]["start_s"] != start_s]
        add("cli_matches_anchor", not mismatch,
            f"CLI --start={start_s}s 与锚点 start={anchored[0]['session']['start_s']}s "
            + ("一致" if not mismatch else f"不一致（{len(mismatch)} 条锚点）"))

    if not any(_is_num(e["parsed"]["pos"]) for e in events):
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
    pause_intervals = _pause_intervals(events, markers)
    for s in sessions:
        numeric = [e for e in s["events"] if _is_num(e["parsed"]["pos"])]
        if s["t"] is None or len(numeric) < 2:
            continue
        seg_start = effective_start(s, start_s)
        # 净播放时刻：从会话锚点起算的挂钟，减去暂停区间。
        played_at = _net_playback_offsets(numeric, s["t"], pause_intervals)
        for (t, pos), net in zip(
                [(e["t"], e["parsed"]["pos"]) for e in numeric], played_at):
            offsets.append(pos - seg_start - net)
        net_elapsed = played_at[-1] - played_at[0]
        if net_elapsed > 0:
            # 重启式续播的 2s 安全余量是**既定代价**（宁可重复不跳词），
            # 不是停滞：把这段受控回退加回分子，否则一次重起流就会把推进率
            # 拉低到下限以下。
            rewound = sum(max(0, m["resume"]["from_s"] - m["resume"]["pos"])
                          for m in markers
                          if m["resume"] is not None and m["resume"]["mode"] == "restart"
                          and _is_num(m["resume"]["pos"])
                          and _is_num(m["resume"]["from_s"])
                          and m["t"] >= s["events"][0]["t"])
            advanced = numeric[-1]["parsed"]["pos"] - numeric[0]["parsed"]["pos"]
            advances.append((advanced + rewound) / net_elapsed)

    if not offsets:
        add("position_tracks_wall_clock", False,
            "可分段的数字位点样本不足（每个会话需 ≥ 2 行，且要有起流锚点）")
        add("position_advance_rate", False, "同上，无从计算推进率")
    else:
        q75 = statistics.quantiles(offsets, n=4)[1] if len(offsets) >= 4 else max(offsets)
        add("position_tracks_wall_clock", q75 <= offset_tol,
            f"位点-挂钟上四分位偏差 {q75:+.2f}s（容差 +{offset_tol}s，已扣暂停）；"
            f"恒定领先 ≈ +0.8s 即 ring 预读被计入位点")
        worst = min(advances) if advances else 0.0
        add("position_advance_rate", bool(advances) and worst >= 0.7,
            f"各会话净播放推进率最低 {worst:.2f}（下限 0.7，已扣暂停）")
    return results


def _pause_intervals(events, markers):
    """每次暂停的墙钟区间 [(start_t, end_t), …]。

    边界**优先取锚点行**（`Music pause:` / `Music resume:`）——它们标记的是
    暂停真正开始/结束的那一刻；位点是 2 秒一拍的遥测，用样本时刻当边界会
    系统性少算半拍到一拍，挂钟断言就被这点误差顶过容差。锚点缺失时退回
    第一/最后一个带暂停标记的样本。
    """
    intervals = []
    for start, end in _pause_spans(events):
        t0 = events[start]["t"]
        t1 = events[end]["t"]
        pause_marks = [m["t"] for m in markers
                       if m["pause"] is not None and m["t"] <= t0]
        if pause_marks:
            t0 = max(pause_marks)
        resume_marks = [m["t"] for m in markers
                        if m["resume"] is not None and m["t"] >= t1]
        if resume_marks:
            t1 = min(resume_marks)
        intervals.append((t0, t1))
    return intervals


def _net_playback_offsets(numeric_events, session_t=None, pause_intervals=None):
    """把会话内的样本时刻折算成「净播放时刻」（扣掉暂停区间）。

    起点是会话锚点行（没有锚点时退回第一个位点样本）：位点-挂钟断言的口径
    是「位点相对起点走了多少」对「锚点以来真正在播多久」。
    """
    if not numeric_events:
        return []
    origin = numeric_events[0]["t"] if session_t is None else session_t
    intervals = pause_intervals or []
    played = []
    for event in numeric_events:
        t = event["t"]
        paused = sum(max(0.0, min(t, end) - max(origin, start))
                     for start, end in intervals)
        played.append(max(0.0, t - origin - paused))
    return played


def _add_pause_assertions(add, events, markers, expect_restart, expect_auto_resume,
                          live=False):
    """暂停/续播断言组（issue #4）。

    只在抓取里真的出现暂停/续播痕迹时断言——没暂停的抓取（issue #3 那套
    「播到哪了」核验）不该因为「没暂停」而报失败。要强制要求覆盖暂停，用
    `--expect-restart` / `--expect-auto-resume`，或直接把抓取窗口覆盖住暂停。
    """
    pause_markers = [m for m in markers if m["pause"] is not None]
    resume_markers = [m for m in markers if m["resume"] is not None]
    auto_markers = [m for m in markers if m["auto_resume"] is not None]
    paused_events = [e for e in events if e["parsed"]["paused"] is not None]

    if paused_events or pause_markers:
        # 暂停「段」= 连续的暂停样本（可能多次暂停）。位点冻结与续播接缝都在
        # 每一段内部比对：整段抓取里的第一段与第二段之间位点当然会变（中间
        # 播过一段），拿全局首尾比就把正常抓取判成失败。
        spans = _pause_spans(events)
        # 两种暂停语义可区分：暂停痕迹带种类，且是允许的两个值之一。
        _add_pause_kind_assertion(add, paused_events, pause_markers, live)
        if not live:
            # 1) 位点冻结：每一段暂停内的位点都不再增长。两个来源都要核——进
            #    暂停时的锚点（Music pause: pos=…，紧挨着这一段之前打出）与
            #    暂停期间的 pipe: 行，它们必须停在同一个值上，这也就是「暂停后
            #    位点不再增长」的直接证据。
            growths = []
            frozen_values = []
            for start, end in spans:
                seg = [e["parsed"]["pos"] for e in events[start:end + 1]
                       if _is_num(e["parsed"]["pos"])]
                if not seg:
                    continue
                span_t0 = events[start]["t"]
                # 紧邻这段暂停之前的锚点（同一拍或最多一条遥测之内）。
                anchors = [m["pause"]["pos"] for m in pause_markers
                           if span_t0 - 3.0 <= m["t"] <= span_t0
                           and _is_num(m["pause"]["pos"])]
                frozen_values.append(seg[0])
                for a, b in zip(seg, seg[1:]):
                    if b > a:
                        growths.append((a, b))
                if anchors and seg[0] > anchors[-1] + RESUME_SEAM_TOL:
                    # 锚点自报冻在更靠前的位点，而暂停期间的 pipe 行却报得更大
                    # ——暂停那一刻之后位点还在涨。
                    growths.append((anchors[-1], seg[0]))
            if not spans and not pause_markers:
                add("pause_freezes_position", False,
                    "暂停样本没有数字位点（暂停时空闲？确认抓取窗口覆盖住暂停）")
            elif not frozen_values:
                add("pause_freezes_position", False,
                    "暂停跨度内没有数字位点（直播流？确认抓取窗口覆盖住暂停）")
            else:
                detail = (f"{len(frozen_values)} 段暂停，位点分别冻结在 "
                          + ", ".join(_fmt_secs(v) for v in frozen_values))
                add("pause_freezes_position", not growths,
                    detail if not growths else
                    "暂停期间位点仍在增长：" + ", ".join(
                        f"{_fmt_secs(a)}→{_fmt_secs(b)}" for a, b in growths[:3]))

        # 用户暂停绝不自动续（spec：「说『暂停』后随便聊一句，音乐不自动响」）：
        # 任何一个 user 种类的暂停跨度内出现 Music auto-resume: 标记即失败。
        offending = _user_pause_auto_resume_hits(spans, events, pause_markers,
                                                 resume_markers, auto_markers)
        if _has_user_pause(events, pause_markers):
            add("user_pause_never_auto_resumes", not offending,
                "用户暂停跨度内无 Music auto-resume: 标记"
                if not offending else
                f"用户暂停期间音乐自动响了 {len(offending)} 次"
                + ("（自动续播只允许发生在会话性暂停跨度内）"))

    if resume_markers:
        # 3) 续播接在原处：continue 必须接在**上一段**暂停冻结的位点上
        #    （±0.5s，spec 的验收缝），restart 的回退按锚点自报的 margin 判
        #    （≤ margin+0.5s）且不前进超过 0.5s。每段暂停各配一次续播，按序配对。
        spans = _pause_spans(events)
        resume_frozen = []
        for m in resume_markers:
            frozen = None
            for start, end in spans:
                if events[start]["t"] <= m["t"]:
                    frozen = events[start]["parsed"]["pos"]
                else:
                    break
            for pm in pause_markers:
                if pm["t"] <= m["t"] and _is_num(pm["pause"]["pos"]):
                    frozen = pm["pause"]["pos"]
            resume_frozen.append(frozen)
        bad = []
        for m, frozen in zip(resume_markers, resume_frozen):
            r = m["resume"]
            if r["pos"] == "live" or r["from_s"] == "live":
                continue  # 直播流位点无意义：重连即正确
            if r["mode"] == "continue":
                # continue 没有 from=（它没换流）：接缝就是 at= 本身。核对它
                # 是否就是暂停冻结的那个位点——跳了就说明续播没接在原处。
                if frozen is None or not _is_num(r["pos"]):
                    continue
                if abs(r["pos"] - frozen) > RESUME_SEAM_TOL:
                    bad.append(f"continue at={_fmt_secs(r['pos'])} 但暂停冻在 "
                               f"{_fmt_secs(frozen)}")
            else:
                if r["from_s"] is None or not _is_num(r["pos"]):
                    continue
                # 回退不超过锚点自报的 margin + 容差；前进不超过容差。
                margin = (float(r["margin_s"]) if r["margin_s"] is not None
                          else RESTART_MARGIN_FALLBACK)
                delta = r["pos"] - r["from_s"]
                if not (-(margin + RESUME_SEAM_TOL) <= delta <= RESUME_SEAM_TOL):
                    bad.append(f"restart at={_fmt_secs(r['pos'])} from="
                               f"{_fmt_secs(r['from_s'])} margin={margin:g}s "
                               f"（Δ={delta:+.1f}s 越界）")
        modes = ", ".join(f"{m['resume']['mode']}@at={_fmt_secs(m['resume']['pos'])}"
                          for m in resume_markers)
        add("resume_continues_position", not bad,
            f"续播接缝：{modes}" if not bad else "接缝越界：" + "; ".join(bad))
    elif paused_events:
        add("resume_continues_position", False,
            "有暂停但没有续播锚点（Music resume: mode=…）——音乐没接上？")

    # 4) 可选：本次抓取要覆盖「原连接不可用 → 重新起流」这条分支。
    if expect_restart:
        restarts = [m for m in resume_markers if m["resume"]["mode"] == "restart"]
        add("restart_path_seen", bool(restarts),
            f"重启式续播 {len(restarts)} 次（mode=restart）"
            if restarts else
            "未见 mode=restart——暂停要够久（超过上游 proxy_read_timeout）才会走这条")

    # 5) 可选：静默数秒确实触发了自动续播。
    if expect_auto_resume:
        add("auto_resume_fired", bool(auto_markers),
            f"自动续播 {len(auto_markers)} 次"
            + (f"（quiet={auto_markers[0]['auto_resume']['quiet_s']}s）"
               if auto_markers else "——会话性暂停后静默数秒未触发"))


def _pause_spans(events):
    """暂停跨度（连续带暂停标记的样本段）的 [start, end] 下标列表。

    位点冻结与续播接缝都在**每一段内部**比对：一次抓取可能有多次暂停，
    段与段之间位点当然会变（中间播了一段），拿全局首尾比会把正常抓取判成
    失败。
    """
    spans = []
    start = None
    for idx, event in enumerate(events):
        paused = event["parsed"]["paused"] is not None
        if paused and start is None:
            start = idx
        elif not paused and start is not None:
            spans.append((start, idx - 1))
            start = None
    if start is not None:
        spans.append((start, len(events) - 1))
    return spans


def _has_user_pause(events, pause_markers):
    """抓取里是否出现过**用户**种类的暂停（pipe: 标记或暂停锚点任一）。"""
    return any(e["parsed"]["paused"] == "user" for e in events) or any(
        m["pause"]["kind"] == "user" for m in pause_markers)


def _user_pause_auto_resume_hits(spans, events, pause_markers, resume_markers,
                                 auto_markers):
    """落在**用户**暂停窗口内的自动续播标记 list（非空即失败）。

    窗口按「暂停锚点切开的子段」算，不把整段暂停当一种语义——两种暂停可以
    紧挨着（用户暂停后立刻被唤醒 → 会话性暂停），中间没有在播样本，位点跨度
    会把它们并成一段；拿整段按 user 判，会话性那半段的合法自动续播就被误伤。

    窗口有两类来源：
      - 锚点：每条 `Music pause: kind=user` 到**下一个暂停锚点 / 第一条续播
        锚点 / +8s**（三者取最近）——覆盖「用户暂停短于一拍遥测、没有暂停
        样本」的情形，同时不越界吞掉后面那段别的语义的暂停；
      - 子段：连续带暂停标记的 pipe: 样本段按暂停锚点切分，子段语义取**它
        起点之前最近的暂停锚点**（锚点先打、随后才是暂停样本，真机就这样），
        没有锚点时取样本自己的标记。窗口终点延到该子段之后的第一条续播锚点
        （最多 +2.5s，略多于一拍遥测）。
    只认「有 user 痕迹」的子段：会话性暂停里的 auto-resume 是正常行为——用户
    的「暂停」之后随便聊一句，音乐不该自己响。用户暂停若被误降级成会话性
    （管道样本标成 PAUSED_CONV），锚点那份 kind=user 仍会把这段判成用户窗口，
    bug 藏不住。
    """
    kAnchorTailS = 8.0   # 锚点后既无下一条锚点也无续播锚点时的兜底（> 静默阈值 5s）
    kSpanTailS = 2.5     # 子段后的尾窗（略多于一拍遥测）
    windows = []
    anchor_times = sorted(m["t"] for m in pause_markers)
    resume_times = sorted(r["t"] for r in resume_markers)
    for m in pause_markers:
        if m["pause"]["kind"] != "user":
            continue
        t0 = m["t"]
        later = [t for t in resume_times if t >= t0]
        later += [t for t in anchor_times if t > t0]
        t_end = min(later) if later else t0 + kAnchorTailS
        windows.append((t0, t_end))
    for t0, t1, kind in _pause_segments(spans, events, pause_markers):
        if kind != "user":
            continue
        later = [t for t in resume_times if t >= t1]
        t_end = min(t1 + kSpanTailS, min(later)) if later else t1 + kSpanTailS
        windows.append((t0, t_end))
    return [m for m in auto_markers
            if any(t0 <= m["t"] <= t1 for t0, t1 in windows)]


def _pause_segments(spans, events, pause_markers):
    """把暂停跨度按暂停锚点切成同语义子段：[(t0, t1, kind), …]。

    子段语义 = 起点之前最近的暂停锚点的 kind（kAnchorLookbackS 内），没有就取
    子段第一个样本的标记；两者都没有（既无锚点也无标记）时按 conversation 算
    ——「无证据」不该升级成用户暂停，那是会误报的方向。
    """
    kAnchorLookbackS = 3.0
    segments = []
    for start, end in spans:
        t_start = events[start]["t"]
        t_end = events[end]["t"]
        cuts = [m["t"] for m in pause_markers if t_start < m["t"] <= t_end]
        bounds = [t_start] + sorted(cuts) + [t_end]
        for idx in range(len(bounds) - 1):
            seg_t0, seg_t1 = bounds[idx], bounds[idx + 1]
            nearby = [m for m in pause_markers
                      if seg_t0 - kAnchorLookbackS <= m["t"] <= seg_t0 + 0.5]
            if nearby:
                kind = max(nearby, key=lambda m: m["t"])["pause"]["kind"]
            else:
                sample_kinds = {events[i]["parsed"]["paused"]
                                for i in range(start, end + 1)
                                if seg_t0 <= events[i]["t"] <= seg_t1}
                kind = ("user" if "user" in sample_kinds
                        else "conversation")
            segments.append((seg_t0, seg_t1, kind))
    return segments


def _fmt_secs(value):
    """位点显示：int 按整秒（`73s`），float 按一位小数（`73.4s`），非数字原样。"""
    if not _is_num(value):
        return str(value)
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.1f}s"
    return f"{value:g}s"


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
                if ("pipe:" in text or "Music" in text or "music" in text
                        or "PAUSED" in text):
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
             "offset-tol": DEFAULT_OFFSET_TOL, "expect-restart": False,
             "expect-auto-resume": False}
    positional = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--assert":
            flags["assert"] = True
        elif a == "--live":
            flags["live"] = True
        elif a == "--expect-restart":
            # 本次抓取要求覆盖「原连接不可用 → 重新起流」这条分支
            # （暂停够久，超过上游 proxy_read_timeout 才会走到）。
            flags["expect-restart"] = True
        elif a == "--expect-auto-resume":
            # 本次抓取要求覆盖「会话性暂停 → 静默数秒自动续播」。
            flags["expect-auto-resume"] = True
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
          "pushed=… fail=… pos=…(位点) PAUSED_CONV/PAUSED_USER(暂停种类)")

    with open("/tmp/serial_full.log", "ab") as f:
        f.write(f"\n==== capture {time.strftime('%Y-%m-%d %H:%M:%S')} ====\n".encode())
        samples, key_lines = capture_with_log(port, duration, f)

    if not flags["assert"]:
        print(f"\n==== 完成，共 {len(key_lines)} 行关键日志，全量见 /tmp/serial_full.log ====")
        return 0

    print(f"\n==== 位点断言（start={flags['start']}s live={flags['live']}）====")
    results = evaluate_capture(
        samples, start_s=flags["start"], live=flags["live"],
        min_lines=flags["min-lines"], offset_tol=flags["offset-tol"],
        expect_restart=flags["expect-restart"],
        expect_auto_resume=flags["expect-auto-resume"])
    ok = True
    for r in results:
        mark = "✅" if r.ok else "❌"
        print(f"  {mark} {r.name}: {r.detail}")
        ok = ok and r.ok
    print(f"==== 断言{'全部通过' if ok else '未通过'} ====")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
