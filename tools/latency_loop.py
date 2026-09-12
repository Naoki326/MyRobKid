#!/usr/bin/env python3
"""对话延迟反馈回路(模拟设备:WS握手→发Opus音频→测各段延迟)

用法:
  server/.venv/bin/python tools/latency_loop.py [问题] [--realtime]
  server/.venv/bin/python tools/latency_loop.py 这歌谁唱的 --music started \
      --server-log /tmp/xiaozhi_server.log

--music 扩展（issue #9 的验收缝二）：假设备在说话**之前**先经既有 MCP 消息通路
推一条 ``music.session`` JSON-RPC 通知（无 id，不期待响应），让服务端知道「现在
在放什么」；然后照常说话，让模型在一次真实调用里读到注入的音乐状态。

断言的对象是**注入是否发生**，不是模型的措辞（模型输出不确定，不能当回归判据）
——服务端在注入路径上打一行锚点（``Music inject: injected=yes|no state=… title='…' form=… pos=…``），
本脚本在 ``--server-log``（或环境变量 ``LOOP_SERVER_LOG``）指向的服务端日志里找它：
  - 找到且 ``injected=yes``、state 与推送的一致 → 注入真的进了送给模型的内容（绿）；
  - 一行都没有，或 ``injected=no``  → 事件没到 / 没注入 / 占位符没展开（红）。
这条判据抓的是「注入没生效」——脚本自造的数据不会让它变绿，因为绿的条件是
**服务端日志里出现了注入锚点**。

--music 后跟的状态（见 server/core/utils/music_session.py 的线协议）：
  started | paused | resumed | completed | interrupted | resume_failed | stopped |
  start_failed，外加 ``--music-live`` 走直播流（不带位点）、``--music-title`` /
  ``--music-author`` / ``--music-pos`` / ``--music-duration`` 覆盖默认字段。
"""
import asyncio, json, os, re, sys, subprocess, tempfile, time, wave
import websockets
import opuslib_next

WS_URL = os.environ.get("LOOP_WS", "ws://127.0.0.1:8002/xiaozhi/v1/")
TAG = "[LOOP]"

#: 服务端注入路径锚点（connection.log_music_injection）。与固件 `Music screen:`
#: 同族的行内风格：字段名/引号一致，串口/日志抓取脚本可以统一断言。
#:
#: `injected=` 是这条锚点的要害：它由服务端在**拼装完送给模型的那份提示之后**
#: 按「注入文本是否真出现在系统提示里」判定，所以 ``injected=yes`` 才是
#: 「音乐状态真的进了模型」的证据。只看「状态=playing」会漏掉占位符缺失、
#: 拼装回归、用户自定义模板这三种「读到了却没进去」的失效。
MUSIC_INJECT_RE = re.compile(
    r"Music inject:\s+injected=(?P<injected>\w+)\s+state=(?P<state>\w+)\s+"
    r"title='(?P<title>.*?)'\s+"
    r"author='(?P<author>.*?)'\s+form=(?P<form>\w+)\s+pos=(?P<pos>[\w.]+)")

#: 状态名 → 服务端权威字段的默认组合。与 music_session.py 的 STATE_* 同名。
_MUSIC_STATES = {
    "started": {"event": "started", "state": "playing"},
    "paused": {"event": "paused", "state": "paused_user"},
    "resumed": {"event": "resumed", "state": "playing"},
    "completed": {"event": "completed", "state": "completed"},
    "interrupted": {"event": "interrupted", "state": "interrupted"},
    "resume_failed": {"event": "resume_failed", "state": "resume_failed"},
    "stopped": {"event": "stopped", "state": "stopped"},
    "start_failed": {"event": "start_failed", "state": "start_failed"},
}


def music_notification(state="started", *, title="晴天", author="周杰伦", live=False,
                       position_s=None, duration_s=None):
    """构造一条 ``music.session`` 通知（设备 → 服务端，无 id = 不期待响应）。"""
    spec = _MUSIC_STATES.get(state)
    if spec is None:
        raise ValueError(f"未知音乐状态: {state}（可选 {', '.join(_MUSIC_STATES)}）")
    form = "live" if live else "finite"
    params = {"event": spec["event"], "state": spec["state"], "title": title,
              "author": author, "form": form}
    # 直播流不带位点/总量：带上就是撒谎，服务端也会主动丢（见 music_session.py）。
    if not live:
        if position_s is not None:
            params["position_s"] = position_s
        if duration_s is not None:
            params["duration_s"] = duration_s
    return {"jsonrpc": "2.0", "method": "music.session", "params": params}


#: 历史写入锚点（mcp_handler._log_music_history，issue #10）。与上面那条同族：
#: 字段名/引号一致，日志抓取脚本可统一断言。
#:
#: 为什么历史也需要一条锚点：历史在服务端进程内，假设备客户端看不见。若只靠
#: 「日志里没看到条目」判「暂停没写历史」，那就是缺席而不是证据——缺席区分不出
#: 「没写」与「没到」。``skipped=`` 把原因写出来，两件事才分得开。
MUSIC_HISTORY_RE = re.compile(
    r"Music history:\s+written=(?P<written>\w+)\s+event=(?P<event>\S+)\s+"
    r"title='(?P<title>.*?)'\s+"
    r"author='(?P<author>.*?)'(?:\s+skipped=(?P<skipped>\S+))?")

#: 写进历史的**曲目级事件**集合（issue #10）。与 music_session.TRACK_LEVEL_EVENTS
#: 同名同义：开始播放 / 换歌 / 播完 / 中断 / 续播失败（「换歌」就是一次新的
#: ``started``）。暂停与继续**不写**，但仍经通道抵达服务端。
_TRACK_LEVEL_STATES = frozenset({"started", "completed", "interrupted",
                                 "resume_failed"})

#: 反向集合：通道承载但**不**写历史的状态（本票要防的就是它们被误写进去）。
_NOT_TRACK_LEVEL_STATES = frozenset({"paused", "resumed", "stopped",
                                     "start_failed"})


async def push_music_notification(ws, payload):
    """经既有 MCP 消息通路发通知（``protocol.cc::SendMcpMessage`` 的线上形状）。"""
    await ws.send(json.dumps({"session_id": "debug", "type": "mcp",
                              "payload": payload}))


def assert_music_injected(pushed, log_path):
    """在服务端日志里找注入锚点：找不到 = 注入没生效（验收缝的真判据）。"""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            log = f.read()
    except OSError as e:
        print(f"{TAG} !! 读不到服务端日志 {log_path}: {e}")
        return False
    matches = list(MUSIC_INJECT_RE.finditer(log))
    if not matches:
        print(f"{TAG} !! 注入锚点一条都没有：事件没到 / 没注入 / 占位符没展开")
        return False
    last = matches[-1].groupdict()
    if last["injected"] != "yes":
        # 状态读到了、但**没进**送给模型的提示（占位符缺失/拼装回归/自定义模板）。
        # 这正是 issue #9 要防的「看起来做了、实际一半失效」。
        print(f"{TAG} !! 注入锚点报 injected={last['injected']}："
              f"音乐状态没进送给模型的内容（占位符没展开？）")
        return False
    expected_state = pushed["params"]["state"]
    if last["state"] != expected_state:
        print(f"{TAG} !! 注入状态是 {last['state']}，推送的是 {expected_state}")
        return False
    if pushed["params"]["title"] and last["title"] != pushed["params"]["title"]:
        print(f"{TAG} !! 注入曲目是 {last['title']!r}，推送的是 "
              f"{pushed['params']['title']!r}")
        return False
    if pushed["params"]["form"] == "live" and last["pos"] != "none":
        print(f"{TAG} !! 直播流注入带了位点 {last['pos']}（不该有）")
        return False
    print(f"{TAG} 注入已生效: state={last['state']} title='{last['title']}' "
          f"author='{last['author']}' form={last['form']} pos={last['pos']}")
    return True


def assert_music_history(pushed_list, log_path):
    """在服务端日志里找历史锚点：曲目级事件写了、暂停/继续一条都没写（issue #10）。

    判据不看「日志里有没有条目」（那是缺席），而看锚点自报的 ``written=`` 与
    两个 ``skipped=`` 值。两条判别力：

      - **暂停被误写进历史**：推 ``paused``/``resumed`` 后期望
        ``written=no skipped=not_track_level``；若实现把它们也写了，这里会看到
        ``written=yes``，判定失败。
      - **重复推送产生重复条目**：同一事件推两次，第二次必须是
        ``written=no skipped=duplicate``（幂等）。

    ``pushed_list`` 是**按顺序**推出去的通知列表（每轮的 ``--music`` 追加一项）。
    返回 True = 每个事件都留下了符合预期的证据。
    """
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            log = f.read()
    except OSError as e:
        print(f"{TAG} !! 读不到服务端日志 {log_path}: {e}")
        return False
    anchors = [m.groupdict() for m in MUSIC_HISTORY_RE.finditer(log)]
    if not anchors:
        print(f"{TAG} !! 历史锚点一条都没有：历史写入路径根本没跑（没到 / 没写）")
        return False

    ok = True
    for pushed in pushed_list:
        event = pushed["params"]["event"]
        title = pushed["params"].get("title")
        # 同一事件的所有锚点里任一条匹配就算这个事件有证据（推两次时第一条
        # 是 written=yes、第二条是 written=no skipped=duplicate，都在）。
        candidates = [a for a in anchors if a["event"] == event
                      and (not title or a["title"] == title)]
        if not candidates:
            print(f"{TAG} !! 事件 {event} title={title!r} 没有任何历史锚点")
            ok = False
            continue
        if event in _TRACK_LEVEL_STATES:
            written = [a for a in candidates if a["written"] == "yes"]
            if not written:
                print(f"{TAG} !! 曲目级事件 {event} 没写进历史（期望 written=yes）")
                ok = False
            else:
                print(f"{TAG} 历史已写入: event={event} "
                      f"title='{written[0]['title']}'")
        elif event in _NOT_TRACK_LEVEL_STATES:
            # 本票的核心断言：这些**不该**写历史。出现 written=yes 就是防线破了。
            bad = [a for a in candidates if a["written"] == "yes"]
            if bad:
                print(f"{TAG} !! {event} 被写进了历史（应当 skipped=not_track_level）"
                      f"—— 历史会被每轮的暂停/继续挤爆")
                ok = False
            else:
                print(f"{TAG} 未写历史（符合预期）: event={event} "
                      f"skipped={candidates[-1]['skipped']}")
        else:
            # 两个集合都不在 → 脚本与实现已经分岔（枚举名字改了漏改一边）。
            print(f"{TAG} !! 未知事件 {event!r}：不在曲目级集合也不在通道集合"
                  f"—— 本脚本的枚举与服务端已分岔")
            ok = False

    # 幂等：同一事件推两次时，第二次必须带 skipped=duplicate。
    seen = set()
    for pushed in pushed_list:
        key = (pushed["params"]["event"], pushed["params"].get("title"))
        if key in seen:
            dupes = [a for a in anchors if a["event"] == key[0]
                     and a["skipped"] == "duplicate"]
            if not dupes:
                print(f"{TAG} !! 重复推送的 {key[0]} 没有 skipped=duplicate 锚点"
                      f"—— 重复事件可能产生了重复历史条目")
                ok = False
        seen.add(key)
    return ok


#: 需要跟一个值的选项（解析时要把值一并从位置参数里剔掉，否则 ``--server-log
#: /dev/null`` 会把 ``/dev/null`` 当成要问的问题）。
_OPTIONS_WITH_VALUE = ("--music", "--music-title", "--music-author", "--music-pos",
                      "--music-duration", "--server-log")


def parse_cli(argv):
    """拆出位置参数（要问的话）与音乐推送选项。纯函数，方便测试。

    返回 ``(text, music, log_path)``；``music`` 为 None 表示不推送。
    """
    options = {}
    positional = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in _OPTIONS_WITH_VALUE:
            options[token] = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
            continue
        if token.startswith("--"):
            i += 1
            continue
        positional.append(token)
        i += 1

    music = None
    if options.get("--music") is not None:
        live = "--music-live" in argv
        # 逗号分隔 = 按顺序推多条（验收缝需要 「started → paused → resumed」
        # 这样的序列来断言「暂停没写历史」）。
        states = [s.strip() for s in options["--music"].split(",") if s.strip()]
        titles = [t.strip() for t in (options.get("--music-title") or "").split(",")]
        music = [music_notification(
            state,
            title=(titles[i] if i < len(titles) and titles[i] else "晴天"),
            author=options.get("--music-author") or "周杰伦",
            live=live,
            position_s=None if live else int(options.get("--music-pos") or 0),
            duration_s=None if live else int(options.get("--music-duration") or 269),
        ) for i, state in enumerate(states)]
    log_path = options.get("--server-log") or os.environ.get("LOOP_SERVER_LOG")
    return (positional[0] if positional else "你好呀"), music, log_path


def pcm_from_say(text):
    wav_path = tempfile.mktemp(suffix=".wav")
    subprocess.run(["say", "-o", wav_path, "--data-format=LEI16@16000", "-v", "Tingting", text], check=True)
    with wave.open(wav_path, "rb") as w:
        pcm = w.readframes(w.getnframes())
    os.unlink(wav_path)
    return pcm

async def run(text, realtime=False, tail_silence_s=1.8, music=None):
    pcm = pcm_from_say(text)
    pcm += b"\x00\x00" * int(16000 * tail_silence_s)
    enc = opuslib_next.Encoder(16000, 1, opuslib_next.APPLICATION_AUDIO)
    fb = 960 * 2
    frames = [pcm[i:i+fb] for i in range(0, len(pcm), fb)]
    if len(frames[-1]) < fb:
        frames[-1] += b"\x00\x00" * (fb - len(frames[-1]))
    hello = {"type":"hello","version":1,"features":{"mcp":True},"transport":"websocket",
             "audio_params":{"format":"opus","sample_rate":16000,"channels":1,"frame_duration":60}}
    t0=t_voice_end=t_stt=t_first=None; stt_text=None
    async with websockets.connect(WS_URL, additional_headers={
        "device-id":"3c:dc:75:fe:81:44","client-id":"b64cb7dd-136b-4339-a398-06454995036a"},
        max_size=None, compression=None) as ws:
        await ws.send(json.dumps(hello))
        while True:
            if json.loads(await asyncio.wait_for(ws.recv(), 10)).get("type")=="hello": break
        # 说话之前先推音乐状态：模型这一次调用就该读到它（注入是逐轮现取的）。
        # 可以是一串（按顺序推）：历史缝需要「started → paused → resumed」这类
        # 序列才能断言「暂停没写历史」。
        for note in (music or []):
            await push_music_notification(ws, note)
            print(f"{TAG} 已推送 music.session: event={note['params']['event']} "
                  f"state={note['params']['state']} "
                  f"title={note['params']['title']!r}")
        async def reader():
            nonlocal t_stt, t_first, stt_text
            while True:
                m = await ws.recv()
                if isinstance(m, bytes):
                    if t_first is None: t_first = time.perf_counter()
                else:
                    d = json.loads(m)
                    if d.get("type")=="stt":
                        t_stt = time.perf_counter(); stt_text = d.get("text")
                        print(f"{TAG} STT: {stt_text!r}")
                    if d.get("type")=="tts" and d.get("state")=="stop":
                        import __main__; __main__.g_stop = time.perf_counter()
        task = asyncio.create_task(reader())
        await ws.send(json.dumps({"session_id":"debug","type":"listen","state":"start","mode":"auto"}))
        await asyncio.sleep(0.3)
        t0 = time.perf_counter()
        n_voice = len(frames) - int(tail_silence_s/0.06)
        for i, f in enumerate(frames):
            await ws.send(enc.encode(f, 960))
            if i == n_voice-1: t_voice_end = time.perf_counter()
            if realtime: await asyncio.sleep(0.06)
        global g_stop; g_stop = None
        deadline = time.perf_counter() + 90
        while time.perf_counter() < deadline:
            await asyncio.sleep(0.1)
            if g_stop and time.perf_counter() > g_stop + 1.0: break
        task.cancel()
    r = lambda a,b: f"{(b-a)*1000:.0f}ms" if a and b else "N/A"
    print(f"{TAG} ===== {text!r} =====")
    print(f"{TAG} 说完话->STT      : {r(t_voice_end,t_stt)}")
    print(f"{TAG} 说完话->首包音频 : {r(t_voice_end,t_first)}")
    if t_first is None: print(f"{TAG} !! 未收到回复音频"); sys.exit(1)


def _arg_value(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


if __name__ == "__main__":
    argv = sys.argv[1:]
    text, music, log_path = parse_cli(argv)
    asyncio.run(run(text, realtime="--realtime" in argv, music=music))
    if music:
        if not log_path:
            print(f"{TAG} !! 未给 --server-log/LOOP_SERVER_LOG，无法断言注入/历史是否生效")
            sys.exit(1)
        if not assert_music_injected(music[0], log_path):
            sys.exit(1)
        if "--assert-history" in argv and not assert_music_history(music, log_path):
            sys.exit(1)
