#!/usr/bin/env python3
"""管线遥测位点断言测试（issue #3 + issue #4）。

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
  - 直播流场景：全部 pos=live，不出现数字位点；
  - 暂停/续播（issue #4）：位点冻结、两种暂停种类可区分、续播接缝在 ±0.5s 内
    （restart 按锚点自报的 margin 放行回退）、用户暂停绝不自动续。

位点精度：pipe: 周期行是整秒；三条锚点行（`Music pause: pos=`、
`Music resume: … at=/from=`）是一位小数——解析两侧都要能吃下（见
test_anchor_positions_accept_one_decimal）。

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


def pause_marker(kind="user", pos=30, t=0.0):
    """暂停锚点行：设备进暂停时打一次，带冻结位点（一位小数口径）。"""
    return {"t": t, "line": f"I (200) MusicPlayer: Music pause: kind={kind} pos={pos}s"}


def resume_marker(mode="continue", pos=30, from_s=None, margin=None, t=0.0):
    """续播锚点行：两段式恢复走了哪一段。

    continue：`Music resume: mode=continue at=<冻结位点>s`
    restart：`Music resume: mode=restart at=<重起位点>s from=<暂停前位点>s margin=<余量>s`
    位点按设备的锚点口径写（一位小数），margin 是整秒。
    """
    line = f"I (201) MusicPlayer: Music resume: mode={mode} at={pos}s"
    if from_s is not None:
        line += f" from={from_s}s"
    if margin is not None:
        line += f" margin={margin}s"
    return {"t": t, "line": line}


def auto_resume_marker(quiet=5, t=0.0):
    return {"t": t, "line": f"I (202) Application: Music auto-resume: quiet={quiet}s"}


class ParsePauseAndResumeLines(unittest.TestCase):
    """暂停/续播锚点行解析（issue #4）。"""

    def test_pipe_line_carries_pause_kind(self):
        # 两种暂停语义必须可从遥测区分。
        conv = serial_telemetry.parse_pipe_line(pipe_line(30, flags="PAUSED_CONV"))
        self.assertEqual(conv["paused"], "conversation")
        user = serial_telemetry.parse_pipe_line(pipe_line(30, flags="PAUSED_USER"))
        self.assertEqual(user["paused"], "user")
        playing = serial_telemetry.parse_pipe_line(pipe_line(30))
        self.assertIsNone(playing["paused"])

    def test_pause_and_resume_and_auto_resume_lines(self):
        pause = serial_telemetry.parse_pause_line(
            "I (1) MusicPlayer: Music pause: kind=conversation pos=42s")
        self.assertEqual(pause, {"kind": "conversation", "pos": 42})
        resume = serial_telemetry.parse_resume_line(
            "I (2) MusicPlayer: Music resume: mode=restart at=120s "
            "from=122s margin=2s")
        self.assertEqual(resume["mode"], "restart")
        self.assertEqual((resume["pos"], resume["from_s"], resume["margin_s"]),
                         (120, 122, 2))
        cont = serial_telemetry.parse_resume_line(
            "I (3) MusicPlayer: Music resume: mode=continue at=42s")
        self.assertEqual(cont["mode"], "continue")
        self.assertEqual(cont["pos"], 42)
        self.assertIsNone(cont["from_s"])
        live = serial_telemetry.parse_resume_line(
            "I (5) MusicPlayer: Music resume: mode=restart at=live from=live margin=0s")
        self.assertEqual((live["pos"], live["from_s"]), ("live", "live"))
        auto = serial_telemetry.parse_auto_resume_line(
            "I (4) Application: Music auto-resume: quiet=5s")
        self.assertEqual(auto, {"quiet_s": 5})

    def test_anchor_positions_accept_one_decimal(self):
        # 锚点行是一位小数（spec 的 ±0.5s 验收缝要这精度）；pipe: 周期行仍是整秒。
        pause = serial_telemetry.parse_pause_line(
            "I (1) MusicPlayer: Music pause: kind=user pos=73.4s")
        self.assertEqual(pause, {"kind": "user", "pos": 73.4})
        cont = serial_telemetry.parse_resume_line(
            "I (2) MusicPlayer: Music resume: mode=continue at=73.4s")
        self.assertEqual(cont["pos"], 73.4)
        restart = serial_telemetry.parse_resume_line(
            "I (3) MusicPlayer: Music resume: mode=restart at=71.4s "
            "from=73.4s margin=2s")
        self.assertEqual((restart["pos"], restart["from_s"], restart["margin_s"]),
                         (71.4, 73.4, 2))
        # pipe: 行继续整秒：小数位不该出现在周期遥测里。
        pipe = serial_telemetry.parse_pipe_line(pipe_line(73))
        self.assertEqual(pipe["pos"], 73)

    def test_live_anchors_still_parse_as_literal_live(self):
        pause = serial_telemetry.parse_pause_line(
            "I (1) MusicPlayer: Music pause: kind=conversation pos=live")
        self.assertEqual((pause["kind"], pause["pos"]), ("conversation", "live"))
        cont = serial_telemetry.parse_resume_line(
            "I (2) MusicPlayer: Music resume: mode=continue at=live")
        self.assertEqual(cont["pos"], "live")

    def test_non_marker_lines_return_none(self):
        self.assertIsNone(serial_telemetry.parse_pause_line(pipe_line(3)))
        self.assertIsNone(serial_telemetry.parse_resume_line(pipe_line(3)))
        self.assertIsNone(serial_telemetry.parse_auto_resume_line(pipe_line(3)))


class EvaluatePauseResume(unittest.TestCase):
    """暂停冻结位点、续播接在原处、两种暂停可区分。"""

    def _capture(self, events, start=0, anchor_t=1000.0):
        """锚点与首个样本同一拍（真机就这样：起流推第一帧即打锚点）。"""
        samples = [{"t": anchor_t, "line": session_line(start=start)}]
        samples += events
        return samples

    def test_paused_span_freezes_position_and_resume_continues(self):
        # 播到 32s → 用户暂停（位点冻结）→ 继续（接在 32s 继续走）。
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(30)},
            {"t": 1002.0, "line": pipe_line(32)},
            pause_marker("user", 32, t=1002.2),
            {"t": 1004.0, "line": pipe_line(32, flags="PAUSED_USER")},
            {"t": 1006.0, "line": pipe_line(32, flags="PAUSED_USER")},
            {"t": 1008.0, "line": pipe_line(32, flags="PAUSED_USER")},
            resume_marker("continue", 32, t=1008.2),
            {"t": 1010.0, "line": pipe_line(34)},
            {"t": 1012.0, "line": pipe_line(36)},
        ], start=30)
        results = serial_telemetry.evaluate_capture(samples)
        failed = [r for r in results if not r.ok]
        self.assertEqual(failed, [])

    def test_pause_kind_is_recorded_per_kind(self):
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(10)},
            {"t": 1002.0, "line": pipe_line(12)},
            pause_marker("conversation", 12, t=1002.2),
            {"t": 1004.0, "line": pipe_line(12, flags="PAUSED_CONV")},
            resume_marker("continue", 12, t=1004.2),
            {"t": 1006.0, "line": pipe_line(14)},
            {"t": 1008.0, "line": pipe_line(16)},
        ], start=10)
        results = serial_telemetry.evaluate_capture(samples)
        kind = [r for r in results if r.name == "pause_kind_recorded"][0]
        self.assertTrue(kind.ok)
        self.assertIn("conversation", kind.detail)
        pipe = [r for r in results if r.name == "pause_freezes_position"][0]
        self.assertTrue(pipe.ok)

    def test_position_growing_while_paused_fails(self):
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(30)},
            {"t": 1002.0, "line": pipe_line(30, flags="PAUSED_USER")},
            # 暂停期间位点还在走：说明「暂停」没真停（仍在推帧）。
            {"t": 1004.0, "line": pipe_line(32, flags="PAUSED_USER")},
            resume_marker("continue", 32, t=1004.2),
            {"t": 1006.0, "line": pipe_line(34)},
            {"t": 1008.0, "line": pipe_line(36)},
        ], start=30)
        results = serial_telemetry.evaluate_capture(samples)
        frozen = [r for r in results if r.name == "pause_freezes_position"][0]
        self.assertFalse(frozen.ok)

    def test_pause_anchor_mismatching_pipe_fails(self):
        # 暂停锚点说冻在 30s，暂停期间的 pipe: 却是 33s——接缝对不上，
        # 「暂停后位点不再增长」没成立。
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(30)},
            pause_marker("user", 30, t=1000.2),
            {"t": 1002.0, "line": pipe_line(33, flags="PAUSED_USER")},
            {"t": 1004.0, "line": pipe_line(33, flags="PAUSED_USER")},
            resume_marker("continue", 33, t=1004.2),
            {"t": 1006.0, "line": pipe_line(35)},
        ], start=30)
        results = serial_telemetry.evaluate_capture(samples)
        frozen = [r for r in results if r.name == "pause_freezes_position"][0]
        self.assertFalse(frozen.ok)

    def test_resume_restart_within_safety_margin_passes(self):
        # 原连接不可用：按位点 122s 重起流，回退 2s 安全余量到 120s。
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(120)},
            {"t": 1002.0, "line": pipe_line(122)},
            pause_marker("conversation", 122, t=1002.2),
            {"t": 1004.0, "line": pipe_line(122, flags="PAUSED_CONV")},
            {"t": 1006.0, "line": pipe_line(122, flags="PAUSED_CONV")},
            resume_marker("restart", 120, from_s=122, margin=2, t=1006.2),
            {"t": 1008.0, "line": pipe_line(122)},
            {"t": 1010.0, "line": pipe_line(124)},
        ], start=120)
        results = serial_telemetry.evaluate_capture(samples)
        failed = [r for r in results if not r.ok]
        self.assertEqual(failed, [])
        seam = [r for r in results if r.name == "resume_continues_position"][0]
        self.assertIn("restart", seam.detail)

    def test_resume_restart_beyond_margin_fails(self):
        # 回退 10s：宁可重复不跳词的余量被越界，属于跳词风险。
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(120)},
            {"t": 1002.0, "line": pipe_line(120, flags="PAUSED_CONV")},
            resume_marker("restart", 110, from_s=120, margin=2, t=1002.2),
            {"t": 1004.0, "line": pipe_line(110)},
            {"t": 1006.0, "line": pipe_line(112)},
        ], start=120)
        results = serial_telemetry.evaluate_capture(samples)
        seam = [r for r in results if r.name == "resume_continues_position"][0]
        self.assertFalse(seam.ok)

    def test_resume_continue_jumping_forward_fails(self):
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(30)},
            {"t": 1002.0, "line": pipe_line(30, flags="PAUSED_USER")},
            # 原连接「继续」却跳到 90s：这是跳词，不是续播。
            resume_marker("continue", 90, from_s=30, t=1002.2),
            {"t": 1004.0, "line": pipe_line(90)},
            {"t": 1006.0, "line": pipe_line(92)},
        ], start=30)
        results = serial_telemetry.evaluate_capture(samples)
        seam = [r for r in results if r.name == "resume_continues_position"][0]
        self.assertFalse(seam.ok)

    def test_two_pauses_in_one_capture_each_freeze_at_their_own_position(self):
        # 一次抓取里暂停两次（会话性暂停 + 自动续播，再来一轮）：两段各自
        # 冻结在各自位点，段间位点当然会变——不该被判成「暂停期间位点增长」。
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(0)},
            {"t": 1002.0, "line": pipe_line(2)},
            {"t": 1004.0, "line": pipe_line(4)},
            pause_marker("conversation", 4, t=1004.2),
            {"t": 1006.0, "line": pipe_line(4, flags="PAUSED_CONV")},
            {"t": 1008.0, "line": pipe_line(4, flags="PAUSED_CONV")},
            auto_resume_marker(5, t=1008.2),
            resume_marker("continue", 4, t=1008.4),
            {"t": 1010.0, "line": pipe_line(6)},
            {"t": 1012.0, "line": pipe_line(8)},
            {"t": 1014.0, "line": pipe_line(10)},
            pause_marker("conversation", 10, t=1014.2),
            {"t": 1016.0, "line": pipe_line(10, flags="PAUSED_CONV")},
            {"t": 1018.0, "line": pipe_line(10, flags="PAUSED_CONV")},
            resume_marker("continue", 10, t=1018.4),
            {"t": 1020.0, "line": pipe_line(12)},
            {"t": 1022.0, "line": pipe_line(14)},
        ], start=0)
        results = serial_telemetry.evaluate_capture(samples)
        failed = [r for r in results if not r.ok]
        self.assertEqual(failed, [])
        frozen = [r for r in results if r.name == "pause_freezes_position"][0]
        self.assertIn("2 段暂停", frozen.detail)

    def test_second_span_continue_jumping_forward_fails(self):
        # 第二段暂停的续播跳了（接到 90s 而不是冻住的 10s）：照样要报。
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(0)},
            {"t": 1002.0, "line": pipe_line(4)},
            pause_marker("conversation", 4, t=1002.2),
            {"t": 1004.0, "line": pipe_line(4, flags="PAUSED_CONV")},
            resume_marker("continue", 4, t=1004.4),
            {"t": 1006.0, "line": pipe_line(10)},
            pause_marker("conversation", 10, t=1006.2),
            {"t": 1008.0, "line": pipe_line(10, flags="PAUSED_CONV")},
            resume_marker("continue", 90, t=1008.4),
            {"t": 1010.0, "line": pipe_line(90)},
        ], start=0)
        results = serial_telemetry.evaluate_capture(samples)
        seam = [r for r in results if r.name == "resume_continues_position"][0]
        self.assertFalse(seam.ok)
        self.assertIn("90", seam.detail)

    def test_pause_without_resume_is_reported(self):
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(30)},
            {"t": 1002.0, "line": pipe_line(30, flags="PAUSED_USER")},
            {"t": 1004.0, "line": pipe_line(30, flags="PAUSED_USER")},
        ], start=30)
        results = serial_telemetry.evaluate_capture(samples)
        seam = [r for r in results if r.name == "resume_continues_position"][0]
        self.assertFalse(seam.ok)

    def test_capture_without_pause_still_passes(self):
        # issue #3 那套核验（只播、不暂停）不该因「没暂停」而失败。
        pairs = [(2, 2.0), (4, 4.1), (6, 6.0), (8, 8.2), (10, 10.1), (12, 12.0)]
        results = serial_telemetry.evaluate_capture(samples_with_wall_clock(pairs))
        failed = [r for r in results if not r.ok]
        self.assertEqual(failed, [])
        names = [r.name for r in results]
        self.assertNotIn("pause_freezes_position", names)

    def test_paused_capture_keeps_wall_clock_assertion_green(self):
        # 一段含暂停的正常抓取：暂停 4s 后位点接着走。挂钟偏差已扣暂停、
        # 推进率按净播放时长算——两条都不该被暂停拖失败。
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(0)},
            {"t": 1002.0, "line": pipe_line(2)},
            {"t": 1004.0, "line": pipe_line(4)},
            pause_marker("user", 4, t=1004.2),
            {"t": 1006.0, "line": pipe_line(4, flags="PAUSED_USER")},
            {"t": 1008.0, "line": pipe_line(4, flags="PAUSED_USER")},
            resume_marker("continue", 4, t=1008.2),
            {"t": 1010.0, "line": pipe_line(6)},
            {"t": 1012.0, "line": pipe_line(8)},
            {"t": 1014.0, "line": pipe_line(10)},
        ], start=0)
        results = serial_telemetry.evaluate_capture(samples)
        failed = [r for r in results if not r.ok]
        self.assertEqual(failed, [])

    def test_expect_restart_requires_restart_mode(self):
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(30)},
            {"t": 1002.0, "line": pipe_line(30, flags="PAUSED_USER")},
            resume_marker("continue", 30, t=1002.2),
            {"t": 1004.0, "line": pipe_line(32)},
            {"t": 1006.0, "line": pipe_line(34)},
        ], start=30)
        results = serial_telemetry.evaluate_capture(samples, expect_restart=True)
        restart = [r for r in results if r.name == "restart_path_seen"][0]
        self.assertFalse(restart.ok)
        samples.append(resume_marker("restart", 32, from_s=34, margin=2, t=1006.2))
        results = serial_telemetry.evaluate_capture(samples, expect_restart=True)
        restart = [r for r in results if r.name == "restart_path_seen"][0]
        self.assertTrue(restart.ok)

    def test_expect_auto_resume_requires_anchor(self):
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(30)},
            {"t": 1002.0, "line": pipe_line(30, flags="PAUSED_CONV")},
            resume_marker("continue", 30, t=1002.2),
            {"t": 1004.0, "line": pipe_line(32)},
            {"t": 1006.0, "line": pipe_line(34)},
        ], start=30)
        results = serial_telemetry.evaluate_capture(samples, expect_auto_resume=True)
        auto = [r for r in results if r.name == "auto_resume_fired"][0]
        self.assertFalse(auto.ok)
        samples.append(auto_resume_marker(quiet=5, t=1006.2))
        results = serial_telemetry.evaluate_capture(samples, expect_auto_resume=True)
        auto = [r for r in results if r.name == "auto_resume_fired"][0]
        self.assertTrue(auto.ok)

    def test_live_capture_records_pause_kind_without_position(self):
        samples = [{"t": 1000.0, "line": session_line(form="live", duration=0)}]
        samples.append({"t": 1002.0, "line": pipe_line("live")})
        samples.append({"t": 1004.0, "line": pipe_line("live", flags="PAUSED_USER")})
        samples.append(resume_marker("restart", "live", t=1004.2))
        samples.append({"t": 1006.0, "line": pipe_line("live")})
        results = serial_telemetry.evaluate_capture(samples)
        failed = [r for r in results if not r.ok]
        self.assertEqual(failed, [])

    def test_user_pause_never_auto_resumes_passes_without_marker(self):
        # 用户暂停 → 答一句 → 手动「继续」：跨度内没有 auto-resume，通过。
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(30)},
            pause_marker("user", 30, t=1000.2),
            {"t": 1002.0, "line": pipe_line(30, flags="PAUSED_USER")},
            {"t": 1004.0, "line": pipe_line(30, flags="PAUSED_USER")},
            resume_marker("continue", 30, t=1004.2),
            {"t": 1006.0, "line": pipe_line(32)},
        ], start=30)
        results = serial_telemetry.evaluate_capture(samples)
        never = [r for r in results
                 if r.name == "user_pause_never_auto_resumes"][0]
        self.assertTrue(never.ok)
        self.assertIn("用户暂停", never.detail)

    def test_user_pause_auto_resuming_fails(self):
        # 「说暂停后随便聊一句，音乐自己响了」：用户暂停跨度里出现自动续播标记。
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(30)},
            pause_marker("user", 30, t=1000.2),
            {"t": 1002.0, "line": pipe_line(30, flags="PAUSED_USER")},
            {"t": 1004.0, "line": pipe_line(30, flags="PAUSED_USER")},
            auto_resume_marker(5, t=1004.5),
            resume_marker("continue", 30, t=1004.6),
            {"t": 1006.0, "line": pipe_line(32)},
        ], start=30)
        results = serial_telemetry.evaluate_capture(samples)
        never = [r for r in results
                 if r.name == "user_pause_never_auto_resumes"][0]
        self.assertFalse(never.ok)
        self.assertIn("自动响", never.detail)

    def test_conversation_pause_auto_resuming_still_passes(self):
        # 会话性暂停的自动续播是正常行为：没有 user 痕迹就不出这条断言，
        # 整段抓取照样全绿（不误伤）。
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(30)},
            pause_marker("conversation", 30, t=1000.2),
            {"t": 1002.0, "line": pipe_line(30, flags="PAUSED_CONV")},
            {"t": 1004.0, "line": pipe_line(30, flags="PAUSED_CONV")},
            auto_resume_marker(5, t=1004.5),
            resume_marker("continue", 30, t=1004.6),
            {"t": 1006.0, "line": pipe_line(32)},
        ], start=30)
        results = serial_telemetry.evaluate_capture(samples)
        names = [r.name for r in results]
        self.assertNotIn("user_pause_never_auto_resumes", names)
        failed = [r for r in results if not r.ok]
        self.assertEqual(failed, [])

    def test_adjacent_user_then_conversation_pause_does_not_trip(self):
        # 用户暂停紧接会话性暂停（中间没有在播样本，位点跨度把两者并成一段）：
        # 会话性那半段的自动续播是合法的，不该被整段按 user 判而误伤。
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(30)},
            pause_marker("user", 30, t=1000.2),
            {"t": 1002.0, "line": pipe_line(30, flags="PAUSED_USER")},
            pause_marker("conversation", 30, t=1002.4),
            {"t": 1004.0, "line": pipe_line(30, flags="PAUSED_CONV")},
            auto_resume_marker(5, t=1004.5),
            resume_marker("continue", 30, t=1004.6),
            {"t": 1006.0, "line": pipe_line(32)},
            {"t": 1008.0, "line": pipe_line(34)},
        ], start=30)
        results = serial_telemetry.evaluate_capture(samples)
        never = [r for r in results
                 if r.name == "user_pause_never_auto_resumes"][0]
        self.assertTrue(never.ok)
        failed = [r for r in results if not r.ok]
        self.assertEqual(failed, [])

    def test_conversation_auto_resume_after_user_span_does_not_trip(self):
        # 用户暂停（用户自己说继续）之后，下一段会话性暂停照常自动续播：
        # 那条 auto-resume 不属于用户跨度，不该误伤。
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(30)},
            pause_marker("user", 30, t=1000.2),
            {"t": 1002.0, "line": pipe_line(30, flags="PAUSED_USER")},
            resume_marker("continue", 30, t=1002.4),
            {"t": 1004.0, "line": pipe_line(32)},
            {"t": 1006.0, "line": pipe_line(34)},
            pause_marker("conversation", 34, t=1006.2),
            {"t": 1008.0, "line": pipe_line(34, flags="PAUSED_CONV")},
            auto_resume_marker(5, t=1008.5),
            resume_marker("continue", 34, t=1008.6),
            {"t": 1010.0, "line": pipe_line(36)},
            {"t": 1012.0, "line": pipe_line(38)},
        ], start=30)
        results = serial_telemetry.evaluate_capture(samples)
        never = [r for r in results
                 if r.name == "user_pause_never_auto_resumes"][0]
        self.assertTrue(never.ok)

    def test_user_pause_anchor_catches_downgraded_pause(self):
        # 用户暂停被误降级成会话性（管道样本标 PAUSED_CONV），随后自动续播：
        # 锚点那份 kind=user 是更早的真相，这段必须仍判成用户窗口。
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(30)},
            pause_marker("user", 30, t=1000.2),
            {"t": 1002.0, "line": pipe_line(30, flags="PAUSED_CONV")},
            {"t": 1004.0, "line": pipe_line(30, flags="PAUSED_CONV")},
            auto_resume_marker(5, t=1004.5),
            resume_marker("continue", 30, t=1004.6),
            {"t": 1006.0, "line": pipe_line(32)},
        ], start=30)
        results = serial_telemetry.evaluate_capture(samples)
        never = [r for r in results
                 if r.name == "user_pause_never_auto_resumes"][0]
        self.assertFalse(never.ok)

    def test_capture_without_pause_has_no_never_auto_resume_assertion(self):
        # 没暂停的抓取不该凭空多出一条断言（issue #3 那套核验保持原样）。
        pairs = [(2, 2.0), (4, 4.1), (6, 6.0), (8, 8.2)]
        results = serial_telemetry.evaluate_capture(samples_with_wall_clock(pairs))
        names = [r.name for r in results]
        self.assertNotIn("user_pause_never_auto_resumes", names)

    def test_decimal_seam_within_half_second_passes(self):
        # 一位小数的锚点：冻在 73.4s，continue 接在 73.4s —— 0 偏差。
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(71)},
            {"t": 1002.0, "line": pipe_line(73)},
            pause_marker("user", 73.4, t=1002.2),
            {"t": 1004.0, "line": pipe_line(73, flags="PAUSED_USER")},
            resume_marker("continue", 73.4, t=1004.2),
            {"t": 1006.0, "line": pipe_line(75)},
            {"t": 1008.0, "line": pipe_line(77)},
        ], start=71)
        results = serial_telemetry.evaluate_capture(samples)
        seam = [r for r in results if r.name == "resume_continues_position"][0]
        self.assertTrue(seam.ok)

    def test_decimal_seam_beyond_half_second_fails(self):
        # 接缝跳了 1s（不是 ±0.5s 内的接续）：spec 的验收缝够得着这个错。
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(71)},
            {"t": 1002.0, "line": pipe_line(73)},
            pause_marker("user", 73.4, t=1002.2),
            {"t": 1004.0, "line": pipe_line(73, flags="PAUSED_USER")},
            resume_marker("continue", 74.4, t=1004.2),
            {"t": 1006.0, "line": pipe_line(76)},
            {"t": 1008.0, "line": pipe_line(78)},
        ], start=71)
        results = serial_telemetry.evaluate_capture(samples)
        seam = [r for r in results if r.name == "resume_continues_position"][0]
        self.assertFalse(seam.ok)

    def test_restart_margin_is_taken_from_the_anchor(self):
        # 回退判据按锚点自报的 margin：margin=2 回退 2.0s 通过……
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(71)},
            {"t": 1002.0, "line": pipe_line(73)},
            pause_marker("conversation", 73.4, t=1002.2),
            {"t": 1004.0, "line": pipe_line(73, flags="PAUSED_CONV")},
            resume_marker("restart", 71.4, from_s=73.4, margin=2, t=1004.2),
            {"t": 1006.0, "line": pipe_line(73)},
            {"t": 1008.0, "line": pipe_line(75)},
        ], start=71)
        results = serial_telemetry.evaluate_capture(samples)
        seam = [r for r in results if r.name == "resume_continues_position"][0]
        self.assertTrue(seam.ok)
        # ……同一份位点，margin 自报 1s（回退 2.0s 越界）就该报失败。
        tight = self._capture([
            {"t": 1000.0, "line": pipe_line(71)},
            {"t": 1002.0, "line": pipe_line(73)},
            pause_marker("conversation", 73.4, t=1002.2),
            {"t": 1004.0, "line": pipe_line(73, flags="PAUSED_CONV")},
            resume_marker("restart", 71.4, from_s=73.4, margin=1, t=1004.2),
            {"t": 1006.0, "line": pipe_line(73)},
            {"t": 1008.0, "line": pipe_line(75)},
        ], start=71)
        results = serial_telemetry.evaluate_capture(tight)
        seam = [r for r in results if r.name == "resume_continues_position"][0]
        self.assertFalse(seam.ok)

    def test_restart_forward_jump_beyond_half_second_fails(self):
        # 重起流却向前跳了 3s：跳词，容差 0.5s 直接逮住。
        samples = self._capture([
            {"t": 1000.0, "line": pipe_line(71)},
            {"t": 1002.0, "line": pipe_line(73)},
            pause_marker("conversation", 73.4, t=1002.2),
            {"t": 1004.0, "line": pipe_line(73, flags="PAUSED_CONV")},
            resume_marker("restart", 76.4, from_s=73.4, margin=2, t=1004.2),
            {"t": 1006.0, "line": pipe_line(78)},
        ], start=71)
        results = serial_telemetry.evaluate_capture(samples)
        seam = [r for r in results if r.name == "resume_continues_position"][0]
        self.assertFalse(seam.ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
