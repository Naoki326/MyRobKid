#!/usr/bin/env python3
"""音乐会话的端到端注入缝（issue #9）。

契约：**设备推来一条 MCP 通知 → 服务端状态更新 → 送给模型的内容里出现音乐
状态**。这条缝横跨三个模块（``mcp_handler`` 的事件入口、``MusicSession`` 的
状态机、``dialogue`` 的占位符展开），单测各模块都绿也可能整条链路是断的——
本文件钉的就是那条链。

判别力（本票最易糊弄处）：断言的对象是**真正送给模型的那份 system 提示**
（``Dialogue.get_llm_dialogue_with_memory`` 的返回值），不是测试脚本自造的
字符串，也不是 `MusicSession` 的内部字段。注入没生效（占位符没展开、状态没
更新、通知没路由）都会让断言失败——这正是「看起来做了、实际一半失效」要防的。

不重复写 `MusicSession` 的单测（那 31 项在 test_music_session.py）；这里只走
通知入口到注入文本这一条路。不变量：

  1. ``music.session`` 通知经 ``handle_mcp_message`` 让注入文本出现曲目/作者；
  2. 换歌后再注入的是新曲目（最新状态，不是会话开始时的快照）；
  3. 暂停与继续都改变注入的状态口径（「暂停了吗」答得出），且**不**落在历史里
     （历史是另一条出口，此处只确认注入这一侧）；
  4. 直播流的注入文本里没有任何 ``m:ss`` 位点；
  5. 重复推送同一事件的注入文本不产生第二块、状态不变（幂等）；
  6. 未知 method / 坏 params / 没有 music_session 的连接都安静忽略——关闭音乐
     功能时注入完全无害（不注入、不报错、不抛）。

运行：server/.venv/bin/python -m unittest discover -s server/tests -t server -v
"""
import asyncio
import sys
import unittest
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parent
sys.path.insert(0, str(SERVER_ROOT))

from core.utils.dialogue import Dialogue, Message  # noqa: E402
from core.utils.music_session import MUSIC_SESSION_METHOD  # noqa: E402
from core.providers.tools.device_mcp import mcp_handler  # noqa: E402
from core.providers.tools.device_mcp.mcp_handler import (  # noqa: E402
    handle_mcp_message,
)

#: 真实的 base prompt 模板——断言必须打在它身上：模板里没有 <music_status>
#: 占位符、或占位符被写错标签，注入都是静默 no-op（本缝要能逮住那种失效）。
BASE_PROMPT_PATH = SERVER_ROOT / "agent-base-prompt.txt"


class _NullLogger:
    """够用的假日志：记录锚点行供断言，其余调用吞掉。"""

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
        # 任何未显式实现的日志方法都退化成 no-op（不断言它们）。
        return lambda *_args, **_kwargs: None


class FakeConn:
    """最小连接桩：只提供注入缝真正依赖的成员（音乐会话 + 对话 + 日志）。

    ``music_prompt()`` 是 ``ConnectionHandler`` 的真方法，这里直接借用同一份
    逻辑吗？不——那会把被测对象换成一个测试副本。这里复刻的是**调用形态**（取
    最新快照、给文本），逻辑仍由 ``MusicSession`` 与 ``apply_prompt_placeholder``
    承担，所以注入没生效时断言照样失败。（真实注入路径上的锚点与 ``injected``
    判据由本文件末尾的 ``RealInjectionAnchorProvesTheTextLanded`` 直接测真方法。）
    """

    def __init__(self, with_music_session=True):
        self.logger = _NullLogger()
        if with_music_session:
            from core.utils.music_session import MusicSession

            self.music_session = MusicSession()
        self.dialogue = Dialogue()
        self.dialogue.update_system_message(BASE_PROMPT_PATH.read_text(encoding="utf-8"))

    def music_prompt(self):
        # 与 ConnectionHandler.music_prompt 同一形态：现取快照，出错就退化成
        # None（注入是只读旁路，不该把对话一起带走）。没有 music_session 的
        # 连接（关闭音乐功能）也走这条。
        try:
            snapshot = self.music_session.prompt()
        except Exception:
            return None
        return snapshot

    def system_prompt_sent_to_model(self):
        """真正送给模型的那份 system 提示（展开记忆与音乐状态之后）。"""
        prompt = self.music_prompt()
        messages = self.dialogue.get_llm_dialogue_with_memory(
            memory_str="", voiceprint_config={}, current_speaker=None,
            music_status=prompt.text if prompt is not None else "",
        )
        return next(m["content"] for m in messages if m["role"] == "system")


def notification(**params):
    """设备推来的 JSON-RPC 通知（无 id = 不期待响应）。"""
    return {"jsonrpc": "2.0", "method": MUSIC_SESSION_METHOD, "params": params}


def push(conn, payload):
    """把通知走真实的 MCP 消息入口（与 ``mcpMessageHandler`` 同一条调用）。"""
    asyncio.run(handle_mcp_message(conn, None, payload))


class NotificationCarriesTheTrack(unittest.TestCase):
    """推一条通知 → 送给模型的内容里出现音乐状态。"""

    def test_started_reaches_the_model_prompt(self):
        conn = FakeConn()
        before = conn.system_prompt_sent_to_model()
        self.assertNotIn("晴天", before)
        # 没有会话时占位符整块摘掉：模板里不留空壳。
        self.assertNotIn("<music_status>", before)

        push(conn, notification(event="started", state="playing", title="晴天",
                                author="周杰伦", form="finite",
                                position_s=0, duration_s=269))

        after = conn.system_prompt_sent_to_model()
        self.assertIn("<music_status>", after)
        self.assertIn("晴天", after)
        self.assertIn("周杰伦", after)

    def test_pause_and_resume_both_change_the_injection(self):
        """暂停/继续必须推送：否则「暂停了吗」答不出、位点还会虚涨。"""
        conn = FakeConn()
        push(conn, notification(event="started", state="playing", title="晴天",
                                author="周杰伦", form="finite",
                                position_s=10, duration_s=269))
        self.assertIn("正在播放", conn.system_prompt_sent_to_model())

        push(conn, notification(event="paused", state="paused_user", title="晴天",
                                author="周杰伦", form="finite",
                                position_s=10, duration_s=269))
        paused = conn.system_prompt_sent_to_model()
        self.assertIn("用户暂停", paused)

        push(conn, notification(event="resumed", state="playing", title="晴天",
                                author="周杰伦", form="finite",
                                position_s=10, duration_s=269))
        self.assertIn("正在播放", conn.system_prompt_sent_to_model())

    def test_song_change_injects_the_new_track(self):
        """注入是最新的：换歌后再问，送给模型的是新曲目。"""
        conn = FakeConn()
        push(conn, notification(event="started", state="playing", title="晴天",
                                author="周杰伦", form="finite", duration_s=269))
        push(conn, notification(event="started", state="playing", title="稻香",
                                author="周杰伦", form="finite", duration_s=223))
        prompt = conn.system_prompt_sent_to_model()
        self.assertIn("稻香", prompt)
        self.assertNotIn("晴天", prompt)

    def test_completion_clears_the_playing_state(self):
        conn = FakeConn()
        push(conn, notification(event="started", state="playing", title="晴天",
                                author="周杰伦", form="finite",
                                position_s=0, duration_s=269))
        push(conn, notification(event="completed", state="completed", title="晴天",
                                author="周杰伦", form="finite",
                                position_s=269, duration_s=269))
        self.assertIn("没有音乐在播放", conn.system_prompt_sent_to_model())


class LiveStreamHasNoClock(unittest.TestCase):
    """直播流的注入内容不含位点（电台没有终点，报位点就是撒谎）。"""

    def test_live_injection_has_no_position(self):
        conn = FakeConn()
        push(conn, notification(event="started", state="playing", title="Jazz24",
                                author="", form="live",
                                position_s=999, duration_s=3600))
        prompt = conn.system_prompt_sent_to_model()
        self.assertIn("Jazz24", prompt)
        self.assertIn("直播流", prompt)
        # 只看音乐状态块：模板里别处本就有当前时间（01:15）的 m:ss 形状，
        # 拿整个提示去匹配会假阳性。
        block = prompt.split("<music_status>", 1)[1].split("</music_status>", 1)[0]
        self.assertNotRegex(block, r"\d:\d\d")


class IdempotentAcrossTheSeam(unittest.TestCase):
    """重复推送同一事件：注入文本不产生第二块、状态不变。"""

    def test_duplicate_notification_is_a_no_op(self):
        conn = FakeConn()
        payload = notification(event="started", state="playing", title="晴天",
                               author="周杰伦", form="finite",
                               position_s=0, duration_s=269)
        push(conn, payload)
        first = conn.system_prompt_sent_to_model()
        push(conn, payload)
        second = conn.system_prompt_sent_to_model()
        self.assertEqual(conn.music_session.applied_events, 1)
        # 位点可能随挂钟推进，但曲目/状态/块数不变。
        self.assertEqual(first.count("<music_status>"), 1)
        self.assertEqual(second.count("<music_status>"), 1)
        self.assertIn("晴天", second)


class OtherMethodsAreUntouched(unittest.TestCase):
    """这条分支同时承载其它 method 通知：未知方法保持「只记一行日志」。"""

    def test_unknown_method_is_ignored(self):
        conn = FakeConn()
        payload = {"jsonrpc": "2.0", "method": "something.else",
                   "params": {"event": "started", "state": "playing",
                              "title": "晴天"}}
        push(conn, payload)  # 不抛
        self.assertFalse(conn.music_session.has_state)
        self.assertNotIn("晴天", conn.system_prompt_sent_to_model())

    def test_bad_params_are_ignored_silently(self):
        conn = FakeConn()
        for params in (None, [], "started", 3, {"state": "wat"}, {"event": "wat"}):
            push(conn, {"jsonrpc": "2.0", "method": MUSIC_SESSION_METHOD,
                        "params": params})
        self.assertFalse(conn.music_session.has_state)
        self.assertNotIn("<music_status>", conn.system_prompt_sent_to_model())


class MissingMusicSessionIsHarmless(unittest.TestCase):
    """关闭音乐功能（连接上没有 music_session）时注入完全无害。"""

    def test_connection_without_music_session_does_not_raise(self):
        conn = FakeConn(with_music_session=False)
        push(conn, notification(event="started", state="playing", title="晴天"))
        self.assertNotIn("<music_status>", conn.system_prompt_sent_to_model())


class TelemetryAnchorHasDiscriminatingFields(unittest.TestCase):
    """注入路径上的锚点含 state/title/form/位点——「注入没生效」可被日志断言。

    锚点走 ``mcp_handler`` 的模块日志（与固件 ``Music screen:`` 同族的行内
    风格）。测试界面临时接管它，把行收进内存——不靠日志级别或后端配置。
    """

    def setUp(self):
        self._saved = mcp_handler.logger
        self._null = _NullLogger()
        mcp_handler.logger = self._null
        self.addCleanup(self._restore)

    def _restore(self):
        mcp_handler.logger = self._saved

    def _anchors(self):
        return [m for _level, m in self._null.lines if "Music session:" in m]

    def test_anchor_records_applied_state_and_form(self):
        conn = FakeConn()
        push(conn, notification(event="started", state="playing", title="晴天",
                                author="周杰伦", form="finite",
                                position_s=83.4, duration_s=269))
        anchors = self._anchors()
        self.assertTrue(anchors, "缺少 Music session: 锚点行")
        anchor = anchors[-1]
        self.assertIn("applied=yes", anchor)
        self.assertIn("state=playing", anchor)
        self.assertIn("title='晴天'", anchor)
        self.assertIn("form=finite", anchor)
        self.assertIn("pos=83.4s", anchor)

    def test_duplicate_anchor_says_not_applied(self):
        conn = FakeConn()
        payload = notification(event="started", state="playing", title="晴天",
                               form="finite", position_s=0, duration_s=269)
        push(conn, payload)
        push(conn, payload)
        self.assertIn("applied=no", self._anchors()[-1])

    def test_live_anchor_reports_live_and_no_numeric_position(self):
        conn = FakeConn()
        push(conn, notification(event="started", state="playing", title="Jazz24",
                                form="live", position_s=999, duration_s=3600))
        anchor = self._anchors()[-1]
        self.assertIn("form=live", anchor)
        self.assertIn("pos=live", anchor)


if __name__ == "__main__":
    unittest.main()


class RealInjectionAnchorProvesTheTextLanded(unittest.TestCase):
    """注入锚点必须证明「音乐状态**真的进了**送给模型的那份提示」。

    为什么单列一类：上面几类的 ``FakeConn`` 自己复刻了一遍取值逻辑，它证明
    得了「状态机 → 提示文本」这条链，却证明不了 ``ConnectionHandler`` 里那
    两行真实代码。而 issue #9 最贵的失效恰在那里——状态读到了、占位符却没
    展开（标签写错、拼装回归、用户自定义模板），提示里什么都没有，锚点却照
    旧报 ``state=playing``：看起来做了、实际一半失效。

    所以这里直接借 ``ConnectionHandler`` 的**真方法**（unbound 调用），只造
    一个够用的宿主对象；判据是 ``injected=`` 字段，而它由「注入文本是否真出现
    在系统提示里」算出。
    """

    def _stub(self, with_music_session=True):
        from core.connection import ConnectionHandler
        from core.utils.music_session import MusicSession

        class _Host:
            pass

        host = _Host()
        host.logger = _NullLogger()
        if with_music_session:
            host.music_session = MusicSession()
        # 真方法（unbound）：测的就是它们本身，不是副本。
        host.music_prompt = lambda: ConnectionHandler.music_prompt(host)
        host.log_music_injection = (
            lambda dialogue, prompt: ConnectionHandler.log_music_injection(
                host, dialogue, prompt))
        return host

    def _anchors(self, host):
        return [m for _lvl, m in host.logger.lines if "Music inject:" in m]

    def test_injected_yes_when_the_text_reached_the_system_prompt(self):
        host = self._stub()
        host.music_session.apply_event(
            {"event": "started", "state": "playing", "title": "晴天",
             "author": "周杰伦", "form": "finite", "position_s": 83.4,
             "duration_s": 269})
        prompt = host.music_prompt()
        dialogue = Dialogue()
        dialogue.update_system_message(
            BASE_PROMPT_PATH.read_text(encoding="utf-8"))
        messages = dialogue.get_llm_dialogue_with_memory(
            memory_str="", voiceprint_config={}, current_speaker=None,
            music_status=prompt.text)
        host.log_music_injection(messages, prompt)
        anchor = self._anchors(host)[-1]
        self.assertIn("injected=yes", anchor)
        self.assertIn("state=playing", anchor)
        self.assertIn("title='晴天'", anchor)

    def test_injected_no_when_the_placeholder_never_expanded(self):
        """判别力：状态读到了但**没进**提示时，锚点必须报 injected=no。

        这条用一个不含 ``<music_status>`` 的模板模拟「拼装回归 / 自定义模板」
        ——注入成了 no-op，而状态本身完全正常。修复前（锚点只看「读了快照」）
        这里会报成生效，工具侧 ``tools/latency_loop.py`` 的 ``--music`` 验收
        缝就会在真的坏了的时候保持绿色。
        """
        host = self._stub()
        host.music_session.apply_event(
            {"event": "started", "state": "playing", "title": "晴天",
             "form": "finite", "position_s": 83.4, "duration_s": 269})
        prompt = host.music_prompt()
        dialogue = Dialogue()
        dialogue.update_system_message("你是一个助手，没有音乐占位符。")
        messages = dialogue.get_llm_dialogue_with_memory(
            memory_str="", voiceprint_config={}, current_speaker=None,
            music_status=prompt.text)
        host.log_music_injection(messages, prompt)
        self.assertIn("injected=no", self._anchors(host)[-1])

    def test_no_anchor_without_a_music_session(self):
        """没有会话（关闭音乐功能）时一行锚点都不打——空转也不该刷日志。"""
        host = self._stub(with_music_session=False)
        prompt = None  # music_session 缺失时调用方拿到的是 None
        host.log_music_injection([], prompt)
        self.assertEqual(self._anchors(host), [])

