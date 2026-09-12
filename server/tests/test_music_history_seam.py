#!/usr/bin/env python3
"""曲目级事件入对话历史的端到端缝（issue #10）。

契约：**设备推来一条 MCP 通知 → 服务端状态更新 → 对话历史里出现一条曲目级
事件条目**；且**只**在曲目级事件上出现（暂停与继续绝不落历史）。

为什么单列一个文件（与 ``test_music_injection_seam.py`` 的分工）：
  - #9 那条缝的出口是**注入**（当前状态进系统提示），判据是「送给模型的
    system 提示里出现曲目」；
  - 本票的出口是**历史**（先后顺序进对话历史），判据是「``Dialogue`` 里多了
    一条 system 事件、且它的文本说出了哪首」。
  两条出口不许合并：注入是「现在在放什么」，历史是「刚才放过什么」。历史写
  成了注入的副产品、或历史写入顺手把暂停也写进去，都会让本文件红。

判别力（本票最易糊弄处）：
  1. **暂停被误写进历史** —— ``paused``/``resumed`` 必须让历史条目数**不变**。
     只断言「started 写了」的话，把暂停也一起写进去照样是绿的；所以每一类都
     同时钉住「该写的写了」与「不该写的一条都没多」。
  2. **换歌就地改写上一条** —— 第二次 ``started`` 必须**新增**一条，且旧条目
     原样留在原位（文本含旧曲目）。就地改写会让 ``晴天`` 那一条消失，从而被
     ``assertIn("晴天", ...)`` 逮住。
  3. **重复推送产生重复条目** —— 幂等判据是 ``apply_event`` 的返回值，历史写入
     必须只在 True 时发生；本文件按「历史条目数不变」断言，而不是只看锚点。

不做的事：不重复写 ``MusicSession`` 的单测（那 31 项在
``test_music_session.py``）、不重复写注入缝（那 12 项在
``test_music_injection_seam.py``）。本文件只钉「事件 → 历史条目」这条新缝。

运行：server/.venv/bin/python -m unittest discover -s server/tests -t server -v
"""
import asyncio
import sys
import unittest
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from core.utils.dialogue import Dialogue, Message  # noqa: E402
from core.utils.music_session import MUSIC_SESSION_METHOD  # noqa: E402
from core.providers.tools.device_mcp import mcp_handler  # noqa: E402
from core.providers.tools.device_mcp.mcp_handler import (  # noqa: E402
    handle_mcp_message,
)

BASE_PROMPT_PATH = SERVER_ROOT / "agent-base-prompt.txt"

#: 历史条目的可识别标记：曲目级事件的文本都在这个标签里，与用户可见对话
#: 区分开（父 spec：「事件以 system 角色写入，与用户可见对话区分开」）。
HISTORY_TAG = "音乐动态"


class _NullLogger:
    """够用的假日志：记录行供断言，其余调用吞掉。"""

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


class FakeConn:
    """最小连接桩：只提供历史缝真正依赖的成员（音乐会话 + 对话 + 日志）。"""

    def __init__(self, with_music_session=True, with_dialogue=True):
        self.logger = _NullLogger()
        if with_music_session:
            from core.utils.music_session import MusicSession

            self.music_session = MusicSession()
        if with_dialogue:
            self.dialogue = Dialogue()
            self.dialogue.update_system_message(
                BASE_PROMPT_PATH.read_text(encoding="utf-8"))

    # ── 观察面 ──────────────────────────────────────────────────
    def history_entries(self):
        """对话历史里的曲目级事件条目（不含基础 system 提示与用户/助手消息）。

        判据取 ``Dialogue`` 里真实的 Message 对象——不是脚本自造的字符串，
        也不是锚点行。历史没写、写错角色、写进别处都会让这里为空。
        """
        return [m for m in self.dialogue.dialogue
                if m.role == "system" and HISTORY_TAG in (m.content or "")]

    def history_texts(self):
        return [m.content for m in self.history_entries()]

    def system_prompt_blocks(self):
        """历史里的 system 条目，按顺序拼成一份文本（模型看顺序就看它）。"""
        return "\n".join(self.history_texts())


def notification(**params):
    return {"jsonrpc": "2.0", "method": MUSIC_SESSION_METHOD, "params": params}


def push(conn, payload):
    """把通知走真实的 MCP 消息入口。"""
    asyncio.run(handle_mcp_message(conn, None, payload))


class TrackLevelEventsEnterHistory(unittest.TestCase):
    """开始播放 / 换歌 / 播完 / 中断 / 续播失败各留下一条。"""

    def test_started_writes_a_history_entry_with_the_track(self):
        conn = FakeConn()
        self.assertEqual(conn.history_entries(), [])

        push(conn, notification(event="started", state="playing", title="晴天",
                                author="周杰伦", form="finite",
                                position_s=0, duration_s=269))

        texts = conn.history_texts()
        self.assertEqual(len(texts), 1)
        self.assertIn("晴天", texts[0])
        # 角色是 system：与用户可见对话区分开（父 spec 的明确要求）。
        self.assertEqual(conn.history_entries()[0].role, "system")

    def test_song_change_appends_a_new_entry_instead_of_rewriting(self):
        """换歌产生**新的**开始条目，而不是就地改写上一条。

        判别力：就地改写会让第一条消失（``晴天`` 不在历史里），断言立刻红。
        顺序也要对——历史存在的意义就是让模型掌握先后。
        """
        conn = FakeConn()
        push(conn, notification(event="started", state="playing", title="晴天",
                                author="周杰伦", form="finite", duration_s=269))
        push(conn, notification(event="started", state="playing", title="稻香",
                                author="周杰伦", form="finite", duration_s=223))

        texts = conn.history_texts()
        self.assertEqual(len(texts), 2, "换歌必须是新增条目，不是就地改写")
        self.assertIn("晴天", texts[0])
        self.assertIn("稻香", texts[1])
        self.assertNotIn("稻香", texts[0], "旧条目不得被新曲目污染")

    def test_completed_and_interrupted_are_distinguishable_terminal_entries(self):
        """播完与中断各有终态条目，两者的文本可区分。"""
        completed = FakeConn()
        push(completed, notification(event="started", state="playing",
                                     title="晴天", author="周杰伦",
                                     form="finite", duration_s=269))
        push(completed, notification(event="completed", state="completed",
                                     title="晴天", author="周杰伦",
                                     form="finite", position_s=269,
                                     duration_s=269))
        interrupted = FakeConn()
        push(interrupted, notification(event="started", state="playing",
                                       title="晴天", author="周杰伦",
                                       form="finite", duration_s=269))
        push(interrupted, notification(event="interrupted", state="interrupted",
                                       title="晴天", author="周杰伦",
                                       form="finite", position_s=42,
                                       duration_s=269))

        completed_last = completed.history_texts()[-1]
        interrupted_last = interrupted.history_texts()[-1]
        self.assertIn("晴天", completed_last)
        self.assertIn("晴天", interrupted_last)
        self.assertNotEqual(completed_last, interrupted_last,
                            "播完与中断必须可区分——都是「音乐没了」，用户与模型要能分辨")
        self.assertNotIn("中断", completed_last)
        self.assertNotIn("播完", interrupted_last)

    def test_resume_failed_writes_its_own_entry(self):
        conn = FakeConn()
        push(conn, notification(event="started", state="playing", title="晴天",
                                author="周杰伦", form="finite", duration_s=269))
        push(conn, notification(event="resume_failed", state="resume_failed",
                                title="晴天", author="周杰伦", form="finite",
                                position_s=42, duration_s=269))
        last = conn.history_texts()[-1]
        self.assertIn("晴天", last)
        self.assertIn("续播", last)


class PauseAndResumeStayOutOfHistory(unittest.TestCase):
    """暂停与继续绝不写历史（但**仍要**经推送通道改变服务端状态）。

    这是本 spec 的核心防线：暂停/继续每轮对话会产生成对的两条，迅速淹没真实
    对话、把历史窗口挤爆。「不发」与「不写历史」是两件事，所以这里同时断言
    两半：历史条目数为 0，而状态确实变了。
    """

    def test_pause_and_resume_produce_no_history_entries(self):
        conn = FakeConn()
        push(conn, notification(event="started", state="playing", title="晴天",
                                author="周杰伦", form="finite", duration_s=269))
        baseline = len(conn.history_entries())

        push(conn, notification(event="paused", state="paused_user", title="晴天",
                                author="周杰伦", form="finite", position_s=42,
                                duration_s=269))
        push(conn, notification(event="resumed", state="playing", title="晴天",
                                author="周杰伦", form="finite", position_s=42,
                                duration_s=269))

        self.assertEqual(len(conn.history_entries()), baseline,
                         "暂停与继续绝不写历史（写法见本类的另一个断言：状态确实变了）")

    def test_pause_still_reaches_the_server_state(self):
        """「不写历史」不等于「不发」：通道必须照收，否则答不出「暂停了吗」。"""
        conn = FakeConn()
        push(conn, notification(event="started", state="playing", title="晴天",
                                author="周杰伦", form="finite", position_s=0,
                                duration_s=269))
        push(conn, notification(event="paused", state="paused_user", title="晴天",
                                author="周杰伦", form="finite", position_s=42,
                                duration_s=269))
        self.assertEqual(conn.music_session.prompt().state, "paused_user")
        push(conn, notification(event="resumed", state="playing", title="晴天",
                                author="周杰伦", form="finite", position_s=42,
                                duration_s=269))
        self.assertEqual(conn.music_session.prompt().state, "playing")

    def test_long_pause_resume_cycle_does_not_grow_history(self):
        """反复暂停/继续（每轮对话一对）不得让历史条目增长——防「历史被挤爆」。"""
        conn = FakeConn()
        push(conn, notification(event="started", state="playing", title="晴天",
                                author="周杰伦", form="finite", duration_s=269))
        baseline = len(conn.history_entries())
        for i in range(20):
            push(conn, notification(event="paused", state="paused_conversation",
                                    title="晴天", author="周杰伦", form="finite",
                                    position_s=i, duration_s=269))
            push(conn, notification(event="resumed", state="playing", title="晴天",
                                    author="周杰伦", form="finite",
                                    position_s=i, duration_s=269))
        self.assertEqual(len(conn.history_entries()), baseline)


class EventsOutsideTheTrackLevelSetAreExcluded(unittest.TestCase):
    """issue 正文列举的集合是字面约束：集合外的事件不写历史。

    issue #10 的集合：开始播放 / 换歌 / 播完 / 中断 / 续播失败。
    ``stopped``（用户按停）与 ``start_failed``（从未出声）**不在**该集合里，
    故不写历史。这条按字面遵循 issue，理由见 issue 报告的备注：
    ``start_failed`` 从未出声、本就没有「曲目」可言；``stopped`` 是边界，
    是否该写入留给调度方裁决（本票不擅自改）。
    """

    def test_stopped_produces_no_history_entry(self):
        conn = FakeConn()
        push(conn, notification(event="started", state="playing", title="晴天",
                                author="周杰伦", form="finite", duration_s=269))
        baseline = len(conn.history_entries())
        push(conn, notification(event="stopped", state="stopped", title="晴天",
                                author="周杰伦", form="finite", position_s=42,
                                duration_s=269))
        self.assertEqual(len(conn.history_entries()), baseline)
        # 但状态照收（通道粒度 ≠ 历史粒度）。
        self.assertEqual(conn.music_session.prompt().state, "stopped")

    def test_start_failed_produces_no_history_entry(self):
        conn = FakeConn()
        push(conn, notification(event="start_failed", state="start_failed",
                                title="晴天", author="周杰伦", form="finite",
                                duration_s=269))
        self.assertEqual(conn.history_entries(), [])
        self.assertEqual(conn.music_session.prompt().state, "start_failed")


class DuplicatePushIsIdempotentInHistory(unittest.TestCase):
    """重复推送同一事件不产生重复条目（幂等）。"""

    def test_duplicate_started_does_not_duplicate_the_entry(self):
        conn = FakeConn()
        payload = notification(event="started", state="playing", title="晴天",
                               author="周杰伦", form="finite", position_s=0,
                               duration_s=269)
        push(conn, payload)
        first = conn.history_texts()
        push(conn, payload)
        push(conn, payload)
        self.assertEqual(conn.history_texts(), first,
                         "重复推送必须幂等——历史条目不该增长")
        self.assertEqual(len(conn.history_texts()), 1)
        # 状态侧同样只有一次真正生效（apply_event 的返回值就是判据）。
        self.assertEqual(conn.music_session.applied_events, 1)
        self.assertEqual(conn.music_session.duplicate_events, 2)

    def test_duplicate_completed_does_not_duplicate_the_entry(self):
        conn = FakeConn()
        push(conn, notification(event="started", state="playing", title="晴天",
                                author="周杰伦", form="finite", duration_s=269))
        payload = notification(event="completed", state="completed", title="晴天",
                               author="周杰伦", form="finite", position_s=269,
                               duration_s=269)
        push(conn, payload)
        n = len(conn.history_entries())
        push(conn, payload)
        self.assertEqual(len(conn.history_entries()), n)


class HistoryEntriesReachTheIntentModel(unittest.TestCase):
    """历史条目的消费方是意图识别那次调用——它自建历史文本、不按角色过滤。

    本类的价值是钉住「这条历史真的会被模型看到」：只写进 ``Dialogue`` 而不被
    任何消费方读到，等于没做。``intent_llm`` 走的就是这份 ``dialogue`` 列表。
    """

    def test_entry_is_visible_in_the_intent_history_text(self):
        conn = FakeConn()
        push(conn, notification(event="started", state="playing", title="晴天",
                                author="周杰伦", form="finite", duration_s=269))
        push(conn, notification(event="started", state="playing", title="稻香",
                                author="周杰伦", form="finite", duration_s=223))
        # 复刻 intent_llm.detect_intent 拼历史文本的方式（逐条 role: content）。
        text = "".join("%s: %s\n" % (m.role, m.content)
                       for m in conn.dialogue.dialogue)
        self.assertIn("晴天", text)
        self.assertIn("稻香", text)
        self.assertLess(text.index("晴天"), text.index("稻香"),
                        "先后顺序必须可读——「换一首」靠的就是这个顺序")

    def test_pause_never_shows_up_in_the_intent_history_text(self):
        conn = FakeConn()
        push(conn, notification(event="started", state="playing", title="晴天",
                                author="周杰伦", form="finite", duration_s=269))
        push(conn, notification(event="paused", state="paused_user", title="晴天",
                                author="周杰伦", form="finite", position_s=42,
                                duration_s=269))
        text = "".join("%s: %s\n" % (m.role, m.content)
                       for m in conn.dialogue.dialogue)
        self.assertIn("晴天", text)
        # 历史里只有 started 那一条曲目级条目，暂停没留下任何痕迹。
        self.assertEqual(len(conn.history_entries()), 1)
        self.assertIn("开始播放", conn.history_texts()[0])
        extra_system = [m.content for m in conn.dialogue.dialogue
                        if m.role == "system" and HISTORY_TAG not in (m.content or "")]
        self.assertEqual(len(extra_system), 1, "除基础提示外不该再有 system")
        self.assertNotIn("暂停", "\n".join(extra_system))


class HistoryAnchorIsAssertable(unittest.TestCase):
    """历史写入路径上的锚点：写入/未写入 + 事件 + 曲目 + 为什么。

    验收缝（假设备客户端）看不到服务端进程内的历史，只能看日志；所以「暂停没
    写历史」也必须是**可断言**的，而不只是「没看到」。锚点与既有的
    ``Music session:`` / ``Music inject:`` 同族（行内风格、字段名一致）。
    """

    def setUp(self):
        self._saved = mcp_handler.logger
        self._null = _NullLogger()
        mcp_handler.logger = self._null
        self.addCleanup(self._restore)

    def _restore(self):
        mcp_handler.logger = self._saved

    def _anchors(self):
        return [m for _level, m in self._null.lines if "Music history:" in m]

    def test_write_failure_is_not_reported_as_no_dialogue(self):
        """对话在、写入抛异常时必须报 skipped=write_failed，不能报 no_dialogue。

        为什么单钉这条：`no_dialogue` 的语义是「连接上没有对话可写」，而写入
        失败是**真故障**（另有 error 行）。两者混作一个值，锚点就在说谎——
        而「没写历史」全靠这条锚点才可断言，一个会说谎的锚点会给验收缝一个
        绿色的假像。这条测试会在两种情形被合并时立刻变红。
        """
        conn = FakeConn()
        conn.dialogue.put = lambda _message: (_ for _ in ()).throw(
            RuntimeError("boom"))
        push(conn, notification(event="started", state="playing", title="晴天",
                                form="finite", duration_s=269))
        anchors = self._anchors()
        self.assertTrue(anchors, "缺少 Music history: 锚点行")
        self.assertIn("written=no", anchors[-1])
        self.assertIn("skipped=write_failed", anchors[-1])
        self.assertNotIn("no_dialogue", anchors[-1])
        # entry= 是「写下去的那串字符」——没写就不该报。
        self.assertNotIn("entry=", anchors[-1])

    def test_anchor_reports_written_event_and_track(self):
        conn = FakeConn()
        push(conn, notification(event="started", state="playing", title="晴天",
                                author="周杰伦", form="finite", duration_s=269))
        anchor = self._anchors()[-1]
        self.assertIn("written=yes", anchor)
        self.assertIn("event=started", anchor)
        self.assertIn("title='晴天'", anchor)

    def test_anchor_says_why_pause_was_skipped(self):
        """暂停必须留下**为什么没写**的证据——「没看到」不可断言。"""
        conn = FakeConn()
        push(conn, notification(event="paused", state="paused_user", title="晴天",
                                author="周杰伦", form="finite", position_s=42,
                                duration_s=269))
        anchor = self._anchors()[-1]
        self.assertIn("written=no", anchor)
        self.assertIn("skipped=not_track_level", anchor)
        self.assertIn("event=paused", anchor)

    def test_anchor_says_duplicate_was_skipped(self):
        conn = FakeConn()
        payload = notification(event="started", state="playing", title="晴天",
                               author="周杰伦", form="finite", duration_s=269)
        push(conn, payload)
        push(conn, payload)
        anchor = self._anchors()[-1]
        self.assertIn("written=no", anchor)
        self.assertIn("skipped=duplicate", anchor)


class MissingCollaboratorsAreHarmless(unittest.TestCase):
    """没有 dialogue / 没有 music_session 的连接都不该抛（关闭音乐功能无害）。"""

    def test_connection_without_dialogue_does_not_raise(self):
        conn = FakeConn(with_dialogue=False)
        push(conn, notification(event="started", state="playing", title="晴天",
                                author="周杰伦", form="finite", duration_s=269))

    def test_connection_without_music_session_does_not_raise(self):
        conn = FakeConn(with_music_session=False)
        push(conn, notification(event="started", state="playing", title="晴天",
                                author="周杰伦", form="finite", duration_s=269))
        self.assertEqual(conn.history_entries(), [])

    def test_bad_params_write_nothing(self):
        conn = FakeConn()
        for params in (None, [], "started", 3, {"state": "wat"}, {"event": "wat"}):
            push(conn, {"jsonrpc": "2.0", "method": MUSIC_SESSION_METHOD,
                        "params": params})
        self.assertEqual(conn.history_entries(), [])


if __name__ == "__main__":
    unittest.main()
