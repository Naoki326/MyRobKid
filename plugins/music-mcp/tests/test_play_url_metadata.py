#!/usr/bin/env python3
"""play_url 内容元数据契约测试（issue #2）。

契约：播放地址保持纯内容语义——「这份内容经代理可取」的属性（源地址、
来源页、标题、作者、时长、内容形态 finite/live）全部编进 play_url；
起点是播放会话状态、由设备持有，不编进 play_url（设备起流时以 ss 追加）。

运行：~/.hermes/hermes-agent/venv/bin/python -m unittest \
      plugins/music-mcp/tests/test_play_url_metadata.py -v
"""
import sys
import urllib.parse
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

import music_mcp  # noqa: E402


def query_of(play_url: str) -> dict:
    """把代理 URL 的查询串解析成 {参数名: [值,…]}（未解码）。"""
    return urllib.parse.parse_qs(urllib.parse.urlsplit(play_url).query)


def first(play_url: str, key: str):
    values = query_of(play_url).get(key)
    return values[0] if values else None


class EnsurePlayableMetadata(unittest.TestCase):
    """_ensure_playable 编码内容属性。"""

    def test_finite_content_carries_title_author_duration_form(self):
        u = music_mcp._ensure_playable(
            "https://cdn.example/song.mp3",
            title="晴天", author="周杰伦", duration=269,
        )
        self.assertEqual(first(u, "src"), "https://cdn.example/song.mp3")
        self.assertEqual(first(u, "title"), "晴天")
        self.assertEqual(first(u, "author"), "周杰伦")
        self.assertEqual(first(u, "duration"), "269")
        self.assertEqual(first(u, "form"), "finite")

    def test_title_is_percent_encoded(self):
        u = music_mcp._ensure_playable(
            "https://cdn.example/a.mp3", title="告白气球 & 彩蛋",
        )
        # 含空格与 & 的标题必须整体编码，不破坏查询串结构
        self.assertEqual(first(u, "title"), "告白气球 & 彩蛋")
        self.assertEqual(len(query_of(u).get("title", [])), 1)

    def test_live_content_has_no_duration(self):
        """直播流（电台）：形态标 live，不带时长。"""
        u = music_mcp._ensure_playable(
            "https://stream.example/radio.mp3", title="Jazz Radio", form="live",
        )
        self.assertEqual(first(u, "form"), "live")
        self.assertIsNone(first(u, "duration"))

    def test_unknown_duration_is_omitted(self):
        """时长未知（RSS 未提供）时省略参数，但形态仍是 finite。"""
        u = music_mcp._ensure_playable(
            "https://cdn.example/ep.mp3", title="某单集", duration=None,
        )
        self.assertIsNone(first(u, "duration"))
        self.assertEqual(first(u, "form"), "finite")

    def test_referer_still_works(self):
        u = music_mcp._ensure_playable(
            "https://cdn.example/bili.m4s",
            referer="https://www.bilibili.com",
            title="视频", author="UP主", duration=613, form="finite",
        )
        self.assertEqual(first(u, "referer"), "https://www.bilibili.com")
        self.assertEqual(first(u, "duration"), "613")

    def test_proxy_base_is_mdns_not_ip(self):
        """地址纪律（ADR-0002）：play_url 主机不许是 IP 字面量。"""
        host = urllib.parse.urlsplit(music_mcp._ensure_playable(
            "https://x/a.mp3", title="t")).hostname or ""
        self.assertFalse(
            all(ch.isdigit() or ch == "." for ch in host),
            f"play_url 主机退回了 IP 字面量：{host}",
        )


class PlayHintMentionsStart(unittest.TestCase):
    """点播提示必须把「起点参数」教给模型（设备工具是 play_music）。"""

    def test_xiaozhi_hint_tells_model_to_pass_start(self):
        hint = music_mcp._PLAY_HINT["xiaozhi"]
        self.assertIn("play_music", hint)
        self.assertIn("start", hint)


class RssDurationParsing(unittest.TestCase):
    """播客时长补数据源：RSS itunes:duration 的三种常见写法。"""

    def test_plain_seconds(self):
        self.assertEqual(music_mcp._parse_rss_duration("5430"), 5430)

    def test_mm_ss(self):
        self.assertEqual(music_mcp._parse_rss_duration("12:34"), 754)

    def test_hh_mm_ss(self):
        self.assertEqual(music_mcp._parse_rss_duration("1:02:03"), 3723)

    def test_garbage_is_none(self):
        self.assertIsNone(music_mcp._parse_rss_duration(" n/a "))
        self.assertIsNone(music_mcp._parse_rss_duration(""))
        self.assertIsNone(music_mcp._parse_rss_duration(None))
        self.assertIsNone(music_mcp._parse_rss_duration("0"))


if __name__ == "__main__":
    unittest.main()
