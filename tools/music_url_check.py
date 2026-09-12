#!/usr/bin/env python3
"""音乐播放地址自检 —— 跑通「搜索 → play_url → 代理取流」，确认设备拿到的 URL 真能播。

为什么要有这个回路：设备侧 play_music 是「假成功」——先返回 true 再去连流，
地址错了日志照样显示成功，只是没声音；而播不出音乐时机器人会停在音乐模式
（唤醒词已停），用户看到的是「说开始播放，机器人就不动了」。
所以判断音乐链路死活不能看日志，要看 URL 能不能真取到音频。

2026-09-11 事故：Mac 走 DHCP，IP 从 192.168.18.172 漂到 .166，而
music_mcp.py 里写死了旧 IP，设备连不上 → 全链路无声。本工具即是该事故的
回归判据：地址一旦退回 IP 字面量，或主机不可达，立刻变红。

2026-09-12 扩展（issue #2）：play_url 开始携带内容属性（title/author/duration/
form），代理支持起点定位（ss，输入定位）与裁剪时长（t）。本工具随之断言：
  - play_url 可解析出标题、时长与内容形态（finite）；直播流标 live 且不带时长；
  - 起点 60s + 裁剪 10s 的代理产出是约 10s 的音频（64kbps ≈ 80KB，量级可辨）；
  - 非法起点被代理明确拒绝（400），不能是 200 空流。
时长语义的精确断言（±3s 容差）在 plugins/music-mcp/tests/test_transcode_proxy.py，
本工具只做线上链路的量级验证。

用法: server/.venv/bin/python tools/music_url_check.py [关键词] [--radio]
（关键词默认「巴赫」；--radio 改测电台直播流；被测进程、路径、环境全部取自
  data/.mcp_server_settings.json，即测的就是线上那条启动命令，不另存一份路径知识。）
"""
import asyncio, json, os, sys, urllib.error, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.path.join(ROOT, "server", "data", ".mcp_server_settings.json")
ARGS = [a for a in sys.argv[1:] if a != "--radio"]
RADIO = "--radio" in sys.argv
KEYWORD = ARGS[0] if ARGS else "巴赫"
TOOL = "search_radio" if RADIO else "search_song"
TAG = "[MUSIC-CHECK]"

# 起点 60s + 裁剪 10s 的产出：64kbps ≈ 80KB。带宽容差放宽到 5s..40s 的当量，
# 只判量级（定位真的生效/没有被 200 空流糊弄），精确 ±3s 断言在契约测试里。
SEEK_BYTES_MIN = 40 * 1024
SEEK_BYTES_MAX = 320 * 1024


def launch_spec():
    """从运行时配置取启动命令；取不到则退回仓库内的脚本 + 当前解释器。"""
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            cfg = json.load(f)["mcpServers"]["xiaozhi_music"]
        env = {**os.environ, **cfg.get("env", {})}
        return cfg["command"], cfg.get("args", []), env
    except Exception as e:
        script = os.path.join(ROOT, "plugins", "music-mcp", "music_mcp.py")
        print(f"{TAG} 读不到 {SETTINGS}（{e}），改用 {script}")
        return sys.executable, [script], {**os.environ, "MUSIC_MCP_CONSUMER": "xiaozhi"}


def looks_like_ip(host: str) -> bool:
    return bool(host) and all(ch.isdigit() or ch == "." for ch in host)


def fetch(url: str, full_body: bool = False):
    """设备是直连：绕开宿主机 HTTP_PROXY，否则测的是代理不是设备视角。
    代理忽略 Range（恒 200 全流），所以默认读 64KB 即断开（电台无限流也安全）；
    full_body=True 读完整响应（用于带 t= 裁剪的有界产出）。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url)
    with opener.open(req, timeout=30) as resp:
        data = resp.read() if full_body else resp.read(65536)
        return resp.status, resp.headers.get("Content-Type", ""), len(data)


def expect_400(url: str):
    """代理对非法起点必须 400；其他结果（含 200 空流）都算失败。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(url), timeout=30) as resp:
            return f"未拒绝（HTTP {resp.status}，{len(resp.read())} 字节）"
    except urllib.error.HTTPError as e:
        return None if e.code == 400 else f"HTTP {e.code}（应为 400）"
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def check_metadata(url: str, radio: bool) -> bool:
    """play_url 内容属性断言（issue #2）：标题/时长/形态随地址下发。"""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    ok = True
    title = (q.get("title") or [""])[0]
    form = (q.get("form") or [""])[0]
    duration = (q.get("duration") or [""])[0]
    if not title:
        print(f"{TAG} ❌ play_url 缺 title（设备无从知道在放什么）")
        ok = False
    if radio:
        if form != "live":
            print(f"{TAG} ❌ 电台 play_url 的 form 应为 live，实为 {form!r}")
            ok = False
        if duration:
            print(f"{TAG} ❌ 直播流不该带时长（duration={duration}）")
            ok = False
        if ok:
            print(f"{TAG} ✅ 内容属性: title={urllib.parse.unquote(title)!r} "
                  f"form={form}（直播流无时长）")
    else:
        if form != "finite":
            print(f"{TAG} ❌ 点播 play_url 的 form 应为 finite，实为 {form!r}")
            ok = False
        if not duration or not duration.isdigit():
            print(f"{TAG} ❌ play_url 缺可用时长（duration={duration!r}）")
            ok = False
        else:
            print(f"{TAG} ✅ 内容属性: title={urllib.parse.unquote(title)!r} "
                  f"duration={duration}s form={form}")
    return ok


async def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    command, args, env = launch_spec()
    print(f"{TAG} 被测启动命令: {command} {' '.join(args)}")
    params = StdioServerParameters(command=command, args=args, env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            res = await session.call_tool(TOOL, {"keyword": KEYWORD})
    text = "\n".join(c.text for c in res.content if getattr(c, "text", None))

    line = next((l for l in text.splitlines() if l.startswith("play_url:")), None)
    if not line:
        print(f"{TAG} ❌ 搜索结果里没有 play_url：\n{text}")
        return 1
    url = line.split("play_url:", 1)[1].strip()
    host = urllib.parse.urlsplit(url).hostname or ""
    print(f"{TAG} play_url 主机: {host}")

    ok = True
    if looks_like_ip(host):
        print(f"{TAG} ❌ 主机是 IP 字面量（{host}）：Mac 走 DHCP，IP 漂移后设备即失联，"
              f"而固件不报错。应改用 mDNS 名。")
        ok = False
    try:
        status, ctype, size = fetch(url)
        print(f"{TAG} ✅ 取流 HTTP {status}  {ctype}  {size} 字节")
        if size < 1024 or "audio" not in ctype:
            print(f"{TAG} ❌ 不像音频流（Content-Type={ctype!r}, {size} 字节）")
            ok = False
    except Exception as e:
        print(f"{TAG} ❌ 取流失败: {type(e).__name__}: {e}")
        ok = False

    if not check_metadata(url, RADIO):
        ok = False

    if RADIO:
        # 直播流定位无意义：只验证不带起点的取流与形态标记，其余跳过。
        print(f"{TAG} ==== 结论: {'通过，设备可播' if ok else '失败，音乐链路是断的'} ====")
        return 0 if ok else 1

    # ── 起点语义（issue #2）：代理从指定位置转出 ────────────────────
    try:
        status, ctype, size = fetch(url + "&ss=60&t=10", full_body=True)
        print(f"{TAG} ✅ 起点 60s + 裁剪 10s: HTTP {status}  {ctype}  {size} 字节")
        if not (SEEK_BYTES_MIN <= size <= SEEK_BYTES_MAX) or "audio" not in ctype:
            print(f"{TAG} ❌ 定位产出量级不对（应为约 10s 的 64kbps ≈ 80KB）")
            ok = False
    except Exception as e:
        print(f"{TAG} ❌ 定位取流失败: {type(e).__name__}: {e}")
        ok = False

    verdict = expect_400(url + "&ss=abc")
    if verdict:
        print(f"{TAG} ❌ 非法起点未明确拒绝：{verdict}")
        ok = False
    else:
        print(f"{TAG} ✅ 非法起点（ss=abc）被代理拒绝（400）")

    print(f"{TAG} ==== 结论: {'通过，设备可播' if ok else '失败，音乐链路是断的'} ====")
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
