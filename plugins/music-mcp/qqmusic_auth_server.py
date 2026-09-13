#!/usr/bin/env python3
"""
QQ 音乐扫码授权服务（供小智音乐 MCP / qqmusic_mcp 共享凭证）

- 页面：http://<host>:8080/apps/qqmusic/（nginx 反代到本服务 127.0.0.1:8777）
- 凭证写入 ~/.hermes/qqmusic_cred.json（与 qqmusic_mcp.py、music_mcp.py 共享）
- 只监听 127.0.0.1，公网访问经 nginx Basic Auth 保护

接口（页面内全部使用相对路径，天然兼容代理前缀）：
  GET  /                 授权页面
  GET  /api/cred         当前凭证状态
  POST /api/login/start  {login_type: mobile|qq|wx} 生成二维码
  GET  /api/login/status 轮询扫码状态（成功时落盘凭证）
"""

import asyncio
import base64
import io
import ipaddress
import json
import math
import os
import socket
import subprocess
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from qqmusic_api import Client, Credential
from qqmusic_api.models.login import QR, QRCodeLoginEvents, QRLoginType

CRED_PATH = Path("~/.hermes/qqmusic_cred.json").expanduser()

# 每 12 小时检查一次；musickey 剩余不到 1 天就提前续
_REFRESH_INTERVAL_S = 12 * 3600


async def _refresh_credential_if_needed(force: bool = False) -> str:
    """musickey 仅 3 天有效（腾讯风控）。到期前用 refresh_token 静默续期。
    返回 'ok' / 'skipped' / 'failed:<reason>'。"""
    cred = _load_credential()
    if cred is None:
        return "failed:no-credential"
    remaining_s = (
        float(getattr(cred, "musickey_create_time", 0) or 0)
        + float(getattr(cred, "key_expires_in", 0) or 0)
        - datetime.now().timestamp()
    )
    if not force and remaining_s > 24 * 3600:
        return "skipped"
    try:
        async with Client(credential=cred) as client:
            new_cred = await client.login.refresh_credential(cred)
        if new_cred.is_expired():
            return "failed:still-expired"
        CRED_PATH.write_text(
            json.dumps(new_cred.model_dump(mode="json"), ensure_ascii=False)
        )
        return "ok"
    except Exception as e:
        return f"failed:{type(e).__name__}:{e}"


async def _cred_refresher_loop():
    while True:
        await asyncio.sleep(_REFRESH_INTERVAL_S)
        result = await _refresh_credential_if_needed()
        if result.startswith("failed"):
            print(f"[cred-refresher] {result}（refresh 链失效，需要重新扫码）", flush=True)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # 启动时先续一次（服务重启即可自愈）
    print(f"[cred-refresher] startup: {await _refresh_credential_if_needed()}", flush=True)
    task = asyncio.create_task(_cred_refresher_loop())
    yield
    task.cancel()


app = FastAPI(title="QQ 音乐授权 · 小智音乐", lifespan=_lifespan)


# ── ffmpeg 转码流代理：把设备不支持的格式（m4a/m4s/aac/flac…）
#    实时转成 mp3 流。B 站音频、喜马拉雅系播客都靠它。 ────────────────

def _src_host_is_safe(src_url: str) -> bool:
    """拒绝指向本机/内网的源（防 SSRF 打内网服务），只允许公网 http(s)。"""
    try:
        parsed = urllib.parse.urlparse(src_url)
        if parsed.scheme not in ("http", "https"):
            return False
        addr = ipaddress.ip_address(socket.gethostbyname(parsed.hostname))
        return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved)
    except Exception:
        return False


def _parse_seconds(value: str | None, name: str) -> float | None:
    """把秒数参数解析为非负有限浮点；非法值抛 ValueError（端点转 400）。

    起点支持小数（如 59.5）。None 表示未提供。
    """
    if value is None or value == "":
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是秒数（支持小数），收到 {value!r}")
    if not math.isfinite(seconds):
        raise ValueError(f"{name} 必须是有限数，收到 {value!r}")
    if seconds < 0:
        raise ValueError(f"{name} 不能为负数，收到 {value!r}")
    return seconds


def _fmt_seconds(seconds: float) -> str:
    # %g 在 ≥1e6 时产出科学计数法（"1e+06"），ffmpeg 解析不了；
    # 定点六位再去尾零，任何有限值都不会出现科学计数法。
    return f"{seconds:.6f}".rstrip("0").rstrip(".")


def _build_headers(referer: str | None) -> str | None:
    """构造 ffmpeg -headers 值；返回 None 表示不传 -headers（用 ffmpeg 默认 UA）。

      UA 策略（不要退回「硬编码浏览器 UA」，那会打挂 Calm Radio）：
      - 默认**不发** User-Agent，让 ffmpeg 用自己的默认 UA。实测 Calm Radio
        （http://streams.calmradio.com:1228/）对浏览器 UA 做防盗链：
        无 UA → 200 音频流；带浏览器 UA → 302 到 index.html?sid=1 → ffmpeg
        读 HTML 报 400/Invalid data，零产出。
      - 仅在调用方显式传了 referer 时才附浏览器 UA：B 站 CDN（bilivideo）
        只认「浏览器 UA + Referer」组合——实测无 UA 与仅有 UA（无 Referer）
        都是 403，UA+Referer 才是 200。这类源本就要求伪装，附上 UA 是对的。
      - 调用方要为自己的源负责：带 referer 的源必须真的需要 UA 伪装。
    """
    if not referer:
        return None
    return (
        "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)\r\n"
        f"Referer: {referer}\r\n"
    )


def _build_ffmpeg_cmd(src: str, referer: str | None,
                      start_s: float | None, trim_s: float | None) -> list[str]:
    """拼装 ffmpeg 转码命令（纯函数，方便测试断言 UA 策略）。"""
    cmd = [
        "/opt/homebrew/bin/ffmpeg", "-loglevel", "error",
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
    ]
    # 起点定位必须用输入定位（-ss 置于 -i 之前）：实测同一首歌全量转码 2.08s、
    # 从 60s 定位 1.09s，产出时长与「总时长 − 60」偏差 0.0s；输出定位会先解码
    # 丢弃，既慢又没有这个精度。起点超出源时长时交给 ffmpeg 自然产出空流。
    # （契约测试：plugins/music-mcp/tests/test_transcode_proxy.py）
    if start_s is not None:
        cmd += ["-ss", _fmt_seconds(start_s)]
    headers = _build_headers(referer)
    if headers is not None:
        cmd += ["-headers", headers]
    cmd += ["-i", src,
            # 设备（zhengchen-minicam）codec 输出 24kHz：直出 24k 单声道，
            # 免掉设备端 44.1k→24k 软件重采样（CPU 大头），解码量也减半；
            # 24k 单声道 64kbps 已接近透明，还省一半网络吞吐。
            "-vn", "-map", "0:a:0", "-ac", "1", "-ar", "24000", "-b:a", "64k"]
    if trim_s is not None:
        # 裁剪时长（输出侧 -t）：自动化验收能在数秒内完成，不必下载整首剩余部分。
        cmd += ["-t", _fmt_seconds(trim_s)]
    cmd += ["-f", "mp3", "pipe:1"]
    return cmd


# 首字节等待窗口：ffmpeg 正常源约 15ms 出首字节，各类硬失败（404/拒连/读到 HTML）
# 约 30-50ms 即以非零码退出；取 4s 给慢上游留足余量，仍远早于设备侧
# kStartWaitTimeoutMs = 10000 的 10s 超时，代理能抢在设备放弃前给出结论。
_FIRST_BYTE_TIMEOUT_S = 4.0


def _ffmpeg_failure_reason(proc) -> str | None:
    """判定 ffmpeg 是否「失败」。返回 None 表示成功或合法空流（保持 200）。

      判据：**进程退出码**。
      - 上游连不上/404/读到 HTML：ffmpeg 以非零码退出（实测 404→8、
        302 防盗链→183、连接拒绝→195），stdout 零字节 → 判失败 → 502。
      - 起点超出源时长：源有效、只是那段没内容，ffmpeg **退出码 0** 并
        正常写出 mp3 头（实测 237 字节）→ 保持 200 空流（既有契约）。
      - ffmpeg 仍在运行（慢上游）：不算失败，交给流继续。

      这里刻意不以「零字节」判失败——合法空流也是零/极少字节；退出码才是
      区分「源有效但无内容」与「源根本打不开」的可靠信号。stderr 已用于诊断
      日志（见调用点），退出码用于判定。
    """
    if proc.returncode is None or proc.returncode == 0:
        return None
    return f"ffmpeg exited with code {proc.returncode}"


async def _read_stderr(proc) -> str:
    """尽力读取 ffmpeg stderr 作为诊断摘要；失败不抛出。

    仅在进程已退出或被 kill 后调用（否则空 pipe 上等待会挂住）。
    """
    try:
        if proc.stderr is None:
            return ""
        data = await asyncio.wait_for(proc.stderr.read(), timeout=1.0)
        return (data or b"").decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


@app.get("/stream")
async def transcode_stream(
    src: str = Query(...),
    referer: str = Query(None),
    ss: str = Query(None),
    t: str = Query(None),
    request: Request = None,
):
    if not _src_host_is_safe(src):
        return JSONResponse({"error": "src 必须是公网 http(s) 地址"}, status_code=400)
    try:
        start_s = _parse_seconds(ss, "ss")
        trim_s = _parse_seconds(t, "t")
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    cmd = _build_ffmpeg_cmd(src, referer, start_s, trim_s)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        # stderr 用 PIPE 抓取：失败时要把它作为诊断信息回报（不再是静默空流）。
        stderr=asyncio.subprocess.PIPE,
    )

    async def _kill():
        if proc.returncode is None:
            proc.kill()

    # 先等首字节（带超时），据此区分「能出声」与「根本打不开」：
    # 设备收到 200 零数据只会干等 10s 超时且分不清音源坏还是系统坏，
    # 代理必须先给出明确结论。
    first_chunk = b""
    try:
        first_chunk = await asyncio.wait_for(
            proc.stdout.read(16 * 1024), timeout=_FIRST_BYTE_TIMEOUT_S)
    except asyncio.TimeoutError:
        await _kill()
        stderr = await _read_stderr(proc)
        return JSONResponse(
            {"error": "上游在首字节窗口内无音频产出",
             "detail": stderr or f"no data within {_FIRST_BYTE_TIMEOUT_S:g}s"},
            status_code=502,
        )

    if not first_chunk:
        # 已到达 EOF：等退出码落定再判定（响应头尚未发出，来得及改 502）。
        await proc.wait()
        reason = _ffmpeg_failure_reason(proc)
        if reason is not None:
            stderr = await _read_stderr(proc)
            print(f"[stream] 502 {reason}: {stderr[:500]}", flush=True)
            return JSONResponse(
                {"error": "上游音频流无法打开", "detail": reason,
                 "ffmpeg_stderr": stderr[-1000:]},
                status_code=502,
            )
        # 合法空流（如起点超源时长）：保持既有 200 + audio 契约。

    async def gen():
        try:
            if first_chunk:
                yield first_chunk
            while True:
                chunk = await proc.stdout.read(16 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            await _kill()
            await _read_stderr(proc)  # 排空 stderr 管道，避免 ffmpeg 阻塞在写 stderr

    return StreamingResponse(gen(), media_type="audio/mpeg")


# ── 二维码会话（单用户，进程内存） ─────────────────────────────────
_lock = asyncio.Lock()
_qr: QR | None = None
_qr_type: QRLoginType = QRLoginType.MOBILE
_last_result: dict = {"state": "none"}  # none|waiting|scanned|success|expired|refused|error
_watch_task: asyncio.Task | None = None

LOGIN_TYPES = {
    "mobile": (QRLoginType.MOBILE, "手机 QQ 音乐 App 扫码（推荐）"),
    "qq": (QRLoginType.QQ, "手机 QQ 扫码"),
    "wx": (QRLoginType.WX, "微信扫码"),
}


def _load_credential() -> Credential | None:
    try:
        return Credential(**json.loads(CRED_PATH.read_text()))
    except Exception:
        return None


def _qr_png_b64(qr: QR) -> str | None:
    """QR.data 为 PNG 字节则直接用；否则（文本内容）用 qrcode 库渲染。"""
    if "png" in (qr.mimetype or "").lower():
        return base64.b64encode(qr.data).decode()
    try:
        import qrcode

        img = qrcode.make(qr.data.decode("utf-8", errors="replace"))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


@app.get("/api/cred")
async def cred_state():
    cred = _load_credential()
    if cred is None:
        return {"logged_in": False}
    expired = True
    try:
        expired = cred.is_expired()
    except Exception:
        pass
    return {
        "logged_in": not expired,
        "expired": expired,
        "uin": getattr(cred, "encrypt_uin", "") or str(getattr(cred, "musicid", "")),
        "login_type": getattr(cred, "login_type", ""),
        "key_expires_in": getattr(cred, "key_expires_in", 0),
    }


@app.post("/api/cred/refresh")
async def cred_refresh():
    return {"result": await _refresh_credential_if_needed(force=True)}


def _apply_login_result(result) -> bool:
    """把 QRLoginResult 映射到页面状态。返回 True 表示已终结。"""
    global _last_result
    if result.event == QRCodeLoginEvents.DONE and result.credential:
        CRED_PATH.write_text(
            json.dumps(result.credential.model_dump(mode="json"), ensure_ascii=False)
        )
        _last_result = {
            "state": "success",
            "uin": getattr(result.credential, "encrypt_uin", "")
            or str(getattr(result.credential, "musicid", "")),
            "time": datetime.now().strftime("%H:%M:%S"),
        }
        return True
    state_map = {
        QRCodeLoginEvents.SCAN: "scanned",
        QRCodeLoginEvents.CONF: "scanned",
        QRCodeLoginEvents.TIMEOUT: "expired",
        QRCodeLoginEvents.REFUSE: "refused",
    }
    _last_result = {"state": state_map.get(result.event, "waiting")}
    return result.event in (
        QRCodeLoginEvents.TIMEOUT,
        QRCodeLoginEvents.REFUSE,
    )


async def _watch_mobile_qr(qr: QR):
    """MOBILE 类型二维码走 MQTT 流接口，后台持续消费状态事件。"""
    try:
        client = Client()
        async for result in client.login.checking_mobile_qrcode(qr):
            if _apply_login_result(result):
                return
    except Exception as e:
        global _last_result
        _last_result = {"state": "error", "detail": f"{type(e).__name__}: {e}"}


@app.post("/api/login/start")
async def login_start(body: dict):
    global _qr, _qr_type, _last_result, _watch_task
    login_type = body.get("login_type", "mobile")
    if login_type not in LOGIN_TYPES:
        return JSONResponse({"error": "login_type 必须是 mobile/qq/wx"}, status_code=400)

    if _watch_task is not None and not _watch_task.done():
        _watch_task.cancel()
        _watch_task = None

    async with _lock:
        client = Client()
        qr = await client.login.get_qrcode(LOGIN_TYPES[login_type][0])
        _qr = qr
        _qr_type = LOGIN_TYPES[login_type][0]
        _last_result = {"state": "waiting"}

    png = _qr_png_b64(qr)
    if png is None:
        return JSONResponse({"error": "二维码渲染失败"}, status_code=500)

    if login_type == "mobile":
        _watch_task = asyncio.create_task(_watch_mobile_qr(qr))

    return {"qr_png_base64": png, "login_type": login_type}


@app.get("/api/login/status")
async def login_status():
    global _last_result
    # 已成功则直接返回缓存，避免重复写凭证
    if _last_result.get("state") == "success":
        return _last_result
    if _qr is None:
        return {"state": "none"}

    # MOBILE 类型由后台 MQTT 流刷新状态，直接读内存
    if _qr_type == QRLoginType.MOBILE:
        return _last_result

    async with _lock:
        qr = _qr
    try:
        client = Client()
        result = await client.login.check_qrcode(qr)
    except Exception as e:
        return {"state": "error", "detail": f"{type(e).__name__}: {e}"}

    _apply_login_result(result)
    return _last_result


# ── 页面（相对路径，兼容 /apps/qqmusic/ 前缀） ─────────────────────

_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QQ 音乐授权 · 小智音乐</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "PingFang SC", sans-serif; max-width: 420px;
         margin: 32px auto; padding: 0 16px; }
  h1 { font-size: 20px; }
  .card { border: 1px solid rgba(128,128,128,.3); border-radius: 12px;
          padding: 20px; margin-top: 16px; text-align: center; }
  .ok   { border-color: #2e9e5b; background: rgba(46,158,91,.08); }
  .bad  { border-color: #c0392b; background: rgba(192,57,43,.08); }
  button { font-size: 15px; padding: 10px 18px; margin: 4px; border-radius: 8px;
           border: 1px solid rgba(128,128,128,.4); background: rgba(128,128,128,.08);
           color: inherit; cursor: pointer; }
  button.active { border-color: #3b82f6; background: rgba(59,130,246,.15); }
  button.primary { border-color: #3b82f6; background: #3b82f6; color: #fff; }
  img#qr { width: 240px; height: 240px; image-rendering: pixelated; }
  .muted { color: rgba(128,128,128,.9); font-size: 13px; }
  ol { text-align: left; display: inline-block; margin: 8px 0; padding-left: 20px; }
</style>
</head>
<body>
<h1>🎵 QQ 音乐授权 <span class="muted">· 小智音乐</span></h1>

<div id="credCard" class="card">正在读取当前凭证…</div>

<div class="card">
  <div style="margin-bottom:10px">选择扫码方式</div>
  <div>
    <button data-t="mobile" class="active">手机 App（推荐）</button>
    <button data-t="qq">手机 QQ</button>
    <button data-t="wx">微信</button>
  </div>
  <div style="margin-top:12px"><button id="startBtn" class="primary">生成二维码</button></div>
</div>

<div id="qrCard" class="card" style="display:none">
  <img id="qr" alt="二维码">
  <div id="status" class="muted" style="margin-top:8px">等待扫码…</div>
  <ol class="muted">
    <li>打开手机 <b>QQ 音乐 App</b></li>
    <li>我的 → 右上角 ☰ → 扫一扫</li>
    <li>扫码并在手机上确认授权</li>
  </ol>
</div>

<script>
const $ = id => document.getElementById(id);
let loginType = 'mobile', timer = null;

document.querySelectorAll('[data-t]').forEach(b => b.onclick = () => {
  document.querySelectorAll('[data-t]').forEach(x => x.classList.remove('active'));
  b.classList.add('active'); loginType = b.dataset.t;
});

async function refreshCred() {
  try {
    const s = await (await fetch('api/cred')).json();
    $('credCard').className = 'card ' + (s.logged_in ? 'ok' : 'bad');
    $('credCard').innerHTML = s.logged_in
      ? `✅ 已登录（uin: ${s.uin || '?'}）<div class="muted">凭证有效，小智可直接点歌</div>`
      : (s.expired
          ? `⚠️ 凭证已过期<div class="muted">重新扫码授权即可恢复点歌</div>`
          : `❌ 未登录<div class="muted">扫码授权后小智才能点歌</div>`);
  } catch (e) {
    $('credCard').className = 'card bad';
    $('credCard').textContent = '凭证状态读取失败：' + e;
  }
}

$('startBtn').onclick = async () => {
  clearInterval(timer);
  $('status').textContent = '正在获取二维码…';
  const r = await fetch('api/login/start', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({login_type: loginType})
  });
  const d = await r.json();
  if (!r.ok) { $('status').textContent = d.error || '生成失败'; return; }
  $('qr').src = 'data:image/png;base64,' + d.qr_png_base64;
  $('qrCard').style.display = '';
  $('status').textContent = '等待扫码…';
  timer = setInterval(poll, 2000);
};

async function poll() {
  try {
    const s = await (await fetch('api/login/status')).json();
    const map = {
      waiting: '等待扫码…', scanned: '已扫码 ✅ 请在手机上确认授权',
      expired: '二维码已过期，请重新生成', refused: '已拒绝授权，请重新生成',
      error: '查询出错：' + (s.detail || '')
    };
    if (s.state === 'success') {
      clearInterval(timer);
      $('status').innerHTML = `<b>授权成功 ✅</b>（${s.time}）`;
      refreshCred();
    } else { $('status').textContent = map[s.state] || s.state; }
  } catch (e) { /* 网络抖动忽略 */ }
}

refreshCred();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return _PAGE
