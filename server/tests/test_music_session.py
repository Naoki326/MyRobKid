#!/usr/bin/env python3
"""音乐会话与提示注入的契约测试（issue #9）。

契约（每一条都对应验收缝里可断言的那半）：

  1. 事件 → 状态：设备推的 ``music.session`` 通知让服务端知道「现在在放什么」，
     开始/换歌/暂停/继续/播完/中断/续播失败都改变状态；
  2. 幂等：同一事件重发不产生重复状态（重连、重试不改变事实）；
  3. 暂停期间位点冻结、在播期间位点推进并 clamp 到总长——「暂停期间位点虚涨」
     正是这条通道要防的那个错；
  4. 直播流不报位点（估算与注入文本里都没有数字）；
  5. 注入文本永远是最新状态（换歌后再取就是新曲目）、只有一块、不累积；
  6. 占位符替换：模板没有 ``<music_status>`` 时 no-op（关闭音乐功能完全无害），
     注入为空时整块摘掉，替换可重复执行（每次调用前重新展开）。

运行：python3 -m unittest discover -s server/tests -t . -v
（纯逻辑测试：只吃事件字典，不起服务端、不连设备、不发 LLM 请求。）
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.utils import music_session as ms  # noqa: E402


def event(event="started", state="playing", title="晴天", author="周杰伦",
          form="finite", **extra):
    params = {"event": event, "state": state, "title": title, "author": author,
              "form": form}
    params.update(extra)
    return params


class FakeClock:
    """可推进的挂钟：位点估算的判据是「事件之后流逝了多久」。"""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class EventToState(unittest.TestCase):
    """事件让服务端知道「现在在放什么」。"""

    def test_no_state_before_any_event(self):
        session = ms.MusicSession()
        self.assertFalse(session.has_state)
        self.assertIsNone(session.prompt())

    def test_started_creates_session_with_track_and_author(self):
        session = ms.MusicSession()
        self.assertTrue(session.apply_event(event(position_s=0, duration_s=269)))
        prompt = session.prompt()
        self.assertEqual(prompt.state, "playing")
        self.assertEqual(prompt.title, "晴天")
        self.assertEqual(prompt.author, "周杰伦")
        self.assertIn("晴天", prompt.text)
        self.assertIn("周杰伦", prompt.text)

    def test_pause_kinds_are_distinguishable(self):
        """两种暂停的措辞必须不同——用户问「暂停了吗」要答得出是哪一种。"""
        session = ms.MusicSession()
        session.apply_event(event(event="paused", state="paused_conversation",
                                  position_s=30))
        conversation = session.prompt().text
        session.apply_event(event(event="paused", state="paused_user", position_s=30))
        user = session.prompt().text
        self.assertIn("会话性暂停", conversation)
        self.assertIn("用户暂停", user)
        self.assertNotEqual(conversation, user)

    def test_resumed_goes_back_to_playing(self):
        session = ms.MusicSession()
        session.apply_event(event(event="paused", state="paused_user", position_s=30))
        session.apply_event(event(event="resumed", state="playing", position_s=30))
        prompt = session.prompt()
        self.assertEqual(prompt.state, "playing")
        self.assertIn("正在播放", prompt.text)

    def test_song_change_reports_the_new_track(self):
        """换歌后再问，答的必须是新曲目（最新状态，不是会话开始时的快照）。"""
        session = ms.MusicSession()
        session.apply_event(event(title="晴天", author="周杰伦", position_s=0,
                                  duration_s=269))
        session.apply_event(event(title="稻香", author="周杰伦", position_s=0,
                                  duration_s=223))
        prompt = session.prompt()
        self.assertEqual(prompt.title, "稻香")
        self.assertIn("稻香", prompt.text)
        self.assertNotIn("晴天", prompt.text)

    def test_terminal_states_say_nothing_is_playing(self):
        for state in ("completed", "interrupted", "resume_failed", "stopped"):
            with self.subTest(state=state):
                session = ms.MusicSession()
                session.apply_event(event(title="晴天", author="周杰伦",
                                          position_s=269, duration_s=269))
                session.apply_event(event(event=state, state=state, title="晴天",
                                          author="周杰伦", position_s=269))
                prompt = session.prompt()
                self.assertEqual(prompt.state, state)
                self.assertIn("没有音乐在播放", prompt.text)
                self.assertIn("晴天", prompt.text)

    def test_start_failed_has_no_playing_state(self):
        session = ms.MusicSession()
        session.apply_event(event(event="start_failed", state="start_failed",
                                  title="不存在", author=""))
        self.assertIn("起播失败", session.prompt().text)

    def test_state_wins_over_event_and_unknown_events_are_ignored(self):
        """state 是权威字段；两个都不认识的输入整条忽略，不猜。"""
        session = ms.MusicSession()
        self.assertFalse(session.apply_event({"event": "wat"}))
        self.assertFalse(session.has_state)
        self.assertTrue(session.apply_event({"event": "started", "state": "paused_user",
                                            "title": "晴天"}))
        self.assertEqual(session.prompt().state, "paused_user")

    def test_event_defaults_to_state_when_event_missing(self):
        session = ms.MusicSession()
        self.assertTrue(session.apply_event({"state": "playing", "title": "晴天"}))
        self.assertEqual(session.prompt().event, "playing")

    def test_non_dict_params_are_ignored(self):
        session = ms.MusicSession()
        for params in (None, [], "started", 3):
            self.assertFalse(session.apply_event(params))
        self.assertFalse(session.has_state)


class Idempotency(unittest.TestCase):
    """重复推送同一事件不产生重复状态（验收缝）。"""

    def test_same_event_twice_keeps_one_state(self):
        session = ms.MusicSession()
        payload = event(position_s=0, duration_s=269)
        self.assertTrue(session.apply_event(payload))
        self.assertFalse(session.apply_event(payload))
        self.assertEqual(session.applied_events, 1)
        self.assertEqual(session.duplicate_events, 1)
        self.assertEqual(session.prompt().title, "晴天")

    def test_duplicate_does_not_restart_the_position_clock(self):
        """重发不是「重新开始」：位点基准与 updated_at 都不动。"""
        clock = FakeClock()
        session = ms.MusicSession(clock=clock)
        session.apply_event(event(position_s=10, duration_s=269))
        clock.advance(5)
        self.assertAlmostEqual(session.estimate_position(), 15.0)
        session.apply_event(event(position_s=10, duration_s=269))
        self.assertAlmostEqual(session.estimate_position(), 15.0)
        self.assertEqual(session.duplicate_events, 1)

    def test_pause_resume_pairs_do_not_accumulate(self):
        """暂停/继续是高频动作：重发同一侧的条目只算一次，但真的换态要认。

        暂停 → 继续 是**真**的状态变更（各算一次）；只有把同一侧的条目重复
        推送（设备重试、重连重放）才是重复——那条通道不许攒出多份状态。
        """
        session = ms.MusicSession()
        paused = event(event="paused", state="paused_user", position_s=10)
        resumed = event(event="resumed", state="playing", position_s=10)
        session.apply_event(paused)
        session.apply_event(paused)
        session.apply_event(resumed)
        session.apply_event(resumed)
        self.assertEqual(session.applied_events, 2)
        self.assertEqual(session.duplicate_events, 2)
        self.assertEqual(session.prompt().state, "playing")

    def test_repeated_pause_after_resume_is_not_a_duplicate(self):
        """暂停 → 继续 → 再暂停 是三个不同状态，一次都不能丢。"""
        session = ms.MusicSession()
        session.apply_event(event(event="paused", state="paused_user", position_s=10))
        session.apply_event(event(event="resumed", state="playing", position_s=10))
        session.apply_event(event(event="paused", state="paused_user", position_s=10))
        self.assertEqual(session.applied_events, 3)
        self.assertEqual(session.duplicate_events, 0)
        self.assertEqual(session.prompt().state, "paused_user")


class PositionEstimate(unittest.TestCase):
    """位点估算：在播推进、暂停冻结、直播不报。"""

    def test_playing_advances_with_the_clock(self):
        clock = FakeClock()
        session = ms.MusicSession(clock=clock)
        session.apply_event(event(position_s=60, duration_s=269))
        clock.advance(3.0)
        self.assertAlmostEqual(session.estimate_position(), 63.0)

    def test_position_clamps_to_duration(self):
        clock = FakeClock()
        session = ms.MusicSession(clock=clock)
        session.apply_event(event(position_s=265, duration_s=269))
        clock.advance(60)
        self.assertAlmostEqual(session.estimate_position(), 269.0)

    def test_pause_freezes_the_position(self):
        clock = FakeClock()
        session = ms.MusicSession(clock=clock)
        session.apply_event(event(position_s=60, duration_s=269))
        clock.advance(2.0)
        session.apply_event(event(event="paused", state="paused_user", position_s=62,
                                  duration_s=269))
        clock.advance(30)
        self.assertAlmostEqual(session.estimate_position(), 62.0)

    def test_terminal_state_freezes_the_position(self):
        clock = FakeClock()
        session = ms.MusicSession(clock=clock)
        session.apply_event(event(event="completed", state="completed", position_s=269,
                                  duration_s=269))
        clock.advance(10)
        self.assertAlmostEqual(session.estimate_position(), 269.0)

    def test_live_stream_has_no_position(self):
        """直播流的「位点」是无意义的数字，注入内容里一个数字都不能有。"""
        clock = FakeClock()
        session = ms.MusicSession(clock=clock)
        session.apply_event(event(title="Jazz24", author="", form="live",
                                  position_s=999, duration_s=3600))
        clock.advance(5)
        self.assertIsNone(session.estimate_position())
        prompt = session.prompt()
        self.assertTrue(prompt.live)
        self.assertIsNone(prompt.position_s)
        self.assertEqual(prompt.duration_s, 0)
        self.assertIn("直播流", prompt.text)
        self.assertNotIn("已播放", prompt.text)

    def test_missing_position_is_not_a_zero(self):
        """不知道位点 ≠ 位点是 0：带着「已播放 0:00」就是撒谎。"""
        session = ms.MusicSession()
        session.apply_event(event(duration_s=269))
        prompt = session.prompt()
        self.assertIsNone(prompt.position_s)
        self.assertNotIn("已播放", prompt.text)
        self.assertIn("总长 4:29", prompt.text)

    def test_bad_position_values_are_dropped(self):
        for value in ("60", float("nan"), float("inf"), -5, True, None, {"a": 1}):
            with self.subTest(value=value):
                session = ms.MusicSession()
                session.apply_event(event(position_s=value))
                self.assertIsNone(session.prompt().position_s)

    def test_clock_format(self):
        self.assertEqual(ms.format_clock(0), "0:00")
        self.assertEqual(ms.format_clock(83.4), "1:23")
        self.assertEqual(ms.format_clock(269), "4:29")
        self.assertEqual(ms.format_clock(3661), "1:01:01")


class PromptPlaceholder(unittest.TestCase):
    """``<music_status>`` 占位符：每轮展开、不累积、没有它也不报错。"""

    TEMPLATE = "<memory>\n</memory>\n<music_status>\n</music_status>\n<tail/>"

    def test_template_without_placeholder_is_untouched(self):
        """用户自定义提示词里没有这个块 —— 注入是 no-op，不是报错。"""
        plain = "<memory>\n</memory>"
        self.assertEqual(ms.apply_prompt_placeholder(plain, "正在播放：晴天"), plain)

    def test_none_status_leaves_the_template_alone(self):
        self.assertEqual(ms.apply_prompt_placeholder(self.TEMPLATE, None), self.TEMPLATE)

    def test_empty_status_removes_the_block(self):
        """关闭音乐功能 / 未启用：整块摘掉，模板里不留空壳。"""
        result = ms.apply_prompt_placeholder(self.TEMPLATE, "")
        self.assertNotIn("<music_status>", result)
        self.assertIn("<memory>", result)
        self.assertIn("<tail/>", result)

    def test_status_is_wrapped_in_the_tag(self):
        result = ms.apply_prompt_placeholder(self.TEMPLATE, "现在正在播放：晴天")
        self.assertIn("<music_status>\n现在正在播放：晴天\n</music_status>", result)

    def test_replacement_does_not_accumulate(self):
        """每次调用前重新展开：块永远只有一个，内容永远是最新那次。"""
        once = ms.apply_prompt_placeholder(self.TEMPLATE, "第一次")
        twice = ms.apply_prompt_placeholder(once, "第二次")
        self.assertEqual(twice.count("<music_status>"), 1)
        self.assertIn("第二次", twice)
        self.assertNotIn("第一次", twice)

    def test_live_injection_has_no_clock(self):
        session = ms.MusicSession()
        session.apply_event(event(title="Jazz24", author="", form="live"))
        result = ms.apply_prompt_placeholder(self.TEMPLATE, session.prompt().text)
        self.assertIn("Jazz24", result)
        self.assertNotRegex(result, r"\d:\d\d")

    def test_placeholder_at_start_of_template_is_replaced_too(self):
        template = "<music_status>\n</music_status>\nbody"
        result = ms.apply_prompt_placeholder(template, "正在播放：晴天")
        self.assertTrue(result.startswith("<music_status>"))
        self.assertIn("body", result)


class InjectionIsReadOnly(unittest.TestCase):
    """注入是只读的：一次调用只改变提示文本，不产生任何副作用。"""

    def test_prompt_does_not_mutate_the_session(self):
        clock = FakeClock()
        session = ms.MusicSession(clock=clock)
        session.apply_event(event(position_s=1, duration_s=269))
        for _ in range(5):
            session.prompt()
            clock.advance(1)
        self.assertEqual(session.applied_events, 1)
        self.assertEqual(session.duplicate_events, 0)
        # 重复渲染同一次状态得到的文本逐字节相同（只随时间变化的位点除外）。
        self.assertEqual(session.prompt().text, session.prompt().text)

    def test_prompt_carries_the_read_only_rule(self):
        session = ms.MusicSession()
        session.apply_event(event())
        self.assertIn("不要主动播报", session.prompt().text)


class TrackLevelHistoryEntries(unittest.TestCase):
    """曲目级事件 → 历史条目（issue #10 的纯逻辑那一半）。

    分类与措辞单列在这里测（不起连接、不走 MCP 入口）：它是本票最容易悄悄
    写错的地方——把暂停也一起写进去、把四种收场写成同一条、把「换歌」写成
    就地改写。端到端那条缝在 ``test_music_history_seam.py``。
    """

    def test_track_level_events_render_a_named_entry(self):
        cases = {
            "started": "开始播放",
            "completed": "播完",
            "interrupted": "中断",
            "resume_failed": "续播",
        }
        for event_name, keyword in cases.items():
            with self.subTest(event=event_name):
                entry = ms.format_history_entry(event_name, "晴天", "周杰伦")
                self.assertIsNotNone(entry)
                self.assertIn("晴天", entry)
                self.assertIn(keyword, entry)
                self.assertIn(ms.HISTORY_TAG, entry)

    def test_terminal_entries_are_pairwise_distinct(self):
        """播完 / 中断 / 续播失败各有一条，且两者两两可区分。"""
        rendered = [ms.format_history_entry(name, "晴天", "周杰伦")
                    for name in ("completed", "interrupted", "resume_failed")]
        self.assertEqual(len(set(rendered)), 3)

    def test_pause_resume_stop_and_start_failed_write_nothing(self):
        """集合外的事件一律 None：暂停/继续（防历史被挤爆）与 issue 未列举的两种。"""
        for event_name in ("paused", "resumed", "stopped", "start_failed",
                          "", "wat", None):
            with self.subTest(event=event_name):
                self.assertIsNone(ms.format_history_entry(event_name, "晴天", ""))

    def test_track_level_set_is_exactly_the_issues_list(self):
        """曲目级集合**恰好**是 issue 列举的那几个——多一个少一个都算缺陷。

        这条以前是「集合与措辞表同步」，现在集合由措辞表推导（``frozenset(
        _HISTORY_TEMPLATES)``），同步性已由语法保证，再断言就是恒真。改钉**内容**：
        多一个事件进来（例如有人把 ``paused`` 加进模板）会让「暂停淹没历史」
        这个被本 spec 明确防住的事故复活，而它是 issue 正文的硬约束。
        """
        self.assertEqual(ms.TRACK_LEVEL_EVENTS,
                         frozenset({"started", "completed", "interrupted",
                                    "resume_failed"}))
        # 被明确排除的两个：暂停与继续（每轮一对，会挤爆历史窗口）。
        for excluded in ("paused", "resumed"):
            self.assertNotIn(excluded, ms.TRACK_LEVEL_EVENTS)
        # 别名必须指向集合内的事件（否则别名等于死代码）。
        for alias, canonical in ms._HISTORY_ALIASES.items():
            self.assertIn(canonical, ms.TRACK_LEVEL_EVENTS)
            self.assertNotIn(alias, ms.TRACK_LEVEL_EVENTS)

    def test_track_text_falls_back_when_author_is_missing(self):
        self.assertIn("晴天", ms.format_history_entry("started", "晴天", ""))
        entry = ms.format_history_entry("started", "", "")
        self.assertIn("未知曲目", entry)

    def test_history_entry_reads_the_authoritative_event(self):
        """状态机只给 ``state`` 时事件名由 _parse 兜底，历史分类跟着它走。

        判别力：拿原始 ``params`` 判分类会与状态机分叉——设备只给
        ``state=playing``时，原始字段里没有 ``event``，拿它判会误归成非曲目级。
        """
        session = ms.MusicSession()
        session.apply_event({"state": "playing", "title": "晴天"})
        entry = session.history_entry()
        self.assertIsNotNone(entry)
        self.assertIn("开始播放", entry)

    def test_history_entry_tracks_the_latest_track(self):
        session = ms.MusicSession()
        session.apply_event(event(title="晴天"))
        session.apply_event(event(title="稻香"))
        self.assertIn("稻香", session.history_entry())
        self.assertNotIn("晴天", session.history_entry())

    def test_history_entry_is_none_before_any_event(self):
        self.assertIsNone(ms.MusicSession().history_entry())

    def test_history_entry_is_none_while_paused(self):
        """最后的合法事件是暂停 → 没有可写入的条目（不是“把上一条再写一遍”）。"""
        session = ms.MusicSession()
        session.apply_event(event())
        session.apply_event(event(event="paused", state="paused_user"))
        self.assertIsNone(session.history_entry())

    def test_history_entry_does_not_touch_idempotency_bookkeeping(self):
        """取历史条目是只读的：不推进 applied/duplicate 记账。"""
        session = ms.MusicSession()
        session.apply_event(event())
        for _ in range(5):
            session.history_entry()
        self.assertEqual(session.applied_events, 1)
        self.assertEqual(session.duplicate_events, 0)


if __name__ == "__main__":
    unittest.main()
