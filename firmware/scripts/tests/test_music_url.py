#!/usr/bin/env python3
"""music_url 纯逻辑契约测试（issue #2 的解析 + issue #4 的替换语义）。

为什么用宿主编译测：`main/audio/music_url.cc` 只依赖 <string>，是整条音乐链路里
唯一能脱离设备验证的纯逻辑。而它管的是**播放地址的改写**——改错一个字符就是
「设备连不上/放错段」，且错误只在真机上才暴露（play_music 是假成功，见
CONTEXT.md）。续播要用它按位点重拼地址，所以把它钉住。

契约三条：
  1. 解析：title/author/duration/form 从查询串读出；ss 单独读回（宽容口径，
     非数字/负值/缺失 → 0）；
  2. 追加（AppendMusicStart）：start <= 0 时逐字节原样返回；
  3. 替换（RemoveMusicStart + AppendMusicStart）：摘掉 ss= 后重拼，**恰好一个
     ss=**，其余参数逐字节不变。AppendMusicStart 本身是追加语义，直接重拼会
     让地址里出现两个 ss=（谁生效取决于上游解析顺序）——续播必须走替换。

运行：cd firmware && python3 -m unittest scripts.tests.test_music_url -v
（需要宿主 C++ 编译器；不需要设备、不跑 idf.py、不联网。）
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MUSIC_URL_CC = ROOT / "main" / "audio" / "music_url.cc"
MAIN_DIR = ROOT / "main"

DRIVER = r"""
#include <cstdio>
#include <cstdlib>
#include <string>
#include "audio/music_url.h"

int main(int argc, char** argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s <op> <url> [n]\n", argv[0]);
        return 2;
    }
    std::string op = argv[1];
    std::string url = argv[2];
    if (op == "remove") {
        fputs(RemoveMusicStart(url).c_str(), stdout);
    } else if (op == "append") {
        fputs(AppendMusicStart(url, argc > 3 ? atoi(argv[3]) : 0).c_str(), stdout);
    } else if (op == "parse") {
        printf("%d", ParseMusicStartSeconds(url));
    } else if (op == "meta") {
        MusicContentMeta m = ParseMusicContentMeta(url);
        printf("title=%s|author=%s|duration=%d|live=%d", m.title.c_str(),
               m.author.c_str(), m.duration_s, m.live ? 1 : 0);
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


class MusicUrlHostTest(unittest.TestCase):
    """编译一次，之后按 op 调用。"""

    binary = None

    @classmethod
    def setUpClass(cls):
        workdir = Path(tempfile.mkdtemp(prefix="music_url_test_"))
        driver = workdir / "driver.cc"
        driver.write_text(DRIVER, encoding="utf-8")
        cls.binary = workdir / "driver"
        cls.workdir = workdir
        cmd = [_compiler(), "-std=c++17", "-I", str(MAIN_DIR), str(driver),
               str(MUSIC_URL_CC), "-o", str(cls.binary)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(
                "host compile failed:\n" + result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "workdir", None):
            shutil.rmtree(cls.workdir, ignore_errors=True)

    def run_op(self, op, url, *extra):
        result = subprocess.run([str(self.binary), op, url, *map(str, extra)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    # ── AppendMusicStart（issue #2 的既有行为，别改坏）───────────────
    def test_append_zero_returns_url_byte_for_byte(self):
        url = ("http://mac.local:8080/music/stream?src=https%3A%2F%2Fcdn%2Fa.mp3"
               "&title=%E6%99%B4%E5%A4%A9&form=finite")
        self.assertEqual(self.run_op("append", url, 0), url)
        self.assertEqual(self.run_op("append", url, -5), url)

    def test_append_adds_ss_with_correct_separator(self):
        self.assertEqual(self.run_op("append", "http://h/stream", 30),
                         "http://h/stream?ss=30")
        self.assertEqual(self.run_op("append", "http://h/stream?src=x", 30),
                         "http://h/stream?src=x&ss=30")

    # ── RemoveMusicStart（issue #4 新增，替换语义的另一半）───────────
    def test_remove_without_query_or_ss_is_noop(self):
        for url in ("http://h/stream",
                    "http://h/stream?src=x&title=y",
                    "http://h/stream#frag",
                    "http://h/stream?src=x#frag"):
            self.assertEqual(self.run_op("remove", url), url)

    def test_remove_drops_ss_and_keeps_other_params_byte_for_byte(self):
        # 其余参数（含 percent 编码与 %26 形式的 '&'）必须逐字节不变。
        url = ("http://h/stream?src=https%3A%2F%2Fc%2Fa.mp3%3Fx%3D1%26y%3D2"
               "&ss=42&title=%E6%99%B4%E5%A4%A9&form=finite")
        self.assertEqual(
            self.run_op("remove", url),
            "http://h/stream?src=https%3A%2F%2Fc%2Fa.mp3%3Fx%3D1%26y%3D2"
            "&title=%E6%99%B4%E5%A4%A9&form=finite")

    def test_remove_ss_only_drops_the_question_mark_too(self):
        self.assertEqual(self.run_op("remove", "http://h/stream?ss=42"),
                         "http://h/stream")
        self.assertEqual(self.run_op("remove", "http://h/stream?ss=42#frag"),
                         "http://h/stream#frag")

    def test_remove_keeps_fragment_and_trailing_params(self):
        self.assertEqual(self.run_op("remove", "http://h/s?ss=7&a=b#f"),
                         "http://h/s?a=b#f")
        self.assertEqual(self.run_op("remove", "http://h/s?a=b&ss=7#f"),
                         "http://h/s?a=b#f")

    def test_remove_drops_every_ss_occurrence(self):
        # 重复 ss= 是坏输入；摘干净再拼一次，恢复才是可预期的。
        self.assertEqual(self.run_op("remove", "http://h/s?ss=1&a=b&ss=2"),
                         "http://h/s?a=b")

    def test_remove_survives_empty_and_dangling_pairs(self):
        # 空对/悬空键不得把其它参数吃掉，也不得多出 '&'。
        self.assertEqual(self.run_op("remove", "http://h/s?ss=1&&a=b&"),
                         "http://h/s?a=b")
        self.assertEqual(self.run_op("remove", "http://h/s?a=b&ss"),
                         "http://h/s?a=b")
        self.assertEqual(self.run_op("remove", "http://h/s?a=b&ss="),
                         "http://h/s?a=b")

    def test_replace_semantics_yields_exactly_one_ss(self):
        """续播的地址重拼：Remove → Append，恰好一个 ss=，值是新的起点。"""
        original = ("http://h/stream?src=https%3A%2F%2Fc%2Fa.mp3&ss=120"
                    "&title=%E6%99%B4%E5%A4%A9&form=finite")
        base = self.run_op("remove", original)
        rebuilt = self.run_op("append", base, 118)
        self.assertEqual(rebuilt.count("ss="), 1)
        self.assertEqual(self.run_op("parse", rebuilt), "118")
        self.assertIn("title=%E6%99%B4%E5%A4%A9", rebuilt)
        self.assertIn("form=finite", rebuilt)
        self.assertNotIn("ss=120", rebuilt)

    def test_restart_at_zero_keeps_url_without_start_param(self):
        """回退点被 clamp 到 0（或直播流重连）时，地址里不该带 ss=0。"""
        base = self.run_op("remove", "http://h/s?ss=1&a=b")
        rebuilt = self.run_op("append", base, 0)
        self.assertEqual(rebuilt, "http://h/s?a=b")
        self.assertNotIn("ss=", rebuilt)

    # ── 解析（宽容口径）────────────────────────────────────────────
    def test_parse_start_seconds(self):
        self.assertEqual(self.run_op("parse", "http://h/s"), "0")
        self.assertEqual(self.run_op("parse", "http://h/s?ss=42"), "42")
        self.assertEqual(self.run_op("parse", "http://h/s?a=b&ss=42&c=d"), "42")
        # 非数字 / 小数 / 负号 / 空：一律 0（与 duration 同一宽容口径）
        for bad in ("ss=12.5", "ss=-3", "ss=abc", "ss=", "ss=1x"):
            self.assertEqual(self.run_op("parse", f"http://h/s?{bad}"), "0", bad)

    def test_parse_meta_ignores_unknown_and_decodes_values(self):
        url = ("http://h/s?src=x&title=%E5%91%8A%E7%99%BD%E6%B0%94%E7%90%83"
               "&author=%E5%91%A8%E6%9D%B0%E4%BC%A6&duration=269&form=finite")
        self.assertEqual(self.run_op("meta", url),
                         "title=告白气球|author=周杰伦|duration=269|live=0")
        self.assertEqual(self.run_op("meta", "http://h/s?form=live"),
                         "title=|author=|duration=0|live=1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
