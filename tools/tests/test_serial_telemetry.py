#!/usr/bin/env python3
"""管线遥测位点断言测试（issue #3）。

契约：serial_telemetry.py 在抓取之外还要能「断言」——
  - pipe: 行带 pos= 字段：有限内容为整数秒（绝对位点：起点 + 已推帧），
    直播流为字面 live（不记位点）；
  - 「Music stream started:」行是起流锚点，带 title/author/duration/form/start；
  - 断言组 evaluate_capture 证明四件事：
      1) 位点单调不减（同一会话内不回退）；
      2) 位点下限不低于起点（带起点起流时是绝对值，不会回落成相对位点）；
      3) 位点不领先挂钟（上四分位偏差 ≤ 容差）——ring 预读（约 0.8s）若被计入
         位点，会稳定领先挂钟约 +0.8s，此断言即「只算推出帧」的串口佐证；
         用 q75 而非中位数：卡顿产生负偏差，不会误触；
      4) 位点总推进不落后挂钟太多（推进率 ≥ 0.7）——帧时长折算错误可被逮住；
  - 直播流场景：全部 pos=live，不出现数字位点。

运行：server/.venv/bin/python -m unittest tools/tests/test_serial_telemetry.py -v
（纯逻辑测试，不碰串口、不建设备模拟器。）
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import serial_telemetry  # noqa: E402


def pipe_line(pos, *, ring=32, ring_max=32, in_buf=4096, read=3000, pushed=76,
              fail=0, flags=""):
    flags_part = f" {flags}" if flags else ""
    pos_field = f"{pos}s" if isinstance(pos, int) else pos
    return (f"I (123) MusicPlayer: pipe: ring={ring}/{ring_max} in_buf={in_buf}B "
            f"read={read}B/2s pushed={pushed} fail={fail} pos={pos_field}{flags_part}")


def session_line(*, title="晴天", author="周杰伦", duration=269, form="finite",
                 start=0):
    return (f"I (124) MusicPlayer: Music stream started: title='{title}' "
            f"author='{author}' duration={duration}s form={form} start={start}s")


class ParsePipeLine(unittest.TestCase):
    """pipe: 行解析。"""

    def test_finite_position_is_integer_seconds(self):
        # 固件实际格式：pos=13s（带单位后缀，沿用管线遥测的字段风格）。
        parsed = serial_telemetry.parse_pipe_line(pipe_line(13))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["pos"], 13)
        self.assertEqual(parsed["pushed"], 76)
        self.assertEqual(parsed["fail"], 0)
        self.assertEqual(parsed["ring"], 32)
        self.assertEqual(parsed["ring_max"], 32)

    def test_real_device_line_shape(self):
        # 真机行（2026-09-12 验收抓取原样），单位后缀与 flags 混排都可解析。
        real = ("I (273579) MusicPlayer: pipe: ring=32/32 in_buf=0B "
                "read=16128B/2s pushed=83 fail=117 pos=13s")
        parsed = serial_telemetry.parse_pipe_line(real)
        self.assertEqual(parsed["pos"], 13)
        self.assertFalse(parsed["eof"])
        real_live = ("I (301579) MusicPlayer: pipe: ring=32/32 in_buf=12B "
                     "read=16128B/2s pushed=83 fail=117 pos=live")
        self.assertEqual(serial_telemetry.parse_pipe_line(real_live)["pos"], "live")

    def test_live_position_is_literal_marker(self):
        parsed = serial_telemetry.parse_pipe_line(pipe_line("live"))
        self.assertEqual(parsed["pos"], "live")

    def test_eof_and_decode_error_flags_are_kept(self):
        parsed = serial_telemetry.parse_pipe_line(pipe_line(7, flags="EOF"))
        self.assertTrue(parsed["eof"])
        self.assertEqual(parsed["pos"], 7)
        parsed = serial_telemetry.parse_pipe_line(pipe_line(8, flags="DECERR"))
        self.assertTrue(parsed["decode_error"])

    def test_non_pipe_line_returns_none(self):
        self.assertIsNone(serial_telemetry.parse_pipe_line("I (1) Wifi: connected"))
        self.assertIsNone(serial_telemetry.parse_pipe_line(""))
        self.assertIsNone(serial_telemetry.parse_pipe_line(
            "I (1) MusicPlayer: Music worker finished (completed): http://x"))


class ParseSessionLine(unittest.TestCase):
    """起流锚点行解析。"""

    def test_finite_session_fields(self):
        parsed = serial_telemetry.parse_session_line(session_line(start=30))
        self.assertEqual(parsed["title"], "晴天")
        self.assertEqual(parsed["author"], "周杰伦")
        self.assertEqual(parsed["duration_s"], 269)
        self.assertEqual(parsed["form"], "finite")
        self.assertEqual(parsed["start_s"], 30)

    def test_live_session(self):
        parsed = serial_telemetry.parse_session_line(
            session_line(title="Jazz Radio", author="", duration=0,
                         form="live", start=0))
        self.assertEqual(parsed["form"], "live")
        self.assertEqual(parsed["start_s"], 0)

    def test_non_session_line_returns_none(self):
        self.assertIsNone(serial_telemetry.parse_session_line(pipe_line("3")))


def samples_with_wall_clock(pairs, anchor_t=1000.0, start=0, form="finite"):
    """构造采样：pairs = [(pos, 偏移锚点的秒数), …]。

    起点写在锚点行里（设备自报口径）——断言按会话取锚点的 start=，
    CLI --start 只是没锚点时的兜底。
    """
    out = [{"t": anchor_t, "line": session_line(start=start, form=form)}]
    for pos, dt in pairs:
        out.append({"t": anchor_t + dt, "line": pipe_line(pos)})
    return out


class EvaluateFiniteCapture(unittest.TestCase):
    """有限内容断言组。"""

    def test_healthy_capture_passes(self):
        # 位点与挂钟同步：12s 窗口内从 2s 走到 12s。
        pairs = [(2, 2.0), (4, 4.1), (6, 6.0), (8, 8.2), (10, 10.1), (12, 12.0)]
        results = serial_telemetry.evaluate_capture(samples_with_wall_clock(pairs))
        failed = [r for r in results if not r.ok]
        self.assertEqual(failed, [])

    def test_ring_lead_fails_wall_clock_assertion(self):
        # 位点含 ring 预读：恒定领先挂钟约 +0.8s → 上四分位偏差越界。
        pairs = [(3, 2.0), (5, 4.1), (7, 6.0), (9, 8.2), (11, 10.1), (13, 12.0)]
        results = serial_telemetry.evaluate_capture(samples_with_wall_clock(pairs))
        track = [r for r in results if r.name == "position_tracks_wall_clock"][0]
        self.assertFalse(track.ok)

    def test_start_offset_absolute_position_passes(self):
        # 起点 30s（锚点自报）：位点从 30s 起算且随挂钟推进（绝对位点）。
        pairs = [(32, 2.0), (34, 4.1), (36, 6.0), (38, 8.1), (40, 10.0)]
        results = serial_telemetry.evaluate_capture(
            samples_with_wall_clock(pairs, start=30))
        failed = [r for r in results if not r.ok]
        self.assertEqual(failed, [])

    def test_relative_position_fails_floor_when_start_given(self):
        # 位点忘了加起点（相对位点）：锚点声明 start=30，位点却只有 2s 起。
        pairs = [(2, 2.0), (4, 4.1), (6, 6.0), (8, 8.1)]
        results = serial_telemetry.evaluate_capture(
            samples_with_wall_clock(pairs, start=30))
        floor = [r for r in results if r.name == "position_not_below_start"][0]
        self.assertFalse(floor.ok)

    def test_cli_start_mismatching_anchor_is_reported(self):
        # 调用方传了 --start 却与设备自报的锚点不一致：单独报一条，不静默放行。
        pairs = [(30, 0.0), (32, 2.0), (34, 4.0)]
        results = serial_telemetry.evaluate_capture(
            samples_with_wall_clock(pairs, start=0), start_s=30)
        cli = [r for r in results if r.name == "cli_matches_anchor"][0]
        self.assertFalse(cli.ok)

    def test_cli_start_matching_anchor_passes(self):
        pairs = [(30, 0.0), (32, 2.0), (34, 4.0)]
        results = serial_telemetry.evaluate_capture(
            samples_with_wall_clock(pairs, start=30), start_s=30)
        cli = [r for r in results if r.name == "cli_matches_anchor"][0]
        self.assertTrue(cli.ok)

    def test_two_songs_in_one_capture_are_segmented(self):
        # 一次抓取连播两首：第二首位点从 0 重新起算，分段断言不误报回退。
        samples = samples_with_wall_clock([(2, 2.0), (4, 4.0), (6, 6.0)])
        samples += samples_with_wall_clock(
            [(1, 10.0), (3, 12.0), (5, 14.0)], anchor_t=1000.0)
        results = serial_telemetry.evaluate_capture(samples)
        failed = [r for r in results if not r.ok]
        self.assertEqual(failed, [])

    def test_position_regression_fails_monotonic(self):
        pairs = [(2, 2.0), (4, 4.1), (2, 6.0), (6, 8.1)]
        results = serial_telemetry.evaluate_capture(samples_with_wall_clock(pairs))
        mono = [r for r in results if r.name == "position_monotonic"][0]
        self.assertFalse(mono.ok)

    def test_stall_samples_do_not_break_wall_clock_assertion(self):
        # 个别卡顿（位点冻结两拍）拉低个别样本：领先检测看上四分位（q75），
        # 卡顿产生负偏差，不把 q75 推过 +0.6；总推进率也不越下限。
        pairs = [(2, 2.0), (4, 4.0), (6, 6.0), (6, 8.0), (8, 10.0), (10, 12.0),
                 (12, 14.0), (14, 16.0)]
        results = serial_telemetry.evaluate_capture(samples_with_wall_clock(pairs))
        track = [r for r in results if r.name == "position_tracks_wall_clock"][0]
        self.assertTrue(track.ok)
        rate = [r for r in results if r.name == "position_advance_rate"][0]
        self.assertTrue(rate.ok)

    def test_grossly_slow_advance_fails_rate(self):
        # 位点系统性走慢（如把帧时长折算错）：总推进远落后挂钟。
        pairs = [(1, 2.0), (2, 4.1), (3, 6.0), (4, 8.2), (5, 10.1), (6, 12.0)]
        results = serial_telemetry.evaluate_capture(samples_with_wall_clock(pairs))
        rate = [r for r in results if r.name == "position_advance_rate"][0]
        self.assertFalse(rate.ok)

    def test_missing_anchor_fails_with_clear_message(self):
        samples = [{"t": 1000.0 + dt, "line": pipe_line(pos)}
                   for pos, dt in [(2, 2.0), (4, 4.0)]]
        results = serial_telemetry.evaluate_capture(samples)
        anchor = [r for r in results if r.name == "session_anchor_seen"][0]
        self.assertFalse(anchor.ok)
        self.assertIn("锚点", anchor.detail)

    def test_too_few_pipe_lines_fails(self):
        pairs = [(2, 2.0)]
        results = serial_telemetry.evaluate_capture(
            samples_with_wall_clock(pairs), min_lines=3)
        enough = [r for r in results if r.name == "enough_position_lines"][0]
        self.assertFalse(enough.ok)


class EvaluateLiveCapture(unittest.TestCase):
    """直播流断言组：不记位点。"""

    def test_all_live_marker_passes(self):
        samples = [{"t": 1000.0, "line": session_line(form="live", duration=0)}]
        for dt in (2.0, 4.0, 6.0, 8.0):
            samples.append({"t": 1000.0 + dt, "line": pipe_line("live")})
        # 形态取自锚点（form=live），无须调用方记得传 --live。
        results = serial_telemetry.evaluate_capture(samples)
        failed = [r for r in results if not r.ok]
        self.assertEqual(failed, [])

    def test_numeric_position_in_live_mode_fails(self):
        samples = [{"t": 1000.0, "line": session_line(form="live", duration=0)}]
        for pos, dt in [(2, 2.0), (4, 4.0), (6, 6.0)]:
            samples.append({"t": 1000.0 + dt, "line": pipe_line(pos)})
        results = serial_telemetry.evaluate_capture(samples, live=True)
        no_pos = [r for r in results if r.name == "live_records_no_position"][0]
        self.assertFalse(no_pos.ok)

    def test_cli_live_flag_still_works_without_anchor(self):
        # 没抓到锚点（旧固件/起播早于开抓）：CLI --live 兜底。
        samples = [{"t": 1000.0 + dt, "line": pipe_line("live")}
                   for dt in (2.0, 4.0, 6.0)]
        results = serial_telemetry.evaluate_capture(samples, live=True)
        no_pos = [r for r in results if r.name == "live_records_no_position"][0]
        self.assertTrue(no_pos.ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
