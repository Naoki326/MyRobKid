#!/usr/bin/env python3
"""屏幕出口断言的契约测试（issue #8）。

契约：serial_telemetry.py 除了位点/暂停/收场之外，还要能断言**屏幕被设成了
什么**——「显示对没对」不靠人眼。锚点是应用侧的行内遥测：

    Music screen: action=now-playing seq=7 owns=on idle_gen=1 device=idle
                  title='晴天' author='周杰伦' form=finite duration=269s
                  total=4:29 text='正在播放：晴天 · 周杰伦 · 4:29'

四条屏幕断言：
  1) screen_shows_track            播放中的文本含曲目与作者；
  2) screen_updates_on_song_change 一次抓取连播两首时，第二首的文本是新曲目
                                   （不是顶着上一首），且它晚于第二条起流锚点；
  3) screen_set_after_state_change 曲目文本晚于它那一次 `State: … -> idle`
                                   （idle 分支的清屏在前、写曲目在后）；
  4) live_screen_has_no_total      直播流的 form/total 都是 live/none，且文本里
                                   没有 `m:ss`；
  5) ended_screen_has_no_stale_track 收场之后屏幕上再没有旧曲目名；参与反馈
                                   的原因必须有一条 end-state 写屏（不留陈旧曲目）。

issue #11 续增四条（暂停态屏幕）：
   6) pause_screen_distinguishes_kinds 两种暂停（会话性/用户）的文本不同；
   7) paused_screen_shows_position     暂停写屏带位点、播放写屏不带；
   8) live_screen_has_no_position      直播写屏无数字位点；
   9) paused_position_matches_truth    屏幕位点与暂停锚点一致（±2s）。

运行：server/.venv/bin/python -m unittest tools.tests.test_screen_telemetry -v
（纯逻辑测试，不碰串口、不建设备模拟器。）
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import serial_telemetry  # noqa: E402


def screen_line(action="now-playing", text="正在播放：晴天 · 周杰伦 · 4:29",
                title="晴天", author="周杰伦", form="finite", duration=269,
                total="4:29", device="idle", seq=7, owns="on", idle_gen=1,
                state="playing", pos="none"):
    return (f"I (700) Application: Music screen: action={action} seq={seq} "
            f"owns={owns} idle_gen={idle_gen} device={device} title='{title}' "
            f"author='{author}' form={form} duration={duration}s total={total} "
            f"state={state} pos={pos} text='{text}'")


def pause_line(kind="user", pos="72.0s"):
    return f"I (695) MusicPlayer: Music pause: kind={kind} pos={pos}"


def state_line(old="speaking", new="idle"):
    return f"I (699) StateMachine: State: {old} -> {new}"


class ParseScreenLine(unittest.TestCase):
    """屏幕锚点行解析。"""

    def test_now_playing_fields(self):
        parsed = serial_telemetry.parse_screen_line(screen_line())
        self.assertEqual(parsed["action"], "now-playing")
        self.assertEqual(parsed["text"], "正在播放：晴天 · 周杰伦 · 4:29")
        self.assertEqual(parsed["title"], "晴天")
        self.assertEqual(parsed["author"], "周杰伦")
        self.assertEqual(parsed["form"], "finite")
        self.assertEqual(parsed["duration_s"], 269)
        self.assertEqual(parsed["total"], "4:29")
        self.assertEqual(parsed["device"], "idle")
        self.assertEqual(parsed["seq"], 7)
        # issue #11：与写屏同时报的呈现态与锚点口径位点。
        self.assertEqual(parsed["state"], "playing")
        self.assertEqual(parsed["pos"], "none")

    def test_end_state_has_empty_track_fields(self):
        """结束态没有曲目字段（那是「不留陈旧曲目」的形状）。"""
        parsed = serial_telemetry.parse_screen_line(
            screen_line(action="end-state", text="播放结束", title="", author="",
                        form="none", duration=0, total="none", owns="off"))
        self.assertEqual(parsed["action"], "end-state")
        self.assertEqual(parsed["text"], "播放结束")
        self.assertEqual(parsed["title"], "")
        self.assertEqual(parsed["total"], "none")

    def test_live_screen(self):
        parsed = serial_telemetry.parse_screen_line(
            screen_line(title="Jazz Radio", author="WDR", form="live", duration=0,
                        total="none", text="正在播放：Jazz Radio · WDR"))
        self.assertEqual(parsed["form"], "live")
        self.assertEqual(parsed["total"], "none")

    def test_non_screen_lines_return_none(self):
        self.assertIsNone(serial_telemetry.parse_screen_line(
            "I (1) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s pushed=0 "
            "fail=0 pos=1s"))
        self.assertIsNone(serial_telemetry.parse_screen_line(state_line()))

    def test_apostrophes_inside_the_track_name_do_not_break_parsing(self):
        """曲目里带单引号（`Don't Stop`）时，后面的字段不能被串进去。"""
        parsed = serial_telemetry.parse_screen_line(
            screen_line(title="Don't Stop", author="O'Brien",
                        text="正在播放：Don't Stop · O'Brien · 4:29"))
        self.assertEqual(parsed["title"], "Don't Stop")
        self.assertEqual(parsed["author"], "O'Brien")
        self.assertEqual(parsed["total"], "4:29")
        self.assertTrue(parsed["text"].endswith("O'Brien · 4:29"))


class ScreenAssertions(unittest.TestCase):
    """抓取里的屏幕断言组。"""

    def _samples(self, lines):
        return [{"t": 1000.0 + i, "line": line} for i, line in enumerate(lines)]

    def test_healthy_now_playing_passes(self):
        samples = self._samples([
            state_line("speaking", "idle"),
            screen_line(),
            # 位点样本让抓取不是「没出声」的形状
            "I (600) MusicPlayer: Music stream started: title='晴天' "
            "author='周杰伦' duration=269s form=finite start=0s",
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=2s",
        ])
        results = serial_telemetry.evaluate_capture(samples)
        screen = [r for r in results if r.name.startswith("screen_")]
        self.assertTrue(screen, "屏幕断言组没跑起来")
        self.assertEqual([r.detail for r in screen if not r.ok], [])

    def test_missing_screen_line_fails(self):
        samples = self._samples([
            "I (600) MusicPlayer: Music stream started: title='晴天' "
            "author='周杰伦' duration=269s form=finite start=0s",
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=2s",
        ])
        results = serial_telemetry.evaluate_capture(samples, expect_screen=True)
        shows = [r for r in results if r.name == "screen_shows_track"][0]
        self.assertFalse(shows.ok)

    def test_screen_written_before_the_state_change_fails(self):
        """写屏早于 `State: … -> idle` = 会被 idle 分支的清屏吃掉（issue #8 的核心）。"""
        samples = self._samples([
            "I (600) MusicPlayer: Music stream started: title='晴天' "
            "author='周杰伦' duration=269s form=finite start=0s",
            screen_line(),
            state_line("speaking", "idle"),
        ])
        results = serial_telemetry.evaluate_capture(samples)
        order = [r for r in results if r.name == "screen_set_after_state_change"][0]
        self.assertFalse(order.ok)

    def test_song_change_updates_the_screen(self):
        """换歌后屏幕上必须是新曲目，且晚于第二条起流锚点。

        真机次序：起流锚点在 worker 里、首帧解出时打；写屏在 Start() 返回后由主
        循环做——每首的写屏都落在
        「它自己的锚点之后、下一首的锚点之前」。
        """
        samples = self._samples([
            state_line("speaking", "idle"),
            "I (600) MusicPlayer: Music stream started: title='晴天' "
            "author='周杰伦' duration=269s form=finite start=0s",
            screen_line(title="晴天", text="正在播放：晴天 · 周杰伦 · 4:29"),
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=2s",
            "I (602) MusicPlayer: Music stream started: title='稻香' "
            "author='周杰伦' duration=223s form=finite start=0s",
            screen_line(title="稻香", text="正在播放：稻香 · 周杰伦 · 3:43",
                        duration=223, total="3:43", seq=8),
            "I (603) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=1s",
        ])
        results = serial_telemetry.evaluate_capture(samples)
        change = [r for r in results if r.name == "screen_updates_on_song_change"][0]
        self.assertTrue(change.ok, change.detail)
        self.assertIn("均有各自的曲目写屏", change.detail)

    def test_song_change_without_screen_update_fails(self):
        samples = self._samples([
            "I (600) MusicPlayer: Music stream started: title='晴天' "
            "author='周杰伦' duration=269s form=finite start=0s",
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=2s",
            "I (602) MusicPlayer: Music stream started: title='稻香' "
            "author='周杰伦' duration=223s form=finite start=0s",
            "I (603) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=1s",
        ])
        results = serial_telemetry.evaluate_capture(samples, expect_screen=True)
        change = [r for r in results if r.name == "screen_updates_on_song_change"][0]
        self.assertFalse(change.ok)

    def test_single_song_capture_reports_not_applicable(self):
        """只播了一首时换歌断言无从谈起：如实报「不适用」，不当回归。"""
        samples = self._samples([
            state_line("speaking", "idle"),
            "I (600) MusicPlayer: Music stream started: title='晴天' "
            "author='周杰伦' duration=269s form=finite start=0s",
            screen_line(),
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=2s",
        ])
        results = serial_telemetry.evaluate_capture(samples)
        change = [r for r in results if r.name == "screen_updates_on_song_change"][0]
        self.assertTrue(change.ok)
        self.assertIn("不适用", change.detail)

    def test_live_screen_shows_no_total(self):
        live = ("I (600) MusicPlayer: Music stream started: title='Jazz Radio' "
                "author='WDR' duration=0s form=live start=0s")
        samples = self._samples([
            live,
            screen_line(title="Jazz Radio", author="WDR", form="live", duration=0,
                        total="none", text="正在播放：Jazz Radio · WDR"),
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=live",
        ])
        results = serial_telemetry.evaluate_capture(samples)
        total = [r for r in results if r.name == "live_screen_has_no_total"][0]
        self.assertTrue(total.ok)

    def test_live_screen_with_a_total_fails(self):
        """直播流上写了个总量（`4:29`）：那是撒谎，断言必须逮住。"""
        live = ("I (600) MusicPlayer: Music stream started: title='Jazz Radio' "
                "author='WDR' duration=0s form=live start=0s")
        samples = self._samples([
            live,
            screen_line(title="Jazz Radio", author="WDR", form="live", duration=0,
                        total="4:29", text="正在播放：Jazz Radio · WDR · 4:29"),
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=live",
        ])
        results = serial_telemetry.evaluate_capture(samples)
        total = [r for r in results if r.name == "live_screen_has_no_total"][0]
        self.assertFalse(total.ok)

    def test_ended_screen_leaves_no_stale_track(self):
        samples = self._samples([
            "I (600) MusicPlayer: Music stream started: title='晴天' "
            "author='周杰伦' duration=269s form=finite start=0s",
            screen_line(text="正在播放：晴天 · 周杰伦 · 4:29"),
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=2s",
            "I (602) MusicPlayer: Music ended: reason=completed played=1 "
            "pos=269.0s url=http://h/stream",
            "I (603) Application: Music feedback: reason=completed pos=269.0s "
            "sound=success screen=ended wake_word=on interactive=scheduled",
            screen_line(action="end-state", text="播放结束", title="", author="",
                        form="none", duration=0, total="none", owns="off", seq=9),
        ])
        results = serial_telemetry.evaluate_capture(samples)
        stale = [r for r in results if r.name == "ended_screen_has_no_stale_track"][0]
        self.assertTrue(stale.ok)

    def test_ended_without_a_screen_write_fails(self):
        """收场必须给出结束态：屏幕不该继续留着已经过时的曲目名。"""
        samples = self._samples([
            "I (600) MusicPlayer: Music stream started: title='晴天' "
            "author='周杰伦' duration=269s form=finite start=0s",
            screen_line(text="正在播放：晴天 · 周杰伦 · 4:29"),
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=2s",
            "I (602) MusicPlayer: Music ended: reason=completed played=1 "
            "pos=269.0s url=http://h/stream",
            "I (603) Application: Music feedback: reason=completed pos=269.0s "
            "sound=success screen=ended wake_word=on interactive=scheduled",
        ])
        results = serial_telemetry.evaluate_capture(samples)
        stale = [r for r in results if r.name == "ended_screen_has_no_stale_track"][0]
        self.assertFalse(stale.ok)

    def test_idle_transition_without_any_later_write_fails(self):
        """抓到了 `-> idle` 却没有任何写屏在它之后：要么写屏被清掉，要么根本没写。"""
        samples = self._samples([
            state_line("speaking", "idle"),
            "I (600) MusicPlayer: Music stream started: title='晴天' "
            "author='周杰伦' duration=269s form=finite start=0s",
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=2s",
        ])
        results = serial_telemetry.evaluate_capture(samples, expect_screen=True)
        order = [r for r in results if r.name == "screen_set_after_state_change"][0]
        self.assertFalse(order.ok)

    def test_no_transition_at_all_reports_not_applicable(self):
        """抓取始于起流那一刻（无 `-> idle`）：转态时序无从比对，如实报不适用。"""
        samples = self._samples([
            "I (600) MusicPlayer: Music stream started: title='晴天' "
            "author='周杰伦' duration=269s form=finite start=0s",
            screen_line(),
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=2s",
        ])
        results = serial_telemetry.evaluate_capture(samples)
        order = [r for r in results if r.name == "screen_set_after_state_change"][0]
        self.assertTrue(order.ok)
        self.assertIn("无从比对", order.detail)

    def test_transition_missing_from_a_long_window_fails(self):
        """抓取盖住了起播之前的窗口，却一条转移行都没有：时序无凭，不当通过。"""
        samples = self._samples([
            "I (100) Wifi: connected",
            "I (600) MusicPlayer: Music stream started: title='晴天' "
            "author='周杰伦' duration=269s form=finite start=0s",
            screen_line(),
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=2s",
        ])
        results = serial_telemetry.evaluate_capture(samples)
        order = [r for r in results if r.name == "screen_set_after_state_change"][0]
        self.assertFalse(order.ok)

    def test_idle_repaint_keeps_the_track(self):
        """idle 分支的重画（`action=repaint`）本身就是「曲目活过了清屏」的证据。"""
        samples = self._samples([
            state_line("speaking", "idle"),
            screen_line(action="repaint", text="正在播放：晴天 · 周杰伦 · 4:29"),
            "I (600) MusicPlayer: Music stream started: title='晴天' "
            "author='周杰伦' duration=269s form=finite start=0s",
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=2s",
        ])
        results = serial_telemetry.evaluate_capture(samples, expect_screen=True)
        shows = [r for r in results if r.name == "screen_shows_track"][0]
        self.assertTrue(shows.ok)
        self.assertIn("repaint", shows.detail)

    def test_no_screen_lines_means_no_screen_assertions(self):
        """旧固件的抓取（没有屏幕锚点，也没有任何音乐痕迹）不该凭空多断言。"""
        samples = self._samples([
            "I (600) MusicPlayer: Music stream started: title='晴天' "
            "author='周杰伦' duration=269s form=finite start=0s",
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=2s",
        ])
        results = serial_telemetry.evaluate_capture(samples, expect_screen=False)
        self.assertEqual([r for r in results if r.name == "screen_shows_track"], [])


class PausedScreenAssertions(unittest.TestCase):
    """暂停态屏幕断言（issue #11）：位点与两种暂停可区分。

    本组不碰串口：用假的锚点行构造一次抓取，看断言在真错下会不会失败。判别力
    比断言本身更重要——工单点名要求「断言能抓住两种暂停文案被写成一样与直播流
    显示了位点这两个真错」，所以这两个真错各有一条测试直接把它写出来。
    """

    PAUSED_CONV = "已暂停（说完自动继续）：晴天 · 周杰伦 · 1:12"
    PAUSED_USER = "已暂停（说“继续”恢复）：晴天 · 周杰伦 · 1:12"
    NOW_PLAYING = "正在播放：晴天 · 周杰伦 · 4:29"

    def _samples(self, lines):
        return [{"t": 1000.0 + i, "line": line} for i, line in enumerate(lines)]

    def _results(self, lines, **kwargs):
        return serial_telemetry.evaluate_capture(self._samples(lines), **kwargs)

    def _assertion(self, results, name):
        hits = [r for r in results if r.name == name]
        self.assertTrue(hits, f"未出现断言 {name}")
        return hits[0]

    def _healthy_pause_capture(self):
        """一次覆盖「播放 → 会话性暂停 → 用户暂停 → 恢复」的抓取。"""
        return [
            state_line("speaking", "idle"),
            screen_line(text=self.NOW_PLAYING, state="playing", pos="none",
                        total="4:29"),
            "I (601) MusicPlayer: Music stream started: title='晴天' "
            "author='周杰伦' duration=269s form=finite start=0s",
            "I (602) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=2s",
            pause_line("conversation", "72.0s"),
            screen_line(action="paused", text=self.PAUSED_CONV,
                        state="paused_conversation", pos="72s", total="none",
                        seq=8),
            pause_line("user", "73.0s"),
            screen_line(action="paused", text=self.PAUSED_USER,
                        state="paused_user", pos="73s", total="none", seq=9),
            state_line("idle", "idle"),
            screen_line(action="now-playing", text=self.NOW_PLAYING,
                        state="playing", pos="none", total="4:29", seq=10),
        ]

    def test_healthy_pause_capture_passes(self):
        results = self._results(self._healthy_pause_capture())
        for name in ("pause_screen_distinguishes_kinds",
                     "paused_screen_shows_position",
                     "paused_position_matches_truth"):
            with self.subTest(name=name):
                self.assertTrue(self._assertion(results, name).ok,
                                self._assertion(results, name).detail)

    def test_two_pause_kinds_written_identically_fails(self):
        """真错一：两种暂停写成同一句（忘了按 state 挑前缀）——必须失败。

        只断言「文本含曲目」的测试看不见这个错，而它正是本票要防的：用户分不出
        自己该等还是该说继续。"""
        lines = self._healthy_pause_capture()
        # 把用户暂停的文本改成与会话性暂停一模一样。
        lines = [ln.replace(self.PAUSED_USER, self.PAUSED_CONV) for ln in lines]
        results = self._results(lines)
        distinct = self._assertion(results, "pause_screen_distinguishes_kinds")
        self.assertFalse(distinct.ok)
        self.assertIn("同一个文本", distinct.detail)

    def test_paused_without_position_fails(self):
        """真错二（反向）：暂停了却不显示位点——用户看到的是「暂停」但没有「放到哪」。"""
        lines = self._healthy_pause_capture()
        lines = [ln.replace("pos=72s", "pos=none").replace("pos=73s", "pos=none")
                 for ln in lines]
        results = self._results(lines)
        shows = self._assertion(results, "paused_screen_shows_position")
        self.assertFalse(shows.ok)

    def test_playing_screen_with_a_position_fails(self):
        """真错三：播放中反而显示了位点（位点与总量在屏幕上应是互斥的两态）。"""
        lines = self._healthy_pause_capture()
        lines = [ln.replace("state=playing pos=none", "state=playing pos=12s")
                 for ln in lines]
        results = self._results(lines)
        shows = self._assertion(results, "paused_screen_shows_position")
        self.assertFalse(shows.ok)
        self.assertIn("播放写屏带了位点", shows.detail)

    def test_live_paused_screen_with_a_position_fails(self):
        """真错四：直播流显示了位点（issue 点名要抓的真错）。"""
        lines = [
            state_line("speaking", "idle"),
            "I (600) MusicPlayer: Music stream started: title='Jazz Radio' "
            "author='WDR' duration=0s form=live start=0s",
            screen_line(text="已暂停（说完自动继续）：Jazz Radio · WDR · 1:12",
                        title="Jazz Radio", author="WDR", form="live",
                        duration=0, total="none", state="paused_conversation",
                        pos="72s"),
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=live",
        ]
        results = self._results(lines)
        live = self._assertion(results, "live_screen_has_no_position")
        self.assertFalse(live.ok)
        self.assertIn("数字位点", live.detail)

    def test_live_paused_screen_without_position_passes(self):
        """直播流暂停不下数字位点、也不下总量——这是对的。"""
        lines = [
            state_line("speaking", "idle"),
            "I (600) MusicPlayer: Music stream started: title='Jazz Radio' "
            "author='WDR' duration=0s form=live start=0s",
            screen_line(text="已暂停（说完自动继续）：Jazz Radio · WDR",
                        title="Jazz Radio", author="WDR", form="live",
                        duration=0, total="none", state="paused_conversation",
                        pos="live"),
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=live",
        ]
        results = self._results(lines)
        self.assertTrue(self._assertion(results, "live_screen_has_no_position").ok)
        # 交叉检查（不是冗余）：直播流的暂停写屏本来就**不该**有位点，所以
        # ``paused_screen_shows_position`` 不能拿「暂停就必须有位点」去卡它。
        # 这两条断言同时成立才是对的；只断言前一条会漏掉「两条断言互相矛盾
        # 导致正确的屏幕被误报」这种缺陷。
        self.assertTrue(
            self._assertion(results, "paused_screen_shows_position").ok,
            "直播+暂停是正确屏幕，不该被「暂停必须有位点」误报："
            + self._assertion(results, "paused_screen_shows_position").detail)

    def test_screen_position_diverging_from_the_pause_anchor_fails(self):
        """真错五：屏幕显示了一个与设备自己记账不一致的位点（如拿总量冒充）。"""
        lines = self._healthy_pause_capture()
        # 屏幕写 72s，但暂停锚点其实冻结在 3s——差 69s，远超容差。
        lines = [ln.replace("kind=conversation pos=72.0s",
                            "kind=conversation pos=3.0s") for ln in lines]
        results = self._results(lines)
        truth = self._assertion(results, "paused_position_matches_truth")
        self.assertFalse(truth.ok)

    def test_pause_screen_without_any_pause_anchor_reports_not_applicable(self):
        """只抓到屏幕那一侧（无暂停锚点）时，位点真值核对如实报「不适用」。"""
        lines = [
            state_line("speaking", "idle"),
            screen_line(action="paused", text=self.PAUSED_CONV,
                        state="paused_conversation", pos="72s", total="none"),
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=2s",
        ]
        results = self._results(lines)
        truth = self._assertion(results, "paused_position_matches_truth")
        self.assertTrue(truth.ok)
        self.assertIn("不适用", truth.detail)

    def test_single_pause_kind_reports_not_applicable(self):
        """只出现一种暂停时，区分断言如实报「不适用」，不用必然失败算回归。"""
        lines = [
            state_line("speaking", "idle"),
            pause_line("conversation", "72.0s"),
            screen_line(action="paused", text=self.PAUSED_CONV,
                        state="paused_conversation", pos="72s", total="none"),
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=2s",
        ]
        results = self._results(lines)
        distinct = self._assertion(results, "pause_screen_distinguishes_kinds")
        self.assertTrue(distinct.ok)
        self.assertIn("不适用", distinct.detail)

    def test_no_pause_screen_lines_means_no_pause_assertions(self):
        """没按暂停的抓取（只验播放中屏幕）不该凭空多出**暂停类**断言。

        注意直播那条（`live_screen_has_no_position`）不在此列：它在没按暂停
        的抓取里也成立、也会跑（本次夹具是 form=finite 所以不出现）。
        """
        results = self._results([
            state_line("speaking", "idle"),
            screen_line(),
            "I (600) MusicPlayer: Music stream started: title='晴天' "
            "author='周杰伦' duration=269s form=finite start=0s",
            "I (601) MusicPlayer: pipe: ring=32/32 in_buf=0B read=0B/2s "
            "pushed=0 fail=0 pos=2s",
        ])
        for name in ("pause_screen_distinguishes_kinds",
                     "paused_screen_shows_position",
                     "live_screen_has_no_position",
                     "paused_position_matches_truth"):
            with self.subTest(name=name):
                self.assertEqual([r for r in results if r.name == name], [],
                                 f"未按暂停却出现了 {name}")


if __name__ == "__main__":
    unittest.main()
