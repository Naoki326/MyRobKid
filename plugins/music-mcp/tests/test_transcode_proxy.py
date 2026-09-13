#!/usr/bin/env python3
"""转码代理 /stream 的起点（ss）与裁剪时长（t）契约测试。

实测事实（2026-09-12，issue #2，勿凭直觉推翻）：
  - 输入定位（-ss 置于 -i 之前）有效且快：同一首歌全量转码 2.08s、
    从 60s 定位 1.09s，产出时长与「总时长 − 60」偏差 0.0s。
  - 输出定位会先解码丢弃，既慢又没有这个精度——契约固定为输入定位。
  - 起点超出源时长：交给 ffmpeg 自然产出空流（200 + audio/mpeg + 头部字节），
    本文件把该行为钉死为断言。
  - 非法起点（负数、非数字、非有限数）必须 400，不能是 200 空流。
  - 上游打不开（404 / 拒连 / 防盗链读到 HTML）：必须在首字节窗口内给出 **502**
    + JSON 错误，不能是 200 空流（设备收到 200 零数据只会干等 10s，还分不清
    音源坏还是系统坏）。
  - UA 策略：默认不发浏览器 UA（Calm Radio 这类见浏览器 UA 即 302 到网页），
    仅调用方传 referer 时才附浏览器 UA（B 站 CDN 这类需要 UA+Referer 伪装）。

运行：~/.hermes/hermes-agent/venv/bin/python -m unittest \
      plugins/music-mcp/tests/test_transcode_proxy.py -v
（依赖 fastapi/httpx，与线上代理同一 venv；ffmpeg/ffprobe 取自 PATH。）
"""
import asyncio
import http.server
import io
import json
import math
import os
import re
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

import httpx

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

import qqmusic_auth_server  # noqa: E402

FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

# 夹具源时长（秒）；断言容差与 issue #2 验收标准一致：±3s。
SOURCE_DURATION = 90.0
TOLERANCE = 3.0


def have_ffmpeg() -> bool:
    return bool(shutil.which(FFMPEG) if os.sep not in FFMPEG else os.path.exists(FFMPEG))


class _RangeFileHandler(http.server.BaseHTTPRequestHandler):
    """支持 Range 的静态文件服务：ffmpeg 输入定位依赖 Range 取流。"""

    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        name = self.path.split("?")[0].lstrip("/")
        path = self.server.fixture_dir / name
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        rng = self.headers.get("Range") or ""
        m = re.search(r"bytes=(\d+)-(\d*)", rng)
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else len(data) - 1
            end = min(end, len(data) - 1)
            chunk = data[start:end + 1] if start <= end else b""
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.send_header("Accept-Ranges", "bytes")
        else:
            chunk = data
            self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)

    def log_message(self, *args):  # 静音测试输出
        pass


class TranscodeSeekContract(unittest.TestCase):
    """代理 /stream 的起点/裁剪契约（真实 ffmpeg 端到端）。"""

    @classmethod
    def setUpClass(cls):
        if not have_ffmpeg():
            raise unittest.SkipTest("ffmpeg 不可用")
        cls.tmp = tempfile.TemporaryDirectory(prefix="music-proxy-test-")
        fixture_dir = Path(cls.tmp.name)
        # 90s 正弦波 mp3（32k），本地生成，不依赖外网。
        subprocess.run(
            [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", f"sine=frequency=440:duration={SOURCE_DURATION:g}",
             "-b:a", "32k", str(fixture_dir / "tone.mp3")],
            check=True,
        )
        cls.fixture_dir = fixture_dir

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        srv = Server(("127.0.0.1", 0), _RangeFileHandler)
        srv.fixture_dir = fixture_dir
        cls.srv = srv
        cls.srv_thread = threading.Thread(target=srv.serve_forever, daemon=True)
        cls.srv_thread.start()
        cls.src_url = f"http://127.0.0.1:{srv.server_address[1]}/tone.mp3"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    # ── 基础设施 ──────────────────────────────────────────────────

    def _fetch(self, query: str):
        # lifespan 不启动（ASGITransport 默认不触发），凭证刷新循环不会跑。
        async def run():
            transport = httpx.ASGITransport(app=qqmusic_auth_server.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
                return await client.get(f"/stream?src={self.src_url}{query}")
        # SSRF 防护只放行公网源；测试源是本机回环，仅在测试进程内放行。
        with mock.patch.object(qqmusic_auth_server, "_src_host_is_safe", lambda u: True):
            return asyncio.run(run())

    @staticmethod
    def _probe_duration(data: bytes):
        if len(data) < 1024:
            return None  # 不足 1KB 视为空流，ffprobe 无意义
        # ffprobe 从 stdin（不可 seek）读 mp3 得不到时长，落临时文件再探测。
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(data)
            path = f.name
        try:
            p = subprocess.run(
                [FFPROBE, "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", path],
                capture_output=True,
            )
        finally:
            os.unlink(path)
        if p.returncode != 0:
            return None
        try:
            return float(p.stdout.decode().strip())
        except ValueError:
            return None

    # ── 契约断言 ──────────────────────────────────────────────────

    def test_no_seek_unchanged_full_duration(self):
        """不带起点：行为与改动前一致，产出全量时长。"""
        r = self._fetch("")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("audio/"))
        dur = self._probe_duration(r.content)
        self.assertIsNotNone(dur)
        self.assertAlmostEqual(dur, SOURCE_DURATION, delta=TOLERANCE)

    def test_seek_60_output_is_full_minus_60(self):
        """起点 60s：产出时长 ≈ 全量 − 60（±3s）。"""
        r = self._fetch("&ss=60")
        self.assertEqual(r.status_code, 200)
        dur = self._probe_duration(r.content)
        self.assertIsNotNone(dur)
        self.assertAlmostEqual(dur, SOURCE_DURATION - 60, delta=TOLERANCE)

    def test_seek_accepts_decimal_seconds(self):
        """起点支持小数秒。"""
        r = self._fetch("&ss=59.5")
        self.assertEqual(r.status_code, 200)
        dur = self._probe_duration(r.content)
        self.assertIsNotNone(dur)
        self.assertAlmostEqual(dur, SOURCE_DURATION - 59.5, delta=TOLERANCE)

    def test_seek_60_trim_10_output_is_10(self):
        """起点 60s + 裁剪 10s：产出时长 ≈ 10s。"""
        r = self._fetch("&ss=60&t=10")
        self.assertEqual(r.status_code, 200)
        dur = self._probe_duration(r.content)
        self.assertIsNotNone(dur)
        self.assertAlmostEqual(dur, 10.0, delta=TOLERANCE)

    def test_trim_without_seek(self):
        """只有裁剪时长：从头转出指定秒数。"""
        r = self._fetch("&t=10")
        self.assertEqual(r.status_code, 200)
        dur = self._probe_duration(r.content)
        self.assertIsNotNone(dur)
        self.assertAlmostEqual(dur, 10.0, delta=TOLERANCE)

    def test_invalid_seek_rejected_400(self):
        """非法起点（非数字/负数/非有限数）：明确 400，不能是 200 空流。"""
        for bad in ("abc", "-5", "NaN", "inf", "60s"):
            r = self._fetch(f"&ss={bad}")
            self.assertEqual(r.status_code, 400, f"ss={bad!r} 应 400")
            self.assertIn("error", r.json(), f"ss={bad!r} 应带错误说明")

    def test_invalid_trim_rejected_400(self):
        for bad in ("abc", "-1", "NaN", "inf"):
            r = self._fetch(f"&t={bad}")
            self.assertEqual(r.status_code, 400, f"t={bad!r} 应 400")

    def test_seek_beyond_duration_natural_empty_stream(self):
        """起点超源时长：固定为 ffmpeg 自然产出空流（200 + audio 头 + 极少字节）。
        设备侧收不到可解码帧即报播放失败；此处把该行为钉死。"""
        r = self._fetch("&ss=500")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("audio/"))
        self.assertLess(len(r.content), 1024, "超出源时长的产出应≈空流")

    def test_fmt_seconds_never_scientific_notation(self):
        """秒数格式化永不产出科学计数法：%g 在 ≥1e6 时给 "1e+06"，ffmpeg 解析不了。"""
        self.assertEqual(qqmusic_auth_server._fmt_seconds(60), "60")
        self.assertEqual(qqmusic_auth_server._fmt_seconds(42.3), "42.3")
        self.assertEqual(qqmusic_auth_server._fmt_seconds(1_000_000), "1000000")
        self.assertEqual(qqmusic_auth_server._fmt_seconds(12345678.9), "12345678.9")

    def test_seek_huge_value_beyond_duration_still_empty_stream(self):
        """极大起点（≥1e6）：命令里不得出现科学计数法，行为与超时长一致——200 空流。"""
        r = self._fetch("&ss=1000000")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("audio/"))
        self.assertLess(len(r.content), 1024)

    def test_missing_src_rejected(self):
        """缺 src 参数：FastAPI 缺参默认 422（既有行为，不改动）。"""
        r = asyncio.run(self._fetch_missing_src())
        self.assertIn(r.status_code, (400, 422))

    async def _fetch_missing_src(self):
        transport = httpx.ASGITransport(app=qqmusic_auth_server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            return await client.get("/stream")

    def test_ssrf_guard_still_rejects_private_src(self):
        """SSRF 防护不回归：回环/内网源仍被拒。"""
        async def run():
            transport = httpx.ASGITransport(app=qqmusic_auth_server.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
                return await client.get(f"/stream?src={self.src_url}")
        r = asyncio.run(run())
        self.assertEqual(r.status_code, 400)


class UpstreamFailureContract(unittest.TestCase):
    """上游打不开时 /stream 必须快速失败（502 + JSON），不能是 200 空流。

    实测 ffmpeg 退出码：404→8、拒连→195、防盗链读到 HTML→183；均在 <50ms 内
    退出。起点超源时长则是 exit 0（合法空流，见 TranscodeSeekContract）。
    """

    @classmethod
    def setUpClass(cls):
        if not have_ffmpeg():
            raise unittest.SkipTest("ffmpeg 不可用")

    def _fetch_src(self, src: str):
        """只放行 SSRF 守卫，让请求真正到达 ffmpeg。"""
        q = urllib.parse.quote(src, safe="")

        async def run():
            transport = httpx.ASGITransport(app=qqmusic_auth_server.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
                return await client.get(f"/stream?src={q}")

        with mock.patch.object(qqmusic_auth_server, "_src_host_is_safe", lambda u: True):
            return asyncio.run(run())

    def test_unreachable_upstream_returns_502(self):
        """连接被拒（端口无监听）：502 + JSON 错误，不是 200 空流。"""
        r = self._fetch_src("http://127.0.0.1:9/none.mp3")
        self.assertEqual(r.status_code, 502, r.content[:400])
        body = r.json()
        self.assertIn("error", body)
        self.assertIn("ffmpeg exited with code", body.get("detail", ""))

    def test_upstream_404_returns_502(self):
        """上游 404：502 + JSON 错误，且错误里说明是打不开而非空内容。"""
        srv = _TempStaticServer()
        try:
            url = f"http://127.0.0.1:{srv.port}/missing.mp3"
            r = self._fetch_src(url)
        finally:
            srv.close()
        self.assertEqual(r.status_code, 502, r.content[:400])
        body = r.json()
        self.assertIn("error", body)
        # 错误信息应带 ffmpeg 的 stderr 摘要（含 404 字样），不是笼统一句话。
        self.assertIn("404", json.dumps(body, ensure_ascii=False))

    def test_upstream_returns_html_not_audio_returns_502(self):
        """上游返回网页（防盗链 302 到 HTML 的等价场景）：502，不是 200 空流。"""
        srv = _TempStaticServer()
        try:
            url = f"http://127.0.0.1:{srv.port}/index.html"
            r = self._fetch_src(url)
        finally:
            srv.close()
        self.assertEqual(r.status_code, 502, r.content[:400])
        self.assertIn("error", r.json())


class UAStrategyContract(unittest.TestCase):
    """UA 策略：默认不发浏览器 UA；只在调用方传 referer 时才发。

    实测依据（curl/ffmpeg，2026-09-13）：
      - Calm Radio（http://streams.calmradio.com:1228/）：无 UA → 200 音频流；
        浏览器 UA → 302 index.html?sid=1 → ffmpeg 零产出。
      - B 站 CDN（bilivideo）：无 UA → 403；仅 UA（无 Referer）→ 403；
        UA + Referer → 200。
    """

    BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

    def _cmd(self, src="http://example.com/a.mp3", referer=None):
        return qqmusic_auth_server._build_ffmpeg_cmd(src, referer, None, None)

    def test_no_referer_sends_no_browser_ua(self):
        """无 referer：命令行里不得出现 -headers，也不得出现浏览器 UA。"""
        cmd = self._cmd(referer=None)
        joined = " ".join(cmd)
        self.assertNotIn("-headers", cmd, "无 referer 不应传 -headers（应用 ffmpeg 默认 UA）")
        self.assertNotIn("User-Agent", joined, "无 referer 不应发浏览器 UA（会打挂 Calm Radio）")
        self.assertNotIn("Mozilla/5.0", joined)

    def test_referer_sends_browser_ua_and_referer(self):
        """有 referer：同时发浏览器 UA 与 Referer（B 站 CDN 需要两者）。"""
        cmd = self._cmd(referer="https://www.bilibili.com")
        self.assertIn("-headers", cmd)
        joined = " ".join(cmd)
        self.assertIn("User-Agent", joined)
        self.assertIn(self.BROWSER_UA, joined)
        self.assertIn("Referer: https://www.bilibili.com", joined)

    def test_build_headers_is_none_without_referer(self):
        """_build_headers 无 referer 返回 None（调用方据此决定是否加 -headers）。"""
        self.assertIsNone(qqmusic_auth_server._build_headers(None))
        self.assertIsNone(qqmusic_auth_server._build_headers(""))
        h = qqmusic_auth_server._build_headers("https://example.com")
        self.assertIsNotNone(h)
        self.assertIn("User-Agent", h)
        self.assertIn("Referer: https://example.com", h)

    def test_ua_policy_end_to_end_over_real_http(self):
        """端到端：起本地假上游记录收到的请求头，验证代理真的按策略发/不发 UA。

        这是行为断言（上游看到什么），不是拼接字符串的自证。
        """
        srv = _HeaderEchoServer()
        try:
            # 无 referer：上游不应看到 User-Agent 里的 Mozilla。
            r = self._fetch(f"http://127.0.0.1:{srv.port}/tone.mp3")
            self.assertEqual(r.status_code, 200, r.content[:300])
            seen = srv.last_headers.get("user-agent", "")
            self.assertNotIn("Mozilla", seen,
                             f"无 referer 时不能发浏览器 UA，上游实收 {seen!r}")

            # 有 referer：上游应看到浏览器 UA 与 Referer。
            r = self._fetch(f"http://127.0.0.1:{srv.port}/tone.mp3",
                            referer="https://www.bilibili.com")
            self.assertEqual(r.status_code, 200, r.content[:300])
            self.assertIn("Mozilla", srv.last_headers.get("user-agent", ""))
            self.assertEqual(srv.last_headers.get("referer"), "https://www.bilibili.com")
        finally:
            srv.close()

    def _fetch(self, src: str, referer: str | None = None):
        q = urllib.parse.quote(src, safe="")
        if referer:
            q += "&referer=" + urllib.parse.quote(referer, safe="")

        async def run():
            transport = httpx.ASGITransport(app=qqmusic_auth_server.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
                return await client.get(f"/stream?src={q}")

        with mock.patch.object(qqmusic_auth_server, "_src_host_is_safe", lambda u: True):
            return asyncio.run(run())


class _TempStaticServer:
    """临时静态 HTTP 服务：用于制造 404 与「返回 HTML」的上游。"""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="music-proxy-upstream-")
        root = Path(self.tmp.name)
        (root / "index.html").write_text(
            "<!DOCTYPE html><html><body>anti-hotlink landing page</body></html>")

        outer = self

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=str(root), **kw)

            def log_message(self, *args):
                pass

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.srv = Server(("127.0.0.1", 0), Handler)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()


class _HeaderEchoServer:
    """假上游：记录最近一次请求头，并返回一段真实 mp3（供 UA 端到端断言）。"""

    def __init__(self):
        self.last_headers: dict[str, str] = {}
        self.tmp = tempfile.TemporaryDirectory(prefix="music-proxy-echo-")
        self.audio = Path(self.tmp.name) / "tone.mp3"
        subprocess.run(
            [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", "sine=frequency=440:duration=3", "-b:a", "32k", str(self.audio)],
            check=True,
        )
        data = self.audio.read_bytes()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802
                outer.last_headers = {k.lower(): v for k, v in self.headers.items()}
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.srv = Server(("127.0.0.1", 0), Handler)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
