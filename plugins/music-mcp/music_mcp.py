#!/usr/bin/env python3
"""
统一音乐 MCP Server（Mac 助手 + 小智共用）

一个脚本，两个消费者，按环境变量 MUSIC_MCP_CONSUMER 决定工具集：
  MUSIC_MCP_CONSUMER=mac     → 全量工具：本机 ffplay 播放/队列/歌单 + 搜索 + 登录管理
                               （~/.hermes/bin/qqmusic_mcp.py 符号链接到这里，默认值）
  MUSIC_MCP_CONSUMER=xiaozhi → 精简工具：search_song / search_radio / get_lyrics
                               （xiaozhi-server 经 .mcp_server_settings.json 以 stdio 拉起）

共享部分：
  - 凭证 ~/.hermes/qqmusic_cred.json（三方共用，过期自动用 refresh_token 静默续期）
  - QQ 音乐搜索 / 播放 URL 解析（仅返回设备可播的 .mp3 直链）
  - radio-browser 电台搜索（仅 MP3 流）

对外地址全部从 PROXY_HOST 派生（见下方「音乐代理地址」），本文件只写一处主机名。
"""

import asyncio
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP
from qqmusic_api import Client, Credential

CONSUMER = os.environ.get("MUSIC_MCP_CONSUMER", "mac")


# ═══ 音乐代理地址：单一事实源 ═══════════════════════════════════
# 设备侧所有音乐/播客/电台 URL（play_url）与授权页提示都从 PROXY_HOST 派生，
# 全文件只此一处写主机名——改这一行，整个音乐链路跟着变。
#
# 为什么用 mDNS 名而不是 IP：宿主机 Mac 走 DHCP（12h 租约），IP 会漂。
# 2026-09-11「搜完歌说开始播放、机器人就不动了」就是地址漂移所致：设备拿到
# 写死的 192.168.18.172（此时已不属于本机，ARP incomplete）连不上，而固件
# play_music 是「假成功」——先返回 true 再去连流，日志看着成功却无声。
# 这与 ADR-0002「地址一律用 mDNS 名」是同一条纪律，音乐代理是第四处地址。
#
# 需要临时改地址（如 Mac 改名）时用环境变量覆盖，不必动代码：
#   MUSIC_PROXY_BASE=http://别的名字:8080/music/stream
PROXY_HOST = "chenMac-mini.local:8080"          # ← 只改这一行
DEFAULT_PROXY_BASE = f"http://{PROXY_HOST}/music/stream"
PROXY_BASE = os.environ.get("MUSIC_PROXY_BASE", DEFAULT_PROXY_BASE)
AUTH_PAGE_HINT = (
    f"请在任意设备打开 QQ 音乐授权页扫码：http://{PROXY_HOST}/apps/qqmusic/"
)


def _resolve_host_of(base: str) -> str:
    return urllib.parse.urlsplit(base).hostname or ""


def _looks_like_ip(host: str) -> bool:
    return bool(host) and all(ch.isdigit() or ch == "." for ch in host)


# 启动即把生效地址打到 stderr（launchd 收进 /tmp/xiaozhi_server_launchd.log）：
# 地址错了是一句日志就能看出来的事，不必再靠串口取证。
_host = _resolve_host_of(PROXY_BASE)
if _looks_like_ip(_host):
    print(
        f"[music-mcp] ⚠ 音乐代理地址是 IP 字面量（{_host}）：Mac 走 DHCP，"
        f"IP 漂移后设备会连不上且固件不报错，表现为「说开始播放却没声音」。"
        f"建议改用 mDNS 名 {PROXY_HOST.split(':')[0]}。",
        file=sys.stderr,
    )
print(f"[music-mcp] 音乐代理地址：{PROXY_BASE}", file=sys.stderr)

mcp = FastMCP("QQMusic" if CONSUMER == "mac" else "XiaoZhiMusic")

CRED_PATH = os.path.expanduser("~/.hermes/qqmusic_cred.json")

# ── radio-browser 轮询镜像 ────────────────────────────────────────
_RADIO_MIRRORS = [
    "https://de1.api.radio-browser.info",
    "https://nl1.api.radio-browser.info",
    "https://at1.api.radio-browser.info",
]


# ═══ 凭证管理（共享） ═════════════════════════════════════════════

def _load_credential() -> Credential | None:
    if not os.path.exists(CRED_PATH):
        return None
    try:
        with open(CRED_PATH) as f:
            return Credential(**json.load(f))
    except Exception:
        return None


def _save_credential(cred: Credential):
    with open(CRED_PATH, "w") as f:
        json.dump(cred.model_dump(mode="json"), f, ensure_ascii=False)


async def _get_client() -> Client:
    """musickey 仅 3 天有效（腾讯对非官方客户端的风控策略）。
    过期时先用 refresh_token 静默续期并落盘，失败才提示扫码。"""
    cred = _load_credential()
    if cred is None:
        raise RuntimeError(f"QQ 音乐未登录。{AUTH_PAGE_HINT}")
    if cred.is_expired():
        try:
            async with Client(credential=cred) as client:
                new_cred = await client.login.refresh_credential(cred)
            if not new_cred.is_expired():
                _save_credential(new_cred)
                cred = new_cred
        except Exception:
            pass  # refresh 链也失效则沿用旧凭证，由上层报错
    return Client(credential=cred)


# ═══ QQ 音乐核心（共享） ══════════════════════════════════════════

def _singer_str(song) -> str:
    if isinstance(song.singer, list) and song.singer:
        return song.singer[0].name
    if isinstance(song.singer, str):
        return song.singer
    return str(song.singer)


async def _resolve_play_urls(keyword: str, max_results: int = 3,
                             quality_prefix: str = None) -> list[dict]:
    """搜索歌曲并解析播放 URL。

    quality_prefix: 按文件前缀过滤音质，"M500"=128k mp3, "M800"=320k mp3。
    None 表示不过滤（Mac ffplay 什么都能播）。小智端应使用 M500/M800
    （设备固件仅支持 mp3 直链）。
    """
    from qqmusic_api.modules.song import SongFileInfo

    async with await _get_client() as client:
        result = await client.search.search_by_type(keyword, page=1, num=8)
        if not result.song:
            return []
        cdn = await client.song.get_cdn_dispatch()
        cdn_prefix = cdn.sip[0]

        songs: list[dict] = []
        for song in result.song:
            if len(songs) >= max_results:
                break
            try:
                urls = await client.song.get_song_urls([SongFileInfo(mid=song.mid)])
                for info in urls.data:
                    if not info.purl:
                        continue
                    if quality_prefix and not info.purl.startswith(quality_prefix):
                        continue
                    url = cdn_prefix + info.purl
                    ok = url.lower().endswith(".mp3") or ".mp3?" in url.lower() or quality_prefix
                    if ok or quality_prefix is None:
                        songs.append({"name": song.name, "singer": _singer_str(song),
                                      "url": url})
                        break
            except Exception:
                continue
        return songs


# ═══ 电台搜索（共享） ═════════════════════════════════════════════

def _search_radio_sync(keyword: str, limit: int) -> list[dict]:
    params = urllib.parse.urlencode({
        "name": keyword, "limit": str(limit * 3),
        "hidebroken": "true", "order": "votes", "reverse": "true",
    })
    last_err: Exception | None = None
    for mirror in _RADIO_MIRRORS:
        try:
            req = urllib.request.Request(
                f"{mirror}/json/stations/search?{params}",
                headers={"User-Agent": "xiaozhi-music-mcp/2.0"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                stations = json.load(resp)
            return [
                s for s in stations
                if (s.get("codec") or "").upper() == "MP3" and s.get("url_resolved")
            ][:limit]
        except Exception as e:
            last_err = e
    raise RuntimeError(f"电台服务不可用：{last_err}")


# ══════════════════════════════════════════════════════════════════
# 通用工具（mac + xiaozhi 都注册）
# ══════════════════════════════════════════════════════════════════

_PLAY_HINT = {
    "xiaozhi": (
        "拿到结果后，必须紧接着调用设备端工具 self.audio_speaker.play_music，"
        "并把 url 参数传为结果中的 play_url。URL 有时效性，应立即播放，不要缓存。"
    ),
    "mac": "返回的 play_url 可直接播放（ffplay/浏览器均可），或改用 play_song_locally 在本机播放。",
}


@mcp.tool()
async def search_song(keyword: str, quality: str = "M500") -> str:
    """搜索歌曲并获取可直接播放的 mp3 直链（推荐首选工具）。

    当用户想听某首歌/某个歌手时调用本工具。

    Args:
        keyword: 歌曲名/歌手名，如 "晴天 周杰伦"
        quality: 音质 "M500"=128k（默认，最稳）或 "M800"=320k
    """
    try:
        songs = await _resolve_play_urls(keyword, quality_prefix=quality)
    except RuntimeError as e:
        return f"搜索失败：{e}"
    except Exception as e:
        return f"搜索失败：{type(e).__name__}: {e}"

    if not songs:
        return (f"没有找到「{keyword}」的可播放歌曲"
                f"（可能无版权，或需要重新扫码：{AUTH_PAGE_HINT}）")

    best = songs[0]
    lines = [
        f"找到 {len(songs)} 首，推荐第 1 首：",
        f"歌曲: {best['name']} — {best['singer']}",
        f"play_url: {_ensure_playable(best['url'])}",
    ]
    if len(songs) > 1:
        others = "、".join(f"{s['name']}—{s['singer']}" for s in songs[1:])
        lines.append(f"备选: {others}")
    lines.append(_PLAY_HINT.get(CONSUMER, ""))
    return "\n".join(lines)


@mcp.tool()
async def search_radio(keyword: str, limit: int = 3) -> str:
    """搜索网络电台，获取可播放的电台流直链。

    当用户想听电台/广播/某种风格的音乐（如"来点爵士乐""放个新闻台"）时调用。
    keyword 用英文效果最好（jazz、classical、news、pop、lofi）。

    Args:
        keyword: 电台名称或风格关键词（英文），如 "jazz"
        limit: 返回数量，默认 3
    """
    try:
        stations = await asyncio.get_event_loop().run_in_executor(
            None, _search_radio_sync, keyword, limit
        )
    except Exception as e:
        return f"电台搜索失败：{e}"

    if not stations:
        return f"没有找到「{keyword}」相关的可播放电台（仅支持 MP3 流）。"

    lines = []
    for i, s in enumerate(stations, 1):
        info = f"{i}. {s['name']}"
        if s.get("bitrate"):
            info += f" [{s['bitrate']}kbps]"
        if s.get("country"):
            info += f" ({s['country']})"
        lines.append(info)
    best = stations[0]
    lines.append(f"play_url: {_ensure_playable(best['url_resolved'])}")
    lines.append(_PLAY_HINT.get(CONSUMER, ""))
    return "\n".join(lines)


@mcp.tool()
async def get_lyrics(keyword: str) -> str:
    """获取歌曲歌词。

    Args:
        keyword: 歌曲名（如 "晴天 周杰伦"）
    """
    async with await _get_client() as client:
        result = await client.search.search_by_type(keyword, page=1, num=1)
        if not result.song:
            return "未找到歌曲"
        lyric = await client.lyric.get_lyric(result.song[0].mid)
        if not lyric or not getattr(lyric, "lyric", None):
            return "暂无歌词"
        return lyric.lyric


# ═══ 播客（Apple iTunes Search API，官方免费免 key） ═════════════

# 设备固件（2.4.8）的 HTTP 栈不跟随 302，且设备外网连通性不可靠；
# 因此 play_url 一律经 Mac 上的 ffmpeg 转码代理（nginx /music/stream → 8777）
# 下发：代理负责跟随重定向与格式转码，设备只连局域网。
# 其余格式（m4a/m4s/aac…）同样依赖该代理转码。
# 地址（含为什么用 mDNS 名）见文件头部「音乐代理地址：单一事实源」。


def _ensure_playable(url: str, referer: str = None) -> str:
    # 统一走局域网转码代理：即使 .mp3 结尾也可能是 302 跳转（如 wavpub 播客）。
    u = f"{PROXY_BASE}?src={urllib.parse.quote(url, safe='')}"
    if referer:
        u += "&referer=" + urllib.parse.quote(referer, safe='')
    return u


# ── B 站音频（匿名 API + ffmpeg 转码代理） ────────────────────────

async def _bilibili_search(keyword: str, limit: int = 3) -> list[dict]:
    """搜 B 站视频，返回 [{title, up, duration_s, bvid}]。"""
    from bilibili_api import search as bili_search

    r = await bili_search.search(keyword)
    videos = next(
        (b["data"] for b in r.get("result", [])
         if b.get("result_type") == "video" and b.get("data")),
        [],
    )
    out = []
    for item in videos[:limit * 2]:
        title = item["title"].replace('<em class="keyword">', "").replace("</em>", "")
        dur = item.get("duration", "0:0")
        parts = [int(x) for x in dur.split(":") if x.isdigit()]
        duration_s = parts[-1] if parts else 0
        if len(parts) >= 2:
            duration_s += parts[-2] * 60
        if len(parts) >= 3:
            duration_s += parts[-3] * 3600
        out.append({"title": title, "up": item.get("author", ""),
                    "duration_s": duration_s, "bvid": item["bvid"]})
        if len(out) >= limit:
            break
    return out


async def _bilibili_audio_url(bvid: str) -> str:
    """取视频第 1 P 的最佳音频流 m4s 直链（匿名，约 64k-132k）。"""
    from bilibili_api.video import Video

    vid = Video(bvid=bvid)
    info = await vid.get_info()
    url = await vid.get_download_url(cid=info["pages"][0]["cid"])
    audios = url.get("dash", {}).get("audio", [])
    if not audios:
        raise RuntimeError("该视频无独立音频流")
    best = max(audios, key=lambda a: a.get("bandwidth", 0))
    return best["baseUrl"]


@mcp.tool()
async def search_bilibili(keyword: str) -> str:
    """搜索哔哩哔哩（B 站）视频的音频并获取可播放直链（自动取最佳音质）。

    B 站内容海量：相声、广播剧、白噪音、助眠、课程、播客切片等。
    返回的是经 Mac 转码后的 mp3 流，可直接交给设备播放。

    Args:
        keyword: 搜索词，如 "郭德纲 相声"、"雨声白噪音"、"三体广播剧"
    """
    try:
        videos = await _bilibili_search(keyword, 3)
    except Exception as e:
        return f"B 站搜索失败：{type(e).__name__}: {e}"

    if not videos:
        return f"B 站没有找到「{keyword}」相关内容。"

    def fmt_dur(s):
        return f"{s // 3600}小时{(s % 3600) // 60}分" if s >= 3600 else (f"{s // 60}分钟" if s >= 60 else f"{s}秒")

    lines = [f"找到 {len(videos)} 个："]
    for i, v in enumerate(videos, 1):
        lines.append(f"{i}. {v['title'][:38]}（UP: {v['up']}，{fmt_dur(v['duration_s'])}）")

    for v in videos:
        try:
            m4s = await _bilibili_audio_url(v["bvid"])
        except Exception:
            continue
        lines.append(f"播放：{v['title'][:38]}")
        lines.append("play_url: " + _ensure_playable(m4s, referer="https://www.bilibili.com"))
        lines.append(_PLAY_HINT.get(CONSUMER, ""))
        return "\n".join(lines)
    return "\n".join(lines) + "\n（以上内容暂时取不到音频流，可换个关键词）"


def _itunes_search_podcast(keyword: str, limit: int = 3) -> list[dict]:
    """iTunes 官方接口搜播客，返回 [{title, artist, feed, genre}]。
    中文关键词搜中国区（中文播客），英文关键词搜美国区（国际播客）。"""
    import urllib.request

    country = "CN" if any("\u4e00" <= c <= "\u9fff" for c in keyword) else "US"
    q = urllib.parse.quote(keyword)
    url = (f"https://itunes.apple.com/search?media=podcast&entity=podcast"
           f"&country=CN&limit={limit}&term={q}")
    req = urllib.request.Request(url, headers={"User-Agent": "xiaozhi-music-mcp/2.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    return [
        {
            "title": r.get("trackName", ""),
            "artist": r.get("artistName", ""),
            "feed": r.get("feedUrl", ""),
            "genre": r.get("primaryGenreName", ""),
        }
        for r in data.get("results", [])
        if r.get("feedUrl")
    ]


def _latest_episode(feed_url: str) -> dict | None:
    """拉取播客 RSS，返回最新一集 {title, url, pub_date}（mp3/m4a enclosure）。"""
    import re
    import xml.etree.ElementTree as ET

    req = urllib.request.Request(feed_url, headers={"User-Agent": "xiaozhi-music-mcp/2.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
    root = ET.fromstring(raw)
    # RSS 2.0；命名空间安全处理
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        enc = item.find("enclosure")
        url = enc.get("url", "") if enc is not None else ""
        # 去掉常见统计前缀，取真正的音频地址
        if url and not url.lower().endswith((".mp3", ".m4a", ".mp3?")):
            if ".mp3" not in url.lower() and ".m4a" not in url.lower():
                continue
        if url:
            return {
                "title": title,
                "url": url,
                "pub_date": (item.findtext("pubDate") or "").strip(),
            }
    return None


@mcp.tool()
async def search_podcast(keyword: str) -> str:
    """搜索播客并获取最新一集的音频直链（脱口秀、故事、相声、知识、儿童内容都有）。

    当用户想听播客/电台节目/讲故事时调用，如“来个播客”“放放故事FM”“讲个脱口秀”。

    Args:
        keyword: 播客名或内容关键词，如 "故事FM"、“脱口秀”、“相声”
    """
    try:
        podcasts = await asyncio.get_event_loop().run_in_executor(
            None, _itunes_search_podcast, keyword, 3
        )
    except Exception as e:
        return f"播客搜索失败：{e}"

    if not podcasts:
        return f"没有找到「{keyword}」相关的播客。"

    lines = [f"找到 {len(podcasts)} 个播客："]
    for i, p in enumerate(podcasts, 1):
        lines.append(f"{i}. {p['title']}（{p['artist']}）[{p['genre']}]")

    # 逐个尝试取最新一集，优先返回能直接播的
    for p in podcasts:
        try:
            ep = await asyncio.get_event_loop().run_in_executor(
                None, _latest_episode, p["feed"]
            )
        except Exception:
            ep = None
        if ep:
            lines.append(f"播放：{p['title']} — {ep['title']}")
            lines.append(f"play_url: {_ensure_playable(ep['url'])}")
            lines.append(_PLAY_HINT.get(CONSUMER, ""))
            return "\n".join(lines)
    return "\n".join(lines) + "\n（以上播客的最新一集音频暂时取不到，可换个关键词）"


# ══════════════════════════════════════════════════════════════════
# Mac 专属工具（本机 ffplay 播放 + 登录管理）
# ══════════════════════════════════════════════════════════════════

if CONSUMER == "mac":
    # ── 播放队列管理（ffplay 串行连播） ────────────────────────────
    _playback_queue: list[dict] = []
    _current_ffplay_proc = None
    _playback_worker_task = None

    def _clear_queue_and_stop():
        global _current_ffplay_proc
        _playback_queue.clear()
        if _current_ffplay_proc and _current_ffplay_proc.returncode is None:
            try:
                _current_ffplay_proc.terminate()
            except Exception:
                pass
        _current_ffplay_proc = None
        subprocess.run(["pkill", "-f", "ffplay"], capture_output=True)

    async def _playback_worker():
        global _current_ffplay_proc, _playback_worker_task
        try:
            while _playback_queue:
                task = _playback_queue.pop(0)
                args = ["ffplay", "-nodisp", "-loglevel", "quiet"]
                args.extend(["-loop", "0"] if task.get("loop") else ["-autoexit"])
                args.append(task["url"])
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                _current_ffplay_proc = proc
                await proc.wait()
                _current_ffplay_proc = None
        except Exception:
            pass
        finally:
            _current_ffplay_proc = None
            _playback_worker_task = None

    def _ensure_worker():
        global _playback_worker_task
        if _playback_worker_task is None or _playback_worker_task.done():
            _playback_worker_task = asyncio.create_task(_playback_worker())

    @mcp.tool()
    async def search_songs(keyword: str, num: int = 5) -> str:
        """搜索 QQ 音乐歌曲（仅列表，不含播放 URL；要播放用 search_song / play_song_locally）。

        Args:
            keyword: 歌曲名或歌手名
            num: 返回结果数量（默认5）
        """
        async with await _get_client() as client:
            result = await client.search.search_by_type(keyword, page=1, num=num)
            lines = [f"搜索「{keyword}」的结果："]
            for i, s in enumerate(result.song, 1):
                dur = f"{s.interval//60}:{s.interval%60:02d}" if getattr(s, "interval", 0) else ""
                lines.append(f"{i}. {s.name} — {_singer_str(s)} {dur}")
            if not result.song:
                lines.append("未找到相关歌曲")
            return "\n".join(lines)

    @mcp.tool()
    async def play_song_locally(keyword: str, mode: str = "replace", loop: bool = False) -> str:
        """搜索并通过 Mac 音响播放歌曲。

        需要先在授权页扫码登录。

        Args:
            keyword: 歌曲名或歌手名
            mode: "replace"(默认): 停止当前+清空队列播新歌; "append": 追加到队列末尾连播
            loop: 是否单曲循环（默认 False）
        """
        try:
            songs = await _resolve_play_urls(keyword, max_results=5)
        except RuntimeError as e:
            return f"❌ {e}"

        if not songs:
            return (f"找到了「{keyword}」但无可播放 URL（无版权或未登录）。{AUTH_PAGE_HINT}")

        best = songs[0]
        if mode == "replace":
            _clear_queue_and_stop()
            _playback_queue.append({**best, "loop": loop})
            _ensure_worker()
            return f"🎵 正在播放：{best['name']} — {best['singer']}（已替换播放列表）"
        elif mode == "append":
            _playback_queue.append({**best, "loop": loop})
            _ensure_worker()
            return f"🎵 已加入播放队列（第{len(_playback_queue)}首）：{best['name']} — {best['singer']}"
        else:
            return "❌ 未知模式，请用 'replace' 或 'append'"

    @mcp.tool()
    async def play_playlist(keywords: str) -> str:
        """批量搜索并连播多首歌曲（Mac 音响，串行播放，一首接一首）。

        适合"播十首钢琴曲"、"连播歌单"等场景。

        Args:
            keywords: JSON 数组字符串，如 '["梦中的婚礼", "Summer 久石让"]'
        """
        try:
            song_list = json.loads(keywords)
        except Exception:
            return '❌ keywords 解析失败，请传 JSON 数组字符串'

        if not isinstance(song_list, list) or not song_list:
            return "❌ 歌曲列表为空"

        resolved, failed = [], []
        for kw in song_list:
            songs = await _resolve_play_urls(kw, max_results=3)
            if songs:
                resolved.append(songs[0])
            else:
                failed.append(kw)

        if not resolved:
            return "❌ 所有歌曲都解析失败：" + "、".join(failed)

        _clear_queue_and_stop()
        _playback_queue.extend(resolved)
        _ensure_worker()

        lines = [f"🎵 播放列表已就绪（{len(resolved)}/{len(song_list)} 首）："]
        lines += [f"  {i}. {it['name']} — {it['singer']}" for i, it in enumerate(resolved, 1)]
        if failed:
            lines.append(f"  ⚠️ 失败 {len(failed)} 首：{'、'.join(failed)}")
        return "\n".join(lines)

    @mcp.tool()
    async def stop_playback() -> str:
        """停止播放并清空整个播放队列。用户说"停/别放了/安静"时使用。"""
        _clear_queue_and_stop()
        return "⏹️ 已停止播放并清空队列"

    @mcp.tool()
    async def qqmusic_login(login_type: str = "mobile") -> str:
        """QQ 音乐扫码登录（统一入口已迁移到授权页）。

        Returns:
            授权页地址说明
        """
        cred = _load_credential()
        if cred and not cred.is_expired():
            return f"✅ 已处于登录状态（uin={getattr(cred, 'encrypt_uin', '?')}），无需重复登录。"
        return (
            "## 📱 QQ 音乐扫码登录\n\n"
            f"请打开授权页扫码：**{AUTH_PAGE_HINT}**\n\n"
            "页面会实时显示扫码进度，授权成功后凭证自动生效。\n"
            "扫完可调用 check_login_status 确认。"
        )

    @mcp.tool()
    async def check_login_status() -> str:
        """检查 QQ 音乐登录状态（读共享凭证文件）。"""
        cred = _load_credential()
        if cred is None:
            return f"❌ 未登录。{AUTH_PAGE_HINT}"
        if cred.is_expired():
            return f"⚠️ 凭证已过期且自动续期失败。{AUTH_PAGE_HINT}"
        return f"✅ 已登录（uin={getattr(cred, 'encrypt_uin', '?')}）"

    @mcp.tool()
    async def logout() -> str:
        """退出 QQ 音乐登录，清除共享凭证。"""
        removed = []
        for p in [CRED_PATH,
                  os.path.expanduser("~/.hermes/qqmusic_login_pending.json"),
                  os.path.expanduser("~/.hermes/qqmusic_qr.png")]:
            if os.path.exists(p):
                os.remove(p)
                removed.append(p)
        return ("✅ 已退出登录，清除：" + "\n".join(removed)) if removed else "ℹ️ 当前未登录"


if __name__ == "__main__":
    mcp.run(transport="stdio")
