#!/usr/bin/env python3
"""假设备客户端推送音乐事件的契约测试（issue #9 验收缝二）。

契约：``tools/latency_loop.py`` 能经**既有 MCP 消息通路**推 ``music.session``
通知，并对「注入是否生效」下断言。这条缝服务端的另一半在
``server/tests/test_music_injection_seam.py``；这里钉的是**假设备侧**的协议形状
与断言判别力——假设备造的数据不该让断言变绿，绿的条件必须是服务端日志里真出现
注入锚点。

为什么本文件能做纯逻辑测试：通知的构造（协议形状）与注入锚点的判定都是纯函数；
网络部分只在 ``run()`` 里，本文件不碰（不连服务端、不发音频）。

断言四件事：
  1) 通知是 JSON-RPC **通知**（无 id）+ 方法名 ``music.session``，字段与
     ``server/core/utils/music_session.py`` 的线协议一致；
  2) 直播流通知**不带** ``position_s`` / ``duration_s``（带了就是撒谎）；
  3) 注入锚点的匹配含 state/title/author/form/位点五要素，且能认出「没有注入」
     （日志里没锚点 → 判定失败），这是「抓得住注入没生效」的判别力；
  4) 推送的 state 与日志里的 state 不一致 → 判定失败（防「脚本文不对题」）。

运行：server/.venv/bin/python -m unittest tools/tests/test_latency_music_push.py -v
"""
import json
import sys
import tempfile
import os
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import latency_loop  # noqa: E402


def inject_line(state="playing", title="晴天", author="周杰伦", form="finite",
                pos="83.4s", injected="yes", prefix="I (700) ConnectionHandler: "):
    return (f"{prefix}Music inject: injected={injected} state={state} "
            f"title='{title}' author='{author}' form={form} pos={pos}")


class MusicNotificationProtocol(unittest.TestCase):
    """通知形状与线协议一致（服务端 _parse 能直接吃）。"""

    def test_is_a_jsonrpc_notification_without_id(self):
        note = latency_loop.music_notification("started")
        self.assertEqual(note["jsonrpc"], "2.0")
        self.assertEqual(note["method"], "music.session")
        self.assertNotIn("id", note, "通知不应带 id（不期待响应）")

    def test_started_carries_authoritative_state_and_track(self):
        note = latency_loop.music_notification(
            "started", title="晴天", author="周杰伦", position_s=0, duration_s=269)
        params = note["params"]
        self.assertEqual(params["event"], "started")
        self.assertEqual(params["state"], "playing")
        self.assertEqual(params["title"], "晴天")
        self.assertEqual(params["author"], "周杰伦")
        self.assertEqual(params["form"], "finite")
        self.assertEqual(params["position_s"], 0)
        self.assertEqual(params["duration_s"], 269)

    def test_pause_and_resume_have_distinguishable_states(self):
        """暂停与继续必须能经这条通道表达（否则「暂停了吗」答不出）。"""
        paused = latency_loop.music_notification("paused")["params"]
        resumed = latency_loop.music_notification("resumed")["params"]
        self.assertEqual(paused["state"], "paused_user")
        self.assertEqual(resumed["state"], "playing")
        self.assertNotEqual(paused["state"], resumed["state"])

    def test_terminal_states_are_carried(self):
        for state in ("completed", "interrupted", "resume_failed", "stopped",
                      "start_failed"):
            with self.subTest(state=state):
                params = latency_loop.music_notification(state)["params"]
                self.assertEqual(params["state"], state)

    def test_live_notification_omits_position_and_duration(self):
        """直播流的位点无意义：通知里根本不许出现这两个字段。"""
        params = latency_loop.music_notification(
            "started", live=True, position_s=999, duration_s=3600)["params"]
        self.assertEqual(params["form"], "live")
        self.assertNotIn("position_s", params)
        self.assertNotIn("duration_s", params)

    def test_unknown_state_is_rejected(self):
        with self.assertRaises(ValueError):
            latency_loop.music_notification("wat")


class InjectAnchorDiscrimination(unittest.TestCase):
    """注入判定的判别力：脚本自造数据不会让它变绿。"""

    def _write_log(self, text):
        fd, path = tempfile.mkstemp(suffix=".log")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        self.addCleanup(os.unlink, path)
        return path

    def test_matching_anchor_passes(self):
        pushed = latency_loop.music_notification("started", title="晴天",
                                                 author="周杰伦")
        log = self._write_log("noise\n" + inject_line() + "\nnoise\n")
        self.assertTrue(latency_loop.assert_music_injected(pushed, log))

    def test_missing_anchor_fails(self):
        """注入没生效（日志里没有锚点）必须判定失败——这是本票的关键判别力。"""
        pushed = latency_loop.music_notification("started")
        log = self._write_log("server started\nMusic stream started: title='晴天'\n")
        self.assertFalse(latency_loop.assert_music_injected(pushed, log))

    def test_state_mismatch_fails(self):
        """日志里的状态与推送的不一致 → 失败（防脚本文不对题）。"""
        pushed = latency_loop.music_notification("paused")
        log = self._write_log(inject_line(state="playing"))
        self.assertFalse(latency_loop.assert_music_injected(pushed, log))

    def test_title_mismatch_fails(self):
        pushed = latency_loop.music_notification("started", title="稻香")
        log = self._write_log(inject_line(title="晴天"))
        self.assertFalse(latency_loop.assert_music_injected(pushed, log))

    def test_live_push_with_numeric_position_fails(self):
        """直播流场景下锚点带了数字位点 → 失败（「电台报位点」是撒谎）。"""
        pushed = latency_loop.music_notification("started", live=True)
        log = self._write_log(inject_line(form="live", pos="83.4s"))
        self.assertFalse(latency_loop.assert_music_injected(pushed, log))

    def test_live_push_with_none_position_passes(self):
        pushed = latency_loop.music_notification("started", live=True)
        log = self._write_log(inject_line(form="live", pos="none", title="晴天"))
        self.assertTrue(latency_loop.assert_music_injected(pushed, log))

    def test_missing_log_file_fails(self):
        pushed = latency_loop.music_notification("started")
        self.assertFalse(latency_loop.assert_music_injected(
            pushed, "/nonexistent/path/xiaozhi_server.log"))

    def test_anchor_regex_does_not_match_other_music_lines(self):
        """只有注入锚点算数：设备侧别的 Music 行不该被误认成注入。"""
        self.assertIsNone(latency_loop.MUSIC_INJECT_RE.search(
            "Music screen: action=now-playing title='晴天'"))
        self.assertIsNone(latency_loop.MUSIC_INJECT_RE.search(
            "Music session: applied=yes state=playing title='晴天'"))


class PushShapeOnTheWire(unittest.TestCase):
    """推送的线上形状与 ``protocol.cc::SendMcpMessage`` 一致。"""

    def test_push_wraps_payload_in_type_mcp_message(self):
        sent = []

        class FakeWs:
            async def send(self, text):
                sent.append(text)

        note = latency_loop.music_notification("started")
        import asyncio
        asyncio.run(latency_loop.push_music_notification(FakeWs(), note))
        frame = json.loads(sent[0])
        self.assertEqual(frame["type"], "mcp")
        self.assertEqual(frame["payload"], note)


class ParseCli(unittest.TestCase):
    """CLI 参数解析：选项值不得污染位置参数（否则 --server-log 的值会被当成问题）。"""

    def test_plain_question(self):
        text, music, log_path = latency_loop.parse_cli(["你好呀"])
        self.assertEqual(text, "你好呀")
        self.assertIsNone(music)
        self.assertIsNone(log_path)

    def test_default_question_when_none(self):
        text, music, log_path = latency_loop.parse_cli([])
        self.assertEqual(text, "你好呀")
        self.assertIsNone(music)

    def test_option_values_do_not_leak_into_the_question(self):
        text, music, log_path = latency_loop.parse_cli(
            ["这歌谁唱的", "--music", "started", "--music-live",
             "--server-log", "/dev/null"])
        self.assertEqual(text, "这歌谁唱的")
        self.assertEqual(log_path, "/dev/null")
        self.assertEqual(music["params"]["form"], "live")

    def test_music_options_build_the_notification(self):
        text, music, _log = latency_loop.parse_cli(
            ["--music", "paused", "--music-title", "稻香", "--music-pos", "42",
             "问一下"])
        self.assertEqual(text, "问一下")
        params = music["params"]
        self.assertEqual(params["state"], "paused_user")
        self.assertEqual(params["title"], "稻香")
        self.assertEqual(params["position_s"], 42)

    def test_log_path_falls_back_to_env(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"LOOP_SERVER_LOG": "/tmp/x.log"}):
            _text, _music, log_path = latency_loop.parse_cli(["hi"])
        self.assertEqual(log_path, "/tmp/x.log")


if __name__ == "__main__":
    unittest.main()
