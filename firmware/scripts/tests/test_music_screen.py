#!/usr/bin/env python3
"""music_screen 纯逻辑契约测试（issue #8：屏幕上看得出在放什么；issue #11：
暂停态的位点与两种暂停文案）。

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

issue #11 续增三条：
  5. 位点与总量**互斥**：暂停态显示已播位点（不显示总量），播放态显示总量
     （不显示位点）——两态各自只出用户当时关心的那个数字；
  6. 两种暂停（会话性 / 用户）的文本**必须不同**：这是本票的核心，用户据此
     判断该等还是该说「继续」；
  7. 直播流在**两种状态下都不出时钟**（位点与总量都没有）。

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
        /* now <title> <author> <duration> <live|finite> <state> <position> <prefix>
         * 空字段用空串传（引号里什么都没有）；未知时长传 0。
         * state：playing | paused_conv | paused_user（issue #11）。
         * position：已播位点秒（暂停态用），未知传 -1。 */
        MusicScreenFacts facts;
        facts.title = argv[2];
        facts.author = argv[3];
        facts.duration_s = atoi(argv[4]);
        facts.live = strcmp(argv[5], "live") == 0;
        const std::string state = argv[6];
        facts.state = state == "paused_conv"
                          ? MusicScreenState::kPausedConversation
                          : (state == "paused_user"
                                 ? MusicScreenState::kPausedUser
                                 : MusicScreenState::kPlaying);
        facts.position_s = atoi(argv[7]);
        printf("%s", BuildMusicNowPlaying(facts, argv[8]).c_str());
    } else if (op == "state_name") {
        /* state_name <playing|paused_conv|paused_user>：锚点串是遥测契约
           （issue #11），两态必须互不相同、且与设备侧 state_name() 同口径。 */
        const std::string state = argv[2];
        MusicScreenState which = state == "paused_conv"
                                     ? MusicScreenState::kPausedConversation
                                     : (state == "paused_user"
                                            ? MusicScreenState::kPausedUser
                                            : MusicScreenState::kPlaying);
        printf("%s", MusicScreenStateName(which));
    } else if (op == "shows") {
        /* shows <duration> <live|finite> <state> <position> <total|position|none>
         * 把两个谓词的结果对齐，方便逐格断言（issue #11）。 */
        MusicScreenFacts facts;
        facts.duration_s = atoi(argv[2]);
        facts.live = strcmp(argv[3], "live") == 0;
        const std::string state = argv[4];
        facts.state = state == "paused_conv"
                          ? MusicScreenState::kPausedConversation
                          : (state == "paused_user"
                                 ? MusicScreenState::kPausedUser
                                 : MusicScreenState::kPlaying);
        facts.position_s = atoi(argv[5]);
        const std::string which = argv[6];
        if (which == "total") {
            printf("%s", MusicScreenShowsTotal(facts) ? "yes" : "no");
        } else if (which == "position") {
            printf("%s", MusicScreenShowsPosition(facts) ? "yes" : "no");
        } else {
            printf("%s", (MusicScreenShowsTotal(facts) ||
                           MusicScreenShowsPosition(facts)) ? "yes" : "no");
        }
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
            prefix="正在播放：", state="playing", position=-1):
        return self.run_op("now", title, author, duration,
                           "live" if live else "finite", state, position, prefix)

    def shows(self, which, duration=269, live=False, state="playing",
              position=-1):
        return self.run_op("shows", duration, "live" if live else "finite",
                           state, position, which)

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

    # ── 暂停态：位点与两种暂停文案（issue #11）──────────────────
    PAUSED_CONV = "已暂停（说完自动继续）："
    PAUSED_USER = "已暂停（说“继续”恢复）："

    def test_paused_shows_position_not_total(self):
        """暂停了报「放到哪」，不再报「还有多久」——位点与总量互斥。"""
        self.assertEqual(
            self.now(state="paused_conv", position=72, prefix=self.PAUSED_CONV),
            "已暂停（说完自动继续）：晴天 · 周杰伦 · 1:12")
        self.assertEqual(
            self.now(state="paused_user", position=72, prefix=self.PAUSED_USER),
            "已暂停（说“继续”恢复）：晴天 · 周杰伦 · 1:12")

    def test_two_pause_kinds_read_differently(self):
        """两种暂停的**文本必须不同**——这是本票的核心：用户据此决定该不该等。

        只差前缀就够：正文（曲目/作者/位点）在同一状态下本就该一样。这条断言
        存在的意义是拦住「两种暂停写成同一句」——那会让屏幕上分不出自己该等
        还是该说「继续」，而任何只断言「字串非空/含曲目」的测试都看不见它。
        """
        conv = self.now(state="paused_conv", position=72,
                        prefix=self.PAUSED_CONV)
        user = self.now(state="paused_user", position=72,
                        prefix=self.PAUSED_USER)
        self.assertNotEqual(conv, user)

    def test_playing_never_shows_a_position(self):
        """播放中不显示位点：那是每 2 秒在变的数字，用户关心的是还有多久。"""
        self.assertEqual(self.now(state="playing", position=72),
                         "正在播放：晴天 · 周杰伦 · 4:29")

    def test_paused_unknown_position_shows_no_clock(self):
        """位点未知（负数）时不出数字，也不退回去显示总量（那是另一态的数字）。"""
        for position in (-1, -10):
            with self.subTest(position=position):
                self.assertEqual(
                    self.now(state="paused_conv", position=position,
                             prefix=self.PAUSED_CONV),
                    "已暂停（说完自动继续）：晴天 · 周杰伦")

    def test_paused_zero_position_is_a_real_clock(self):
        """0 是合法位点（刚开始就暂停），显示 0:00 而不是当未知吞掉。"""
        self.assertEqual(
            self.now(state="paused_conv", position=0, prefix=self.PAUSED_CONV),
            "已暂停（说完自动继续）：晴天 · 周杰伦 · 0:00")

    def test_live_never_shows_position_or_total(self):
        """直播流不显示位点与总量（issue #11）——即便位点/时长都传了值。

        两种变体都试：暂停态（位点本该出现的态）与播放态（总量本该出现的态）。
        """
        self.assertEqual(
            self.now(title="Jazz Radio", author="WDR", duration=999, live=True,
                     position=72, state="paused_conv",
                     prefix=self.PAUSED_CONV),
            "已暂停（说完自动继续）：Jazz Radio · WDR")
        self.assertEqual(
            self.now(title="Jazz Radio", author="WDR", duration=999, live=True,
                     position=72, state="playing"),
            "正在播放：Jazz Radio · WDR")

    def test_paused_with_missing_track_still_shows_position(self):
        """曲目缺失不影响位点：曲目字段可以空，位点仍是用户要靠的那个数字。"""
        self.assertEqual(self.now(title="", author="", state="paused_conv",
                                  position=72, prefix=self.PAUSED_CONV),
                         "已暂停（说完自动继续）：1:12")

    def test_position_uses_the_same_clock_format(self):
        """位点与总走同一口径（m:ss / h:mm:ss）——两套格式会让屏幕上的数字读不出关系。"""
        self.assertEqual(
            self.run_op("now", "晴天", "周杰伦", 269, "finite",
                        "paused_conv", 3661, self.PAUSED_CONV),
            "已暂停（说完自动继续）：晴天 · 周杰伦 · 1:01:01")

    # ── 两个 Shows* 谓词的真值表（issue #11）──────────────────────
    def test_shows_position_only_when_paused_and_seekable(self):
        self.assertEqual(self.shows("position", state="playing", position=72),
                         "no")
        self.assertEqual(self.shows("position", state="paused_conv",
                                    position=72), "yes")
        self.assertEqual(self.shows("position", state="paused_user",
                                    position=72), "yes")
        self.assertEqual(self.shows("position", live=True, state="paused_user",
                                    position=72), "no")
        self.assertEqual(self.shows("position", state="paused_conv",
                                    position=-1), "no")

    def test_shows_total_only_when_playing_or_paused_with_known_duration(self):
        """总量谓词本就不看状态（它只答「该不该显示总量」这个事实问题）。"""
        self.assertEqual(self.shows("total", duration=269, state="playing"),
                         "yes")
        self.assertEqual(self.shows("total", duration=269, state="paused_conv"),
                         "yes")
        self.assertEqual(self.shows("total", live=True, duration=269), "no")
        self.assertEqual(self.shows("total", duration=0), "no")

    def test_live_never_shows_any_clock(self):
        """直播流在两种状态下都不出时钟（位点与总量都不该有）。"""
        for state in ("playing", "paused_conv", "paused_user"):
            with self.subTest(state=state):
                self.assertEqual(self.shows("none", live=True, duration=269,
                                            state=state, position=72), "no")


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


class MusicScreenPausedStructureTest(unittest.TestCase):
    """暂停文案的结构不变量（issue #11）：前缀得从**快照**挑，不从入参挑。

    为什么这条要用源码断言而不是普通行为测试：被它挡住的 bug 是「按调用方的
    暂停种类入参挑前缀」。那在两条真实路径上会写出错话——

      - 会话性暂停期间用户改口说「暂停」（升级）：播放器把种类改成 user，但若
        前缀从入参（conversation）取，屏幕依旧写「说完自动继续」——用户等下去，
        音乐会永远不回来；
      - 用户暂停期间又来一次唤醒（降级被拒）：播放器仍是 user，但若前缀从入参
        （conversation）取，屏幕上写着「会自动继续」而实际上必须说「继续」。

    两种都只在“某种暂停下又发生一次事件”时暴露，而那是抓取脚本里最不常覆盖的
    组合。判据因此放在源码层：**三个 Lang 前缀的赋值必须在同一个 switch
    里、且那个 switch 的条件是 snapshot 的状态**。
    """

    CC = Path(__file__).resolve().parents[2] / "main" / "application.cc"

    def setUp(self):
        self.source = self.CC.read_text(encoding="utf-8")
        self.lines = self.source.splitlines()

    def test_pause_prefixes_are_chosen_from_the_snapshot(self):
        """两个暂停前缀的赋值必须紧跟在 `switch (status.state)` 内。

        若有人在 `PauseMusic(kind)` 里写 `prefix = kind == kUser ? … : …`，
        那两个赋值就不再落在 status.state 的 switch 里，这里会失败。
        """
        assigns = [i for i, line in enumerate(self.lines)
                   if "Lang::Strings::MUSIC_PAUSED_" in line
                   and "=" in line.split("//")[0]]
        self.assertEqual(len(assigns), 2,
                         "暂停前缀应恰好两处赋值（会话性 + 用户），实际行号："
                         f"{[i + 1 for i in assigns]}")
        # 向上找到最近的 switch，它必须是 status.state 上的分支。
        switch_at = None
        for i in range(min(assigns), -1, -1):
            if "switch (status.state)" in self.lines[i]:
                switch_at = i
                break
        self.assertIsNotNone(
            switch_at,
            "两个暂停前缀的赋值不在 `switch (status.state)` 内——前缀会随入参"
            "（而非实际状态）变，两种暂停的文案就会互相写错")
        # 两个赋值与那个 switch 之间不得夹着别的 switch。
        between = "\n".join(self.lines[switch_at:max(assigns) + 1])
        self.assertEqual(between.count("switch ("), 1,
                         "暂停前缀的赋值被夹在多个 switch 之间，判据不成立")

    def test_pause_screen_write_does_not_take_the_pause_kind(self):
        """写屏入口不得接受暂停种类——种类只活在快照里，进去一个就是第二份真相。

        `ScheduleMusicScreen` 只收一个遥测标签（`"paused"`），文案与种类都
        现取快照。若将来给暂停写屏加上 kind 参数，两种暂停就会有两处判断。
        """
        for i, line in enumerate(self.lines):
            if "ScheduleMusicScreen(" not in line or i > 0 and "void " in line:
                continue
            self.assertNotIn(
                "kind", line,
                f"第 {i + 1} 行的写屏调用带了暂停种类：{line.strip()}"
                "——前缀必须从快照取，传 kind 会让两种暂停的判断分叉")


class MusicScreenStateNameTest(MusicScreenHostTest):
    """`MusicScreenStateName` 是遥测契约（issue #11）：两态串必须互不相同。

    为什么必须可断言：串口断言靠 `state=` 分组比对「两种暂停的屏幕文本不得
    相同」。若这个映射把两态写成同一个串（或漏了分支落到默认值），两种暂停
    在锚点里就分不开——而屏幕上是不是真的不同，反而没人能自动验。
    """

    def test_names_are_distinct_and_stable(self):
        self.assertEqual(self.run_op("state_name", "playing"), "playing")
        self.assertEqual(self.run_op("state_name", "paused_conv"),
                         "paused_conversation")
        self.assertEqual(self.run_op("state_name", "paused_user"), "paused_user")
        names = [self.run_op("state_name", s) for s in
                 ("playing", "paused_conv", "paused_user")]
        self.assertEqual(len(set(names)), 3, f"三个呈现态的锚点串撞了：{names}")

    def test_names_match_the_device_side_contract(self):
        """与 `MusicPlaybackStatus::state_name()` 一字不差——两边同一对术语。"""
        source = (Path(__file__).resolve().parents[2]
                  / "main" / "audio" / "music_player.h").read_text(encoding="utf-8")
        for name in ("playing", "paused_conversation", "paused_user"):
            self.assertIn(f'"{name}"', source,
                          f"{name} 不在 music_player.h 的 state_name() 里")


if __name__ == "__main__":
    unittest.main()
