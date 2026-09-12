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
                total="4:29", device="idle", seq=7, owns="on", idle_gen=1):
    return (f"I (700) Application: Music screen: action={action} seq={seq} "
            f"owns={owns} idle_gen={idle_gen} device={device} title='{title}' "
            f"author='{author}' form={form} duration={duration}s total={total} "
            f"text='{text}'")


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


if __name__ == "__main__":
    unittest.main()
