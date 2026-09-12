#!/usr/bin/env python3
"""假设备客户端推曲目级事件的端到端历史缝（issue #10 的验收缝）。

issue 原文的验收缝要求：「**同一假设备客户端**，断言历史里出现事件、且暂停/继续
未产生条目」。本文件把这条缝真的跑一遍——不是纯逻辑比对，而是：

  1. 起一个**进程内的最小 WebSocket 服务端**（与本仓库真服务端同一入口形状：
     收 ``type=mcp`` 帧 → ``handle_mcp_message``）；
  2. 用 ``tools/latency_loop.py`` 的**真推送函数**（``push_music_notification``）
     把通知发过去（线上形状就是 ``{"type":"mcp","payload":…}``）；
  3. 复用 ``mcp_handler`` 的**真事件入口**（``_apply_music_session_event``）与
     真的 ``Dialogue`` / ``MusicSession``；
  4. 断言两件事：
     - **历史里出现事件**：``Dialogue`` 里多出曲目级 system 条目，且顺序正确；
     - **暂停/继续未产生条目**：条目数不变（同时状态确实变了——「不发」与
       「不写历史」是两件事）。

判别力：断言打在真 ``Dialogue`` 对象与真锚点行上，不是脚本自造的字符串。
把「暂停也写进历史」或「换歌就地改写」注入实现都会让本文件变红（已实测，
见 issue #10 报告）。

为什么仍然单独一个文件（与 ``test_music_history_seam.py`` 的分工）：那个文件
走 ``handle_mcp_message`` 的直接调用（快、是主回归）；本文件多走一段真实网络
与假设备客户端的推送函数，钉住的是「**同一假设备客户端**」这半句——正是 issue
验收缝的字面要求。

运行：server/.venv/bin/python -m unittest discover -s tools/tests -t tools -v
（不连外网、不起真服务端；LLM/ASR/TTS 一概不需要。）
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

TOOLS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOLS_ROOT.parent
SERVER_ROOT = REPO_ROOT / "server"
sys.path.insert(0, str(TOOLS_ROOT))
sys.path.insert(0, str(SERVER_ROOT))

import latency_loop  # noqa: E402
import websockets  # noqa: E402

from core.utils.dialogue import Dialogue  # noqa: E402
from core.utils.music_session import MusicSession  # noqa: E402
from core.providers.tools.device_mcp import mcp_handler  # noqa: E402

BASE_PROMPT_PATH = SERVER_ROOT / "agent-base-prompt.txt"
HISTORY_TAG = "音乐动态"


class _NullLogger:
    """接管 mcp_handler 的模块日志，把锚点行收进内存供断言。"""

    def __init__(self):
        self.lines = []

    def bind(self, **_kwargs):
        return self

    def _record(self, level, message):
        self.lines.append((level, str(message)))

    def debug(self, message):
        self._record("debug", message)

    def info(self, message):
        self._record("info", message)

    def warning(self, message):
        self._record("warning", message)

    def error(self, message):
        self._record("error", message)

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


class _FakeConn:
    """真事件入口只需要这几个成员（音乐会话 + 对话 + 日志）。"""

    def __init__(self):
        self.logger = _NullLogger()
        self.music_session = MusicSession()
        self.dialogue = Dialogue()
        self.dialogue.update_system_message(
            BASE_PROMPT_PATH.read_text(encoding="utf-8"))

    def history_texts(self):
        return [m.content for m in self.dialogue.dialogue
                if m.role == "system" and HISTORY_TAG in (m.content or "")]


class _StubServer:
    """进程内最小服务端：只做 hello 握手 + 把 ``type=mcp`` 帧喂给真入口。

    跑在自己的线程 + 自己的事件循环上（测试主体是同步的），收摊时**在循环内部**
    等 ``async with`` 真正退出（关监听、取消残留连接）才停循环——否则 websockets
    会在已关闭的循环上 ``create_task``，吐一串 RuntimeError。
    """

    def __init__(self, conn):
        self.conn = conn
        self.port = None
        self._ready = threading.Event()
        self._shutdown = None
        self._done = threading.Event()

    async def _handler(self, ws):
        hello = json.loads(await ws.recv())
        assert hello.get("type") == "hello", hello
        await ws.send(json.dumps({"type": "hello", "version": 1,
                                  "session_id": "stub"}))
        async for raw in ws:
            frame = json.loads(raw)
            if frame.get("type") == "mcp":
                # 与本仓库真服务端同一条路由（mcp_handler.handle_mcp_message）。
                await mcp_handler.handle_mcp_message(
                    self.conn, None, frame.get("payload"))

    async def _serve(self):
        async with websockets.serve(self._handler, "127.0.0.1", 0) as server:
            self.port = server.sockets[0].getsockname()[1]
            self._ready.set()
            await self._shutdown
        self._done.set()

    def start(self):
        self._loop = asyncio.new_event_loop()
        self._shutdown = self._loop.create_future()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(10)
        return self

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    def stop(self):
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._shutdown.set_result, None)
        self._thread.join(10)
        self._done.wait(10)
        self._loop.close()


def _push_over_the_wire(port, payloads):
    """用假设备客户端的**真推送函数**发一串通知（验收缝的「同一客户端」）。"""

    async def _go():
        url = "ws://127.0.0.1:%d/xiaozhi/v1/" % port
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({
                "type": "hello", "version": 1,
                "features": {"mcp": True}, "transport": "websocket"}))
            while True:
                if json.loads(await ws.recv()).get("type") == "hello":
                    break
            for payload in payloads:
                await latency_loop.push_music_notification(ws, payload)
            # 让服务端把最后一帧处理完再关（真服务端也是异步消费的）。
            await asyncio.sleep(0.05)

    asyncio.run(_go())


class FakeDeviceHistorySeam(unittest.TestCase):
    """**同一假设备客户端** → 真事件入口 → 真历史：整条缝。"""

    def setUp(self):
        self._saved_logger = mcp_handler.logger
        self.null = _NullLogger()
        mcp_handler.logger = self.null
        self.addCleanup(self._restore)
        self.conn = _FakeConn()
        self.server = _StubServer(self.conn).start()
        self.addCleanup(self.server.stop)

    def _restore(self):
        mcp_handler.logger = self._saved_logger

    def _anchors(self):
        return [m for _lvl, m in self.null.lines if "Music history:" in m]

    def _note(self, state, **kwargs):
        return latency_loop.music_notification(state, **kwargs)

    def test_song_change_produces_two_ordered_entries(self):
        """换歌产生新的开始条目，顺序可读（「昨天放的那首」靠这个顺序）。"""
        _push_over_the_wire(self.server.port, [
            self._note("started", title="晴天"),
            self._note("started", title="稻香"),
        ])
        texts = self.conn.history_texts()
        self.assertEqual(len(texts), 2)
        self.assertIn("晴天", texts[0])
        self.assertIn("稻香", texts[1])

    def test_pause_and_resume_add_no_entries_but_reach_the_state(self):
        """验收缝的字面要求：历史里出现事件、而暂停/继续一条都没产生。"""
        _push_over_the_wire(self.server.port, [
            self._note("started", title="晴天"),
            self._note("paused", title="晴天"),
            self._note("resumed", title="晴天"),
            self._note("completed", title="晴天"),
        ])
        texts = self.conn.history_texts()
        self.assertEqual(len(texts), 2, "started + completed 两条，暂停/继续不写")
        self.assertIn("开始播放", texts[0])
        self.assertIn("播完", texts[1])
        # 「不写历史」不等于「不发」：状态确实被暂停/继续改过（现在停在 completed）。
        self.assertEqual(self.conn.music_session.prompt().state, "completed")
        # 锚点把「为什么没写」也说清了——不是靠缺席推断。
        skipped = [a for a in self._anchors() if "skipped=not_track_level" in a]
        self.assertEqual(len(skipped), 2)
        self.assertTrue(all("event=paused" in a or "event=resumed" in a
                            for a in skipped))

    def test_duplicate_push_over_the_wire_stays_idempotent(self):
        note = self._note("started", title="晴天")
        _push_over_the_wire(self.server.port, [note, note, note])
        self.assertEqual(len(self.conn.history_texts()), 1)
        dupes = [a for a in self._anchors() if "skipped=duplicate" in a]
        self.assertEqual(len(dupes), 2)

    def test_completed_and_interrupted_are_distinguishable_in_history(self):
        _push_over_the_wire(self.server.port, [
            self._note("started", title="晴天"),
            self._note("interrupted", title="晴天"),
        ])
        texts = self.conn.history_texts()
        self.assertEqual(len(texts), 2)
        self.assertNotEqual(texts[0], texts[1])
        self.assertIn("中断", texts[1])

    def test_cli_assertion_accepts_the_real_anchor_lines(self):
        """CLI 的 ``assert_music_history`` 能吃掉**真锚点行**（验收缝的闭环）。

        这条钉住「假设备脚本对历史的断言」与「服务端打的锚点」是同一套词汇；
        两边各自纯逻辑绿、合起来对不上，是这类缝最典型的失效。
        """
        pushed = [self._note("started", title="晴天"),
                  self._note("paused", title="晴天"),
                  self._note("started", title="稻香")]
        _push_over_the_wire(self.server.port, pushed)
        fd, path = tempfile.mkstemp(suffix=".log")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(self._anchors()) + "\n")
        self.addCleanup(os.unlink, path)
        self.assertTrue(latency_loop.assert_music_history(pushed, path))

    def test_cli_assertion_rejects_when_pause_was_written(self):
        """闭环的判别力：把暂停的锚点改成 written=yes，CLI 断言必须变红。"""
        pushed = [self._note("started", title="晴天"),
                  self._note("paused", title="晴天")]
        _push_over_the_wire(self.server.port, pushed)
        polluted = []
        for line in self._anchors():
            if "event=paused" in line:
                line = line.replace("written=no", "written=yes")
            polluted.append(line)
        fd, path = tempfile.mkstemp(suffix=".log")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(polluted) + "\n")
        self.addCleanup(os.unlink, path)
        self.assertFalse(latency_loop.assert_music_history(pushed, path))


if __name__ == "__main__":
    unittest.main()
