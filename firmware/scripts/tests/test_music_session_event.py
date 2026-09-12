#!/usr/bin/env python3
"""music_session_event 线协议契约测试（issue #9：设备把状态变更推给服务端）。

为什么用宿主编译测：`main/audio/music_session_event.{h,cc}` 只引 <string> 与
<cstdio>，不含任何 ESP 依赖。它管的是**设备 → 服务端的线协议**——错了不会崩，
只会让机器人答错「这歌谁唱的」「暂停了吗」，而那类错只在真机上、隔着一次模型
调用才看得见。把它钉住就能在没有设备的情况下验证载荷形状（真机回环见
tools/latency_loop.py 的 --music 与 tools/README）。

契约四条（对应 spec 的验收条款）:
  1. 通知形状：JSON-RPC 通知（无 id）、method="music.session"、字段与服务端
     music_session.py 的 _parse 一致（event/state/title/author/form/位点/总量）；
  2. 直播流不带 position_s / duration_s（硬约束——带上就是撒谎）；
  3. 位点未知不写 position_s（「不知道放到哪」≠「放到 0:00」）；
  4. JSON 转义：曲目名带 `"` / `\\` 时编出的是合法 JSON（不转义会解析失败、
     状态静默丢掉）。

运行：cd firmware && python3 -m unittest scripts.tests.test_music_session_event -v
（需要宿主 C++ 编译器；不需要设备、不跑 idf.py、不联网。）
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MUSIC_SESSION_CC = ROOT / "main" / "audio" / "music_session_event.cc"
MAIN_DIR = ROOT / "main"

DRIVER = r"""
#include <cstdio>
#include <cstring>
#include <string>
#include "audio/music_session_event.h"

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

int main(int argc, char** argv) {
    if (argc < 2) return 2;
    const std::string op = argv[1];
    const char* csv = argc > 2 ? argv[2] : "";
    MusicSessionEventFacts facts;
    facts.state  = Has(csv, "state")    ? "playing" : "paused_user";
    facts.event  = Has(csv, "event")    ? "started" : "paused";
    facts.title  = Has(csv, "title")    ? "晴天" : "";
    facts.author = Has(csv, "author")   ? "周杰伦" : "";
    facts.live   = Has(csv, "live");
    facts.have_position = Has(csv, "pos");
    facts.position_s = 83.4;
    facts.duration_s = Has(csv, "duration") ? 269 : 0;
    if (Has(csv, "quote")) {
        facts.title = "He said \"hi\" \\ bye";
    }
    if (Has(csv, "terminal")) {
        facts.state = "completed";
        facts.event = "completed";
    }

    if (op == "build") {
        printf("%s\n", BuildMusicSessionNotification(facts).c_str());
        return 0;
    }
    fprintf(stderr, "unknown op: %s\n", op.c_str());
    return 2;
}
"""


def _compiler():
    for candidate in ("c++", "clang++", "g++"):
        path = shutil.which(candidate)
        if path:
            return path
    raise unittest.SkipTest("no host C++ compiler found (c++/clang++/g++)")


class MusicSessionEventHostTest(unittest.TestCase):
    """编译一次，之后按 op 调用。"""

    @classmethod
    def setUpClass(cls):
        workdir = Path(tempfile.mkdtemp(prefix="music_session_event_test_"))
        driver = workdir / "driver.cc"
        driver.write_text(DRIVER, encoding="utf-8")
        cls.binary = workdir / "driver"
        cls.workdir = workdir
        cmd = [_compiler(), "-std=c++17", "-I", str(MAIN_DIR), str(driver),
               str(MUSIC_SESSION_CC), "-o", str(cls.binary)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(
                "host compile failed:\n" + result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "workdir", None):
            shutil.rmtree(cls.workdir, ignore_errors=True)

    def build(self, csv=""):
        result = subprocess.run([str(self.binary), "build", csv],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    # ── 通知形状 ─────────────────────────────────────────────────
    def test_is_a_jsonrpc_notification_without_id(self):
        note = self.build("state,event,title,author,pos,duration")
        self.assertEqual(note["jsonrpc"], "2.0")
        self.assertEqual(note["method"], "music.session")
        self.assertNotIn("id", note, "通知不应带 id（不期待响应）")

    def test_params_carry_state_event_and_track(self):
        note = self.build("state,event,title,author,pos,duration")
        params = note["params"]
        self.assertEqual(params["state"], "playing")
        self.assertEqual(params["event"], "started")
        self.assertEqual(params["title"], "晴天")
        self.assertEqual(params["author"], "周杰伦")
        self.assertEqual(params["form"], "finite")

    def test_finite_content_carries_position_and_duration(self):
        params = self.build("state,event,title,author,pos,duration")["params"]
        self.assertEqual(params["position_s"], 83.4)
        self.assertEqual(params["duration_s"], 269)

    def test_terminal_state_is_carried(self):
        params = self.build("title,author,pos,terminal")["params"]
        self.assertEqual(params["state"], "completed")
        self.assertEqual(params["event"], "completed")

    # ── 直播流：不带位点与总量（硬约束）────────────────────────────
    def test_live_stream_omits_position_and_duration(self):
        params = self.build("state,event,title,author,live,pos,duration")["params"]
        self.assertEqual(params["form"], "live")
        self.assertNotIn("position_s", params)
        self.assertNotIn("duration_s", params)

    # ── 位点未知 ≠ 位点为 0 ──────────────────────────────────────
    def test_missing_position_is_omitted(self):
        """设备没报位点时不许写 position_s（0.0 会被读成「已播放 0:00」）。"""
        params = self.build("state,event,title,author,duration")["params"]
        self.assertNotIn("position_s", params)
        self.assertEqual(params["duration_s"], 269)

    def test_missing_duration_is_omitted(self):
        params = self.build("state,event,title,author,pos")["params"]
        self.assertEqual(params["position_s"], 83.4)
        self.assertNotIn("duration_s", params)

    # ── JSON 转义 ────────────────────────────────────────────────
    def test_quotes_and_backslashes_are_escaped(self):
        """曲目名带引号/反斜杠时编出的仍是合法 JSON（否则状态静默丢掉）。"""
        note = self.build("state,event,title,author,pos,quote")
        self.assertEqual(note["params"]["title"], 'He said "hi" \\ bye')

    # ── 服务端能直接吃这条载荷 ────────────────────────────────────
    def test_payload_matches_server_line_protocol(self):
        """字段名与服务端 music_session.py 的 _parse 一字不差。"""
        params = self.build("state,event,title,author,pos,duration")["params"]
        self.assertEqual(set(params.keys()),
                         {"event", "state", "title", "author", "form",
                          "position_s", "duration_s"})
        for key in ("event", "state", "title", "author", "form"):
            self.assertIsInstance(params[key], str)
        self.assertIsInstance(params["position_s"], float)
        self.assertIsInstance(params["duration_s"], int)


if __name__ == "__main__":
    unittest.main()
