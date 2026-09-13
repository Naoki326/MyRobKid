#!/usr/bin/env python3
"""speaking 看门狗纯逻辑契约测试（issue #33：tts stop 丢失时设备不该永久沉默）。

为什么用宿主编译测：`main/audio/speaking_watchdog.{h,cc}` 一个 ESP 头文件都不引，
只吃两个事实位（是否仍在 speaking、TTS 音频是否已全放完）。它管的是**判据**——
「什么时候该认为对端不会再发 tts stop 了」。判错了就是两种真机故障：
判早了会在正常长回答中间把设备踢出 speaking，判晚了就是本票要修的「永久沉默」，
两种都只在真机上才暴露。

契约五条：
  1. 判据不是「speaking 太久」，而是「**播放已放完**却仍停在 speaking」——
     播放队列非空（还在出声）时**永不**计数，所以连续 20+ 秒的长回答不会被
     误打断（issue #33 验收第二条）；
  2. 只有连续 N 拍满足条件才触发：中途只要有一拍不满足（又出声了 / 已退出
     speaking），计数清零，从头再来——句间间隙不会累积成误触；
  3. 触发**恰好一次**：返回 kDrainedUnstopped 的那一拍把计数清零，之后要重新数满 N 拍
     才会再次触发（避免每拍刷一条日志）；
  4. 未满足条件时计数恒为 0（不是「保持」），离开 speaking 不残留旧计数；
  5. N 是构造参数且必须为正；N=1 时首个满足条件的拍即触发。

运行：cd firmware && python3 -m unittest scripts.tests.test_speaking_watchdog -v
（需要宿主 C++ 编译器；不需要设备、不跑 idf.py、不联网。）
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WATCHDOG_CC = ROOT / "main" / "audio" / "speaking_watchdog.cc"
MAIN_DIR = ROOT / "main"

DRIVER = r"""
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include "audio/speaking_watchdog.h"

static const char* ActionName(SpeakingWatchdogAction action) {
    switch (action) {
        case SpeakingWatchdogAction::kNone: return "none";
        case SpeakingWatchdogAction::kDrainedUnstopped: return "drained_unstopped";
    }
    return "?";
}

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <op> [args]\n", argv[0]);
        return 2;
    }
    const std::string op = argv[1];

    if (op == "tick") {
        /* tick <threshold> <speaking:0|1> <drained:0|1> [...pairs]
         * 每一对事实消费一拍，逐拍打印返回值；末行打印最终计数。 */
        SpeakingWatchdog dog(atoi(argv[2]));
        for (int i = 3; i + 1 < argc; i += 2) {
            bool speaking = strcmp(argv[i], "1") == 0;
            bool drained = strcmp(argv[i + 1], "1") == 0;
            printf("%s\n", ActionName(dog.Tick(speaking, drained)));
        }
        printf("elapsed=%d\n", dog.ElapsedTicks());
    } else if (op == "reset") {
        /* reset <threshold> <speaking> <drained> <reset_at>
         * 消费 reset_at 拍后 Reset()，再消费两拍，验证计数真的归零。 */
        SpeakingWatchdog dog(atoi(argv[2]));
        bool speaking = strcmp(argv[3], "1") == 0;
        bool drained = strcmp(argv[4], "1") == 0;
        for (int i = 0; i < atoi(argv[5]); i++) dog.Tick(speaking, drained);
        dog.Reset();
        printf("after_reset=%d\n", dog.ElapsedTicks());
        dog.Tick(speaking, drained);
        printf("after_tick=%d\n", dog.ElapsedTicks());
    } else {
        fprintf(stderr, "unknown op: %s\n", op.c_str());
        return 2;
    }
    return 0;
}
"""


def _compile() -> str:
    tmp = Path(tempfile.mkdtemp(prefix="speaking_watchdog_test_"))
    src = tmp / "driver.cc"
    src.write_text(DRIVER)
    binary = tmp / "driver"
    compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        raise unittest.SkipTest("no host C++ compiler found")
    subprocess.run(
        [compiler, "-std=c++17", "-I", str(MAIN_DIR), str(src), str(WATCHDOG_CC),
         "-o", str(binary)],
        check=True, capture_output=True, text=True)
    return str(binary)


class SpeakingWatchdogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = _compile()

    def run_op(self, *args):
        proc = subprocess.run([self.binary, *args], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip().splitlines()

    def test_expires_only_after_n_consecutive_drained_speaking_ticks(self):
        """判据成立要连续数满 N 拍：第 N 拍才 drained_unstopped，前 N-1 拍都是 none。"""
        lines = self.run_op("tick", "10", *(["1", "1"] * 10))
        self.assertEqual(lines[:9], ["none"] * 9)
        self.assertEqual(lines[9], "drained_unstopped")

    def test_long_reply_is_never_interrupted(self):
        """播放队列非空 = 还在出声，再久也不计数（长回答不被误打断）。"""
        lines = self.run_op("tick", "10", *(["1", "0"] * 60))
        self.assertEqual(lines[:60], ["none"] * 60)
        self.assertEqual(lines[-1], "elapsed=0")

    def test_inter_sentence_gap_does_not_accumulate(self):
        """句间间隙（几拍 drained 后又出声）不累积：计数被出声那一拍清零。"""
        # 9 拍 drained（差一拍到点），然后一连串仍在出声，再给 9 拍 drained
        # —— 若计数没被清零，第二个 9 拍就会提前触发。
        pairs = ["1", "1"] * 9 + ["1", "0"] * 20 + ["1", "1"] * 9
        lines = self.run_op("tick", "10", *pairs)
        self.assertNotIn("drained_unstopped", lines[:-1])

    def test_fires_exactly_once_then_recounts(self):
        """触发后计数清零：不会每拍都刷一条日志。"""
        lines = self.run_op("tick", "5", *(["1", "1"] * 12))
        self.assertEqual([i for i, v in enumerate(lines[:-1]) if v == "drained_unstopped"],
                         [4, 9])

    def test_leaving_speaking_clears_the_counter(self):
        """退出 speaking 一拍即清零：不残留旧计数到下一次 speaking。"""
        pairs = ["1", "1"] * 9 + ["0", "1"] + ["1", "1"] * 9
        lines = self.run_op("tick", "10", *pairs)
        self.assertNotIn("drained_unstopped", lines[:-1])

    def test_threshold_of_one_fires_on_first_qualifying_tick(self):
        lines = self.run_op("tick", "1", "1", "1")
        self.assertEqual(lines[0], "drained_unstopped")

    def test_reset_clears_accumulated_ticks(self):
        lines = self.run_op("reset", "10", "1", "1", "7")
        self.assertEqual(lines, ["after_reset=0", "after_tick=1"])

    def test_elapsed_ticks_reflects_only_qualifying_ticks_since_last_reset(self):
        # (1,1) 计数 → (1,0) 清零 → (1,1)(1,1) 归 2：计数是「自上次清零以来」的
        # 连续拍数，不是「历史满足过的拍数」。
        lines = self.run_op("tick", "10", "1", "1", "1", "0", "1", "1", "1", "1")
        self.assertEqual(lines[-1], "elapsed=2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
