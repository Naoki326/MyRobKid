#!/usr/bin/env python3
"""music_screen 纯逻辑契约测试（issue #8：屏幕上看得出在放什么）。

为什么用宿主编译测：`main/audio/music_screen.{h,cc}` 一个 ESP 头文件都不引，
只吃四个字段（曲目/作者/时长/内容形态）。它管的是**消息区文案的拼装**——错了
就是「直播流上显示一个假的总量」或者「作者空了却留下一个孤零零的分隔符」，
两种都只在真机屏幕上才暴露。

契约四条：
  1. 曲目 + 作者 + 总量；缺哪块就不出哪块，不留悬空分隔符；
  2. 直播流（form=live）与时长未知**都不显示总量**——总量对它无意义，显示一个
     数字就是撒谎；
  3. 总量口径 m:ss（≥1 小时 h:mm:ss）；0 与负数视为未知；
  4. 前缀（Lang 字符串）原样前置，空前缀 = 只有正文。

运行：cd firmware && python3 -m unittest scripts.tests.test_music_screen -v
（需要宿主 C++ 编译器；不需要设备、不跑 idf.py、不联网。）
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MUSIC_SCREEN_CC = ROOT / "main" / "audio" / "music_screen.cc"
MAIN_DIR = ROOT / "main"

DRIVER = r"""
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include "audio/music_screen.h"

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <op> [args]\n", argv[0]);
        return 2;
    }
    const std::string op = argv[1];
    if (op == "clock") {
        /* clock <seconds> */
        printf("%s", FormatMusicClock(atoi(argv[2])).c_str());
    } else if (op == "now") {
        /* now <title> <author> <duration> <live|finite> <prefix>
         * 空字段用空串传（引号里什么都没有）；未知时长传 0。 */
        MusicScreenFacts facts;
        facts.title = argv[2];
        facts.author = argv[3];
        facts.duration_s = atoi(argv[4]);
        facts.live = strcmp(argv[5], "live") == 0;
        printf("%s", BuildMusicNowPlaying(facts, argv[6]).c_str());
    } else {
        fprintf(stderr, "unknown op: %s\n", op.c_str());
        return 2;
    }
    return 0;
}
"""


def _compiler():
    for candidate in ("c++", "clang++", "g++"):
        path = shutil.which(candidate)
        if path:
            return path
    raise unittest.SkipTest("no host C++ compiler found (c++/clang++/g++)")


class MusicScreenHostTest(unittest.TestCase):
    """编译一次，之后按 op 调用。"""

    binary = None

    @classmethod
    def setUpClass(cls):
        workdir = Path(tempfile.mkdtemp(prefix="music_screen_test_"))
        driver = workdir / "driver.cc"
        driver.write_text(DRIVER, encoding="utf-8")
        cls.binary = workdir / "driver"
        cls.workdir = workdir
        cmd = [_compiler(), "-std=c++17", "-I", str(MAIN_DIR), str(driver),
               str(MUSIC_SCREEN_CC), "-o", str(cls.binary)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(
                "host compile failed:\n" + result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "workdir", None):
            shutil.rmtree(cls.workdir, ignore_errors=True)

    def run_op(self, op, *extra):
        result = subprocess.run([str(self.binary), op, *map(str, extra)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def now(self, title="晴天", author="周杰伦", duration=269, live=False,
            prefix="正在播放："):
        return self.run_op("now", title, author, duration,
                           "live" if live else "finite", prefix)

    # ── 总量口径：m:ss / h:mm:ss ────────────────────────────────
    def test_clock_uses_minutes_and_seconds(self):
        self.assertEqual(self.run_op("clock", 0), "0:00")
        self.assertEqual(self.run_op("clock", 5), "0:05")
        self.assertEqual(self.run_op("clock", 59), "0:59")
        self.assertEqual(self.run_op("clock", 60), "1:00")
        self.assertEqual(self.run_op("clock", 269), "4:29")
        self.assertEqual(self.run_op("clock", 3599), "59:59")

    def test_clock_switches_to_hours(self):
        """一小时以上给 h:mm:ss——「60:00」没人读得出来，但也不能丢总量。"""
        self.assertEqual(self.run_op("clock", 3600), "1:00:00")
        self.assertEqual(self.run_op("clock", 3661), "1:01:01")
        self.assertEqual(self.run_op("clock", 86400), "24:00:00")

    def test_clock_never_shows_negative(self):
        """负数秒是坏数据，不是「负时长」：给空串，让调用方当未知处理。"""
        self.assertEqual(self.run_op("clock", -1), "")
        self.assertEqual(self.run_op("clock", -3600), "")

    # ── 正文拼装 ──────────────────────────────────────────────
    def test_title_author_and_total(self):
        self.assertEqual(self.now(), "正在播放：晴天 · 周杰伦 · 4:29")

    def test_prefix_is_prepended_verbatim(self):
        """Lang 字符串自带它的分隔（zh 是「：」，en 是「: 」），这里只管接上。"""
        self.assertEqual(self.now(prefix="Now playing: "),
                         "Now playing: 晴天 · 周杰伦 · 4:29")
        self.assertEqual(self.now(prefix=""), "晴天 · 周杰伦 · 4:29")

    def test_missing_author_leaves_no_dangling_separator(self):
        self.assertEqual(self.now(author=""), "正在播放：晴天 · 4:29")

    def test_missing_title_keeps_author(self):
        """曲目是主字段，但只剩作者时也得显示，且不留前导分隔符。"""
        self.assertEqual(self.now(title="", author="周杰伦"),
                         "正在播放：周杰伦 · 4:29")

    def test_missing_title_and_author_shows_only_total(self):
        self.assertEqual(self.now(title="", author=""), "正在播放：4:29")

    def test_nothing_to_say_returns_prefix_only(self):
        """一个字段都没有时只剩前缀：调用方据此判空（别往屏幕上写空白）。"""
        self.assertEqual(self.now(title="", author="", duration=0),
                         "正在播放：")
        self.assertEqual(self.now(title="", author="", duration=0, prefix=""), "")

    # ── 内容形态：live 与未知时长都不显示总量 ────────────────────
    def test_live_never_shows_total(self):
        self.assertEqual(self.now(title="Jazz Radio", author="WDR", duration=0,
                                  live=True),
                         "正在播放：Jazz Radio · WDR")

    def test_live_ignores_a_duration_that_slipped_through(self):
        """直播流即便带了个时长（上游没剥干净）也不显示——形态是否定条件的唯一依据。"""
        self.assertEqual(self.now(title="Jazz Radio", author="WDR", duration=999,
                                  live=True),
                         "正在播放：Jazz Radio · WDR")

    def test_unknown_duration_shows_no_total(self):
        """0 与负数都是「不知道放多久」：显示 0:00 会被读成「这就完了」。"""
        for duration in (0, -1, -10):
            with self.subTest(duration=duration):
                self.assertEqual(self.now(duration=duration),
                                 "正在播放：晴天 · 周杰伦")


class MusicScreenOwnershipTest(unittest.TestCase):
    """归属权（issue #8）的**结构不变量**：只有一处能复位它。

    为什么这条要用源码断言而不是行为测试：被它挡住的 bug（review 揪出来的）
    是「四条清屏路径里有一条漏了交还所有权」——那会让消息区被永久劫持，
    之后**所有**正常对话文字都显示不出来，而任何单条路径的行为测试都看不
    见它（每条路径自己都「工作正常」）。缺陷在「四者是否一致」这个层面，
    所以判据也必须在那一层。

    不建设备模拟器（工单的验收缝约定）：这里只读源码文本，不起固件、不碰串口。
    与 tools/tests/test_screen_telemetry.py 的分工是：那边管「串口锚点能不能
    断言屏幕内容」，这边管「源码里还有没有第二处复位」。
    """

    CC = Path(__file__).resolve().parents[2] / "main" / "application.cc"

    def setUp(self):
        self.source = self.CC.read_text(encoding="utf-8")

    def test_ownership_is_reset_in_exactly_one_place(self):
        """复位只能发生在 RepaintOrClearMusicScreen 体内（唯一一处实现）。

        这条会抓住的回归：有人图省事在某条清屏路径上写回
        `music_screen_owns_content_ = false;`。一旦出现第二处，
        StopNotification 那次漏写的历史就会重演——只是换个地方漏。
        """
        resets = [i + 1 for i, line in enumerate(self.source.splitlines())
                  if "music_screen_owns_content_ = false" in line
                  and not line.strip().startswith("//")]
        self.assertEqual(
            len(resets), 1,
            "归属权复位应恰好一处（RepaintOrClearMusicScreen 内），"
            f"实际 {len(resets)} 处，行号 {resets}")

    def test_repaint_decision_is_shared_by_the_clear_paths(self):
        """四条清屏路径都必须经 helper，不许自己判归属权。

        判据：`music_screen_owns_content_ && IsMusicBusy()` 这个组合判断在
        源码里只应出现在 helper 内（+ idle 分支那次纯计数），不该在某个清屏
        点上再抄一遍——散抄正是它漏写复位的成因。
        """
        guard = [i + 1 for i, line in enumerate(self.source.splitlines())
                 if "music_screen_owns_content_ && IsMusicBusy()" in line
                 and not line.strip().startswith("//")]
        callers = [i + 1 for i, line in enumerate(self.source.splitlines())
                   if "RepaintOrClearMusicScreen(" in line
                   and not line.strip().startswith("void Application::")
                   and not line.strip().startswith("//")]
        # 源码内：helper 自己 1 处判据 + idle 分支 1 处计数 = 2；调用点 4 处。
        self.assertEqual(len(guard), 2,
                         f"归属权判据应只在 helper 与 idle 计数处，实际 {guard}")
        self.assertEqual(len(callers), 4,
                         f"四条清屏路径应各调一次 helper，实际 {callers}")

    def test_every_clear_path_hands_back_ownership(self):
        """四条路径的**清屏动作**各自保留（显示变体差异），但都经 helper 交还。

        这条锁修复的实质：StopNotification 的历史 bug 正是它**没走**共享路径
        （裸清屏 + 漏了复位）。将来有人把某条路径改回裸清屏，这里会失败。

        判据不按函数名找（OnAudioChannelClosed 是 lambda，不具名），而是按
        **四条路径各自的清屏动作**是否紧邻一次 helper 调用：清屏 API 因变体
        而异（`ClearChatMessages()` 与 `SetChatMessage(role, "")` 两种），
        所以验的是「每处清屏动作都出现在 helper 的调用参数里」，而不是「只有
        一种清屏 API」。
        """
        lines = self.source.splitlines()
        # 定义行（`void Application::RepaintOrClearMusicScreen(`）不算调用点——
        # 它的参数是 std::function，不含具体清屏 API，会被下面那条判据误报。
        call_at = [i for i, line in enumerate(lines)
                   if "RepaintOrClearMusicScreen(" in line
                   and "void Application::" not in line]
        self.assertEqual(len(call_at), 4, f"应有 4 处调用，实际 {call_at}")
        # 每处调用都必须在随后的几行里出现清屏动作（它作为回调传入）。
        for i in call_at:
            window = "\n".join(lines[i:i + 4])
            self.assertTrue(
                "ClearChatMessages()" in window or 'SetChatMessage(' in window,
                f"第 {i + 1} 行的 helper 调用没有传入清屏动作：\n{window}")


if __name__ == "__main__":
    unittest.main()
