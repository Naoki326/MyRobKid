#!/usr/bin/env python3
"""音乐播放地址自检 —— 跑通「搜索 → play_url → 代理取流」，确认设备拿到的 URL 真能播。

为什么要有这个回路：设备侧 play_music 是「假成功」——先返回 true 再去连流，
地址错了日志照样显示成功，只是没声音；而播不出音乐时机器人会停在音乐模式
（唤醒词已停），用户看到的是「说开始播放，机器人就不动了」。
所以判断音乐链路死活不能看日志，要看 URL 能不能真取到音频。

2026-09-11 事故：Mac 走 DHCP，IP 从 192.168.18.172 漂到 .166，而
music_mcp.py 里写死了旧 IP，设备连不上 → 全链路无声。本工具即是该事故的
回归判据：地址一旦退回 IP 字面量，或主机不可达，立刻变红。

用法: server/.venv/bin/python tools/music_url_check.py [关键词]
（关键词默认「巴赫」；被测进程、路径、环境全部取自 data/.mcp_server_settings.json，
  即测的就是线上那条启动命令，不另存一份路径知识。）
"""
import asyncio, json, os, sys, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.path.join(ROOT, "server", "data", ".mcp_server_settings.json")
KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "巴赫"
TAG = "[MUSIC-CHECK]"


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


def fetch(url: str):
    """设备是直连：绕开宿主机 HTTP_PROXY，否则测的是代理不是设备视角。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"Range": "bytes=0-65535"})
    with opener.open(req, timeout=15) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), len(resp.read())


async def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    command, args, env = launch_spec()
    print(f"{TAG} 被测启动命令: {command} {' '.join(args)}")
    params = StdioServerParameters(command=command, args=args, env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            res = await session.call_tool("search_song", {"keyword": KEYWORD})
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

    print(f"{TAG} ==== 结论: {'通过，设备可播' if ok else '失败，音乐链路是断的'} ====")
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
