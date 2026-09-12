#!/usr/bin/env python3
"""music_ending 纯逻辑契约测试（issue #7：三种收场可区分）。

为什么用宿主编译测：`main/audio/music_ending.{h,cc}` 一个头文件都不引，不含任何
ESP 依赖。它管的是**一次音乐会话收场原因 → 反馈**的映射——错了就是
「用户按停却听见故障音」或者「真断了却安静得像放完了」，两种都只在真机上才暴露。
把它钉住就能在没有设备的情况下验证分支（`--assert` 需要真机，见 tools/README）。

契约三条：
  1. 推导：五个事实位（played/drained/cancelled/replaced/attempted_restart）→ 恰好
     一个结局，优先级 fixed（replaced > start_failed > completed > stopped >
     resume_failed > interrupted）；
  2. 命名：结局名是遥测契约（`Music ended: reason=…` / `Music feedback: reason=…`），
     两两不同且非空；
  3. 反馈：自然播完与链路中断必须给**不同**的音（且都不是静音）；用户主动停止
     /换歌**永远不给故障音**——这是 issue #7 的核心不变量。

运行：cd firmware && python3 -m unittest scripts.tests.test_music_ending -v
（需要宿主 C++ 编译器；不需要设备、不跑 idf.py、不联网。）
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MUSIC_ENDING_CC = ROOT / "main" / "audio" / "music_ending.cc"
MAIN_DIR = ROOT / "main"

DRIVER = r"""
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <string>
#include "audio/music_ending.h"

static bool Has(const char* csv, const char* flag) {
    const size_t n = strlen(flag);
    for (const char* p = csv; *p;) {
        const char* comma = strchr(p, ',');
        const size_t len = comma ? size_t(comma - p) : strlen(p);
        if (len == n && strncmp(p, flag, n) == 0) return true;
        if (!comma) break;
        p = comma + 1;
    }
    return false;
}

static const char* CueName(MusicCue cue) {
    switch (cue) {
        case MusicCue::kNone: return "none";
        case MusicCue::kSuccess: return "success";
        case MusicCue::kWarning: return "warning";
    }
    return "?";
}

/* 屏幕文案锚点的值 → 字面量（`Music feedback:` 的 screen= 字段）。 */
static const char* ScreenName(MusicEndingScreen screen) {
    return MusicEndingScreenName(screen);
}

/* 名字 → 结局的查表只属于测试：固件从不解析这个名字（它只输出）。 */
static bool LookupEnding(const char* name, MusicEnding* out) {
    if (strcmp(name, "completed") == 0) *out = MusicEnding::kCompleted;
    else if (strcmp(name, "interrupted") == 0) *out = MusicEnding::kInterrupted;
    else if (strcmp(name, "resume_failed") == 0) *out = MusicEnding::kResumeFailed;
    else if (strcmp(name, "stopped") == 0) *out = MusicEnding::kStopped;
    else if (strcmp(name, "replaced") == 0) *out = MusicEnding::kReplaced;
    else if (strcmp(name, "start_failed") == 0) *out = MusicEnding::kStartFailed;
    else return false;
    return true;
}

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <op> [args]\n", argv[0]);
        return 2;
    }
    std::string op = argv[1];
    if (op == "names") {
        printf("%s %s %s %s %s %s",
               MusicEndingName(MusicEnding::kCompleted),
               MusicEndingName(MusicEnding::kInterrupted),
               MusicEndingName(MusicEnding::kResumeFailed),
               MusicEndingName(MusicEnding::kStopped),
               MusicEndingName(MusicEnding::kReplaced),
               MusicEndingName(MusicEnding::kStartFailed));
    } else if (op == "derive") {
        const char* csv = argc > 2 ? argv[2] : "";
        MusicEndingFacts facts;
        facts.played = Has(csv, "played");
        facts.drained = Has(csv, "drained");
        facts.cancelled = Has(csv, "cancelled");
        facts.replaced = Has(csv, "replaced");
        facts.attempted_restart = Has(csv, "restart");
        printf("%s", MusicEndingName(DeriveMusicEnding(facts)));
    } else if (op == "pos") {
        /* pos <seconds> <live|finite> [known|unknown]：位点字段格式是串口断言的
           口径，与 pipe: 周期行的整秒不同（锚点行要一位小数）。第三参是
           have_position：unknown 必须写 none，不能冒充 0.0。 */
        char buf[32];
        const bool have_position = (argc < 5) || strcmp(argv[4], "unknown") != 0;
        WritePositionField(atof(argv[2]), strcmp(argv[3], "live") == 0,
                           have_position, buf, sizeof(buf));
        printf("%s", buf);
    } else if (op == "screens") {
        printf("%s %s %s %s %s %s",
               MusicEndingScreenName(MusicEndingScreenOf(MusicEnding::kCompleted)),
               MusicEndingScreenName(MusicEndingScreenOf(MusicEnding::kInterrupted)),
               MusicEndingScreenName(MusicEndingScreenOf(MusicEnding::kResumeFailed)),
               MusicEndingScreenName(MusicEndingScreenOf(MusicEnding::kStopped)),
               MusicEndingScreenName(MusicEndingScreenOf(MusicEnding::kReplaced)),
               MusicEndingScreenName(MusicEndingScreenOf(MusicEnding::kStartFailed)));
    } else if (op == "cue" || op == "failure" || op == "name" || op == "screen") {
        MusicEnding ending = MusicEnding::kCompleted;
        if (!LookupEnding(argc > 2 ? argv[2] : "", &ending)) {
            fprintf(stderr, "unknown ending: %s\n", argc > 2 ? argv[2] : "");
            return 2;
        }
        if (op == "cue") printf("%s", CueName(MusicEndingCue(ending)));
        else if (op == "failure") printf("%d", MusicEndingIsFailure(ending) ? 1 : 0);
        else if (op == "screen") printf("%s", ScreenName(MusicEndingScreenOf(ending)));
        else printf("%s", MusicEndingName(ending));
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


class MusicEndingHostTest(unittest.TestCase):
    """编译一次，之后按 op 调用。"""

    binary = None

    @classmethod
    def setUpClass(cls):
        workdir = Path(tempfile.mkdtemp(prefix="music_ending_test_"))
        driver = workdir / "driver.cc"
        driver.write_text(DRIVER, encoding="utf-8")
        cls.binary = workdir / "driver"
        cls.workdir = workdir
        cmd = [_compiler(), "-std=c++17", "-I", str(MAIN_DIR), str(driver),
               str(MUSIC_ENDING_CC), "-o", str(cls.binary)]
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

    def derive(self, csv=""):
        return self.run_op("derive", csv)

    # ── 推导：五个事实位 → 一个结局 ──────────────────────────────
    def test_natural_completion(self):
        """出过声 + 缓冲播空 + 无人取消 = 自然播完。"""
        self.assertEqual(self.derive("played,drained"), "completed")

    def test_link_interruption_stays_interrupted(self):
        """出过声但流没播空（读失败/解码失败）= 链路中断。"""
        self.assertEqual(self.derive("played"), "interrupted")

    def test_resume_failed_after_played(self):
        """续播尝试失败（旧连接只出不进、重连失败）= 续播失败。"""
        self.assertEqual(self.derive("played,restart"), "resume_failed")

    def test_user_stop_after_played(self):
        """出过声 + 被取消 = 用户主动停止（不是故障）。"""
        self.assertEqual(self.derive("played,cancelled"), "stopped")

    def test_replaced_beats_everything_else(self):
        """换歌：旧会话被替换，无论播没播过、取消与否，都不算旧会话的收场。"""
        for csv in ("replaced", "played,replaced,cancelled",
                    "played,replaced,cancelled,restart", "played,replaced,drained",
                    "cancelled,replaced", "played,drained,replaced"):
            self.assertEqual(self.derive(csv), "replaced", csv)

    def test_never_started_is_start_failed(self):
        """从未出声：起流失败/超时/空洞的流——不是"中断"，也不是"停止"。"""
        for csv in ("", "cancelled", "restart", "cancelled,restart",
                    "drained", "drained,cancelled"):
            self.assertEqual(self.derive(csv), "start_failed", csv)

    def test_drained_beats_raced_cancel(self):
        """播空与取消的赛跑：真播完了就报 completed，不能因为按键晚到一步而误报。"""
        self.assertEqual(self.derive("played,drained,cancelled"), "completed")

    def test_cancelled_beats_resume_failed(self):
        """用户停止发生在续播重连之后时，用户停止优先（不该听见故障音）。"""
        self.assertEqual(self.derive("played,cancelled,restart"), "stopped")

    # ── 命名：遥测契约 ───────────────────────────────────────────
    def test_names_are_stable_and_distinct(self):
        names = self.run_op("names").split()
        self.assertEqual(names, ["completed", "interrupted", "resume_failed",
                                 "stopped", "replaced", "start_failed"])
        self.assertEqual(len(set(names)), len(names))

    def test_name_is_never_empty_and_has_no_spaces(self):
        """遥测锚点是 `key=value` 空格分隔的；名字里不能有空格，否则解析会歪。"""
        for name in self.run_op("names").split():
            self.assertTrue(name)
        for name in ("completed", "interrupted", "resume_failed", "stopped",
                     "replaced", "start_failed"):
            self.assertEqual(self.run_op("name", name), name)

    # ── 反馈：音效与故障标记 ─────────────────────────────────────
    def test_two_audible_endings_use_different_tones(self):
        """issue #7 的核心：自然播完与链路中断给**不同**的音，且都出声。"""
        done = self.run_op("cue", "completed")
        broken = self.run_op("cue", "interrupted")
        self.assertNotEqual(done, broken)
        self.assertNotEqual(done, "none")
        self.assertNotEqual(broken, "none")

    def test_user_stop_and_replaced_never_warn(self):
        """用户主动停止（按钮/语音/换歌）永远不报故障音。"""
        self.assertEqual(self.run_op("cue", "stopped"), "none")
        self.assertEqual(self.run_op("cue", "replaced"), "none")

    def test_start_failed_never_warns(self):
        """起流失败已经由 StartMusicNow 的日志报出来了，别再叠一声故障音。"""
        self.assertEqual(self.run_op("cue", "start_failed"), "none")

    def test_resume_failed_warns(self):
        self.assertEqual(self.run_op("cue", "resume_failed"), "warning")

    # ── 屏幕文案锚点：issue #12「续播失败除提示音外还要有屏幕说明」──────
    def test_resume_failed_has_its_own_screen_text(self):
        """续播失败在屏幕上必须与链路中断可区分。

        issue #12 的验收：续播失败走「提示音 + 屏幕说明」；父 spec 明确
        「用户不应把续播失败听成歌放完了」，而可区分的下一步是——用户与
        串口断言都要能分出「续播没接上」与「链路断了」。
        """
        self.assertNotEqual(self.run_op("screen", "resume_failed"),
                            self.run_op("screen", "interrupted"))

    def test_resume_failed_screen_differs_from_completed_too(self):
        """对自然播完当然也要可分（issue 原文的「与自然播完都可区分」）。"""
        self.assertNotEqual(self.run_op("screen", "resume_failed"),
                            self.run_op("screen", "completed"))

    def test_warning_endings_carry_distinct_screens(self):
        """同一提示音（alert）的两个结局必须靠屏幕分开——音一样，屏不能一样。"""
        screens = [self.run_op("screen", name)
                   for name in ("interrupted", "resume_failed")]
        self.assertEqual(len(set(screens)), 2, "两种告警收场的屏幕文案不得相同")
        self.assertNotIn("none", screens)

    def test_screen_silence_is_none_for_silent_endings(self):
        """用户主动停止之外无声的收场不应凭空给屏幕文案。

        stopped 有屏幕文案（用户要看见「已停止」），replaced/start_failed 没有
        （屏幕归新会话 / 起播那一刻已报错）——三者与提示音分支一一对应。
        """
        self.assertEqual(self.run_op("screen", "replaced"), "none")
        self.assertEqual(self.run_op("screen", "start_failed"), "none")
        self.assertNotEqual(self.run_op("screen", "stopped"), "none")

    def test_screen_names_are_stable_and_distinct(self):
        """屏幕锚点值是遥测契约（`Music feedback: … screen=…`）：非空、无空格。"""
        names = self.run_op("screens").split()
        self.assertEqual(names, ["ended", "interrupted", "resume_failed",
                                 "stopped", "none", "none"])
        for name in names:
            self.assertTrue(name)
            self.assertNotIn(" ", name)

    def test_failure_flag_marks_real_failures_only(self):
        self.assertEqual(self.run_op("failure", "interrupted"), "1")
        self.assertEqual(self.run_op("failure", "resume_failed"), "1")
        self.assertEqual(self.run_op("failure", "start_failed"), "1")
        self.assertEqual(self.run_op("failure", "completed"), "0")
        self.assertEqual(self.run_op("failure", "stopped"), "0")
        self.assertEqual(self.run_op("failure", "replaced"), "0")

    # ── 位点字段：三处锚点共用的格式 ─────────────────────────
    def test_position_field_marks_live(self):
        """直播流报 live：位点对它无意义，报个数字就是撒谎。"""
        self.assertEqual(self.run_op("pos", "0", "live"), "live")
        self.assertEqual(self.run_op("pos", "123.4", "live"), "live")

    def test_position_field_uses_one_decimal(self):
        """有限内容一位小数：spec 的 ±0.5s 验收缝靠它，整秒量化够不着。"""
        self.assertEqual(self.run_op("pos", "12.34", "finite"), "12.3s")
        self.assertEqual(self.run_op("pos", "0", "finite"), "0.0s")
        self.assertEqual(self.run_op("pos", "269", "finite"), "269.0s")

    def test_position_field_marks_unknown_as_none(self):
        """位点未知写 none，不能写 0.0——0.0 会被读成「刚开始放」，那是撒谎。

        这条是 issue #9 的硬约束（直播不带位点、位点未知不冒充 0.0）在
        格式层的落点：三种取值各有其字面量，且互不相同。
        """
        self.assertEqual(self.run_op("pos", "0", "finite", "unknown"), "none")
        self.assertEqual(self.run_op("pos", "83.4", "finite", "unknown"), "none")
        # 已知位点仍是数字；直播流即便带数字也写 live（形态优先）。
        self.assertEqual(self.run_op("pos", "83.4", "finite", "known"), "83.4s")
        self.assertEqual(self.run_op("pos", "83.4", "live", "known"), "live")
        self.assertEqual(self.run_op("pos", "83.4", "live", "unknown"), "live")
        self.assertEqual(self.run_op("pos", "0", "finite", "known"), "0.0s")

    def test_position_field_is_shared_by_all_anchors(self):
        """所有 `pos=` 锚点必须走同一个格式化函数，不许再抄一份。

        这是结构性不变量，不是行为断言：被它挡住的是「某处锚点把 live 写成
        数字」「某处把未知位点写成 0.0」——单条锚点的行为测试互相看不见对方，
        缺陷在「几处是否一致」这一层。

        判据只数**函数体里的调用**（行首是空白 + 标识符），注释与文档提及不算
        ——否则将来有人在注释里写一句 `WritePositionField` 就会把测试弄红，
        那是假失败，会让后来人把它删掉。
        """
        import subprocess
        hits = subprocess.run(
            ["grep", "-rn", "WritePositionField", str(ROOT / "main")],
            capture_output=True, text=True).stdout.strip().splitlines()
        calls = []
        for hit in hits:
            path, _, rest = hit.partition(":")
            _, _, text = rest.partition(":")
            code = text.lstrip()
            if not path.endswith(".cc"):
                continue
            if code.startswith("//") or code.startswith("*"):
                continue          # 注释/文档提及
            if "void WritePositionField" in code:
                continue          # 定义
            calls.append(hit)
        # 五个锚点：播放器收场、应用层跳过分支、反馈行、会话推送、屏幕出口
        # （issue #11 新增的那处——屏幕位点与其余锚点同一个 `live`/未知口径，
        # 否则「直播不报位点」就只在一半锚点上成立）。
        self.assertEqual(len(calls), 5,
                         "锚点格式化应恰好五处调用，实际：\n" + "\n".join(calls))
        for name in ("music_player.cc", "application.cc"):
            self.assertTrue(any(name in c for c in calls),
                            "应覆盖 %s 的锚点，实际：\n%s" % (name, "\n".join(calls)))

    def test_cue_none_only_for_non_played_endings(self):
        """见过声才可能有提示音；没出声的收场一律安静（用户没听到东西，别吓他）。"""
        for name in ("stopped", "replaced", "start_failed"):
            self.assertEqual(self.run_op("cue", name), "none", name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
