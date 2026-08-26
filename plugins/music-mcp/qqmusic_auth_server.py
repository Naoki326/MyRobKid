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


@app.get("/stream")
async def transcode_stream(
    src: str = Query(...),
    referer: str = Query(None),
    request: Request = None,
):
    if not _src_host_is_safe(src):
        return JSONResponse({"error": "src 必须是公网 http(s) 地址"}, status_code=400)

    cmd = [
        "/opt/homebrew/bin/ffmpeg", "-loglevel", "error",
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
    ]
    headers = f"User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)\r\n"
    if referer:
        # B 站等 CDN 有 Referer 防盗链
        headers += f"Referer: {referer}\r\n"
    cmd += ["-headers", headers, "-i", src,
            "-vn", "-map", "0:a:0", "-ac", "1", "-ar", "44100", "-b:a", "128k",
            "-f", "mp3", "pipe:1"]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    async def gen():
        try:
            while True:
                chunk = await proc.stdout.read(16 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            if proc.returncode is None:
                proc.kill()

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
