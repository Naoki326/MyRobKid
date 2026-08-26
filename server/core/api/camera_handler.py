"""摄像头监控页：网页实时查看设备摄像头画面。

路由（挂载在 8003 aiohttp，nginx 8080 以 /xiaozhi/ 反代）：
- GET /xiaozhi/camera/                监控页 HTML
- GET /xiaozhi/camera/api/snapshot    代理设备快照（JPEG）

工作方式：
- 设备端固件内置 HTTP 快照服务（端口 8080，mDNS 域名 xiaozhi-XXXX.local）
- 服务器代理设备快照，页面同源访问，避免浏览器 mixed-content / CORS 问题
- 每次快照都是设备实时拍摄（按需拍照，不占用带宽）
"""
import time

from aiohttp import web, ClientTimeout

# 设备快照地址（mDNS 域名，IP 变化无需改配置）
DEVICE_HOST = "xiaozhi-8144.local"
DEVICE_SNAPSHOT_URL = f"http://{DEVICE_HOST}:8080/camera/snapshot"

PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>小智摄像头监控</title>
<style>
  body { font-family: -apple-system, sans-serif; background:#1a1a2e; color:#eee;
         display:flex; flex-direction:column; align-items:center; margin:0; padding:20px; }
  h1 { font-size: 18px; margin: 0 0 12px; }
  .toolbar { display:flex; gap:10px; align-items:center; margin-bottom:12px; flex-wrap:wrap; justify-content:center; }
  button { background:#4f8cff; color:#fff; border:none; border-radius:6px; padding:8px 18px;
           font-size:14px; cursor:pointer; }
  button:hover { background:#3a76e0; }
  select { background:#2a2a4a; color:#eee; border:1px solid #444; border-radius:6px; padding:6px; }
  .frame { background:#0d0d1a; border-radius:10px; padding:8px; max-width:96vw; display:flex; justify-content:center; }
  #cam { display:none; }
  canvas { max-width:100%; height:auto; border-radius:6px; }
  img { max-width:100%; height:auto; border-radius:6px; display:block; }
  .status { margin-top:10px; font-size:13px; color:#9a9ab8; min-height:18px; }
  .err { color:#ff6b6b; }
  .ok { color:#7bdc7b; }
  .note { font-size:12px; color:#77779a; margin-top:14px; max-width:600px; text-align:center; }
</style>
</head>
<body>
<h1>📷 小智摄像头监控</h1>
<div class="toolbar">
  <button onclick="capture()">拍照</button>
  <label>自动刷新:
    <select id="interval" onchange="setInterval2()">
      <option value="0">关</option>
      <option value="1000">1 秒</option>
      <option value="5000" selected>5 秒</option>
      <option value="30000">30 秒</option>
    </select>
  </label>
</div>
<div class="frame">
  <img id="cam" alt="等待拍摄..." onerror="onErr()" onload="onOk()">
  <canvas id="view"></canvas>
</div>
<div class="status" id="status">正在定位设备…</div>
<div class="note">画面由浏览器直接从设备获取，照片固件层已转正（顺时针 90°）</div>
<script>
const MDS_HOST = 'xiaozhi-8144.local';
const PORT = 8080;
let deviceBase = null;   // 例如 http://192.168.18.122:8080
let timer = null, fetching = false;
const img = document.getElementById('cam');
const status = document.getElementById('status');
const canvas = document.getElementById('view');
// 固件已顺时针转 90°，页面直接显示；清除历史手动旋转记录
localStorage.removeItem('camRot');

// 照片绘制到 canvas（右键保存即正图）
function drawPhoto() {
  if (!img.naturalWidth) return;
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0);
}

async function locateDevice() {
  status.textContent = '正在定位设备…';
  // 服务器代解析 mDNS → 拿设备 IP（绕开浏览器 .local 解析问题）
  try {
    const r = await fetch('/xiaozhi/camera/api/device-info');
    const info = await r.json();
    if (info.ip) {
      deviceBase = 'http://' + info.ip + ':' + (info.port || PORT);
      capture();
      return;
    }
  } catch (e) {}
  // 兑底：直接用 mDNS 域名（Safari 等支持）
  deviceBase = 'http://' + MDS_HOST + ':' + PORT;
  capture();
}

function capture() {
  if (!deviceBase || fetching) return;
  fetching = true;
  status.textContent = '拍摄中…';
  status.className = 'status';
  img.src = deviceBase + '/camera/snapshot?t=' + Date.now();
}
function onOk() {
  fetching = false;
  drawPhoto();
  status.textContent = '✓ 已更新 ' + new Date().toLocaleTimeString() + '  (' + deviceBase.replace('http://','') + ')';
  status.className = 'status ok';
}
function onErr() {
  fetching = false;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  canvas.width = canvas.height = 0;
  status.textContent = '✗ 设备不可达（检查设备电源 / Wi-Fi）';
  status.className = 'status err';
}
function setInterval2() {
  if (timer) { clearInterval(timer); timer = null; }
  const v = parseInt(document.getElementById('interval').value);
  if (v > 0) { timer = setInterval(capture, v); capture(); }
}
locateDevice();
</script>
</body>
</html>"""


class CameraHandler:
    def __init__(self, config):
        self.config = config

    async def handle_page(self, request):
        return web.Response(text=PAGE, content_type="text/html")

    async def handle_device_info(self, request):
        """服务器代解析设备 mDNS 域名，返回 IP。

        浏览器（尤其 Chrome 内置 DNS）可能解析不了 .local 域名，
        但系统级 getaddrinfo 走 mDNSResponder 可以。
        """
        import socket
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            # getaddrinfo 在线程池里跑（.local 查询可能耗时几百 ms）
            infos = await loop.run_in_executor(
                None,
                lambda: socket.getaddrinfo(DEVICE_HOST, None, socket.AF_INET),
            )
            ip = infos[0][4][0] if infos else None
            return web.json_response({"host": DEVICE_HOST, "ip": ip, "port": 8080})
        except Exception as e:
            return web.json_response(
                {"host": DEVICE_HOST, "ip": None, "port": 8080, "error": str(e)}
            )

    async def handle_snapshot(self, request):
        """代理设备快照。设备离线/超时返回 502。"""
        import aiohttp

        try:
            timeout = ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(DEVICE_SNAPSHOT_URL) as resp:
                    if resp.status != 200:
                        return web.Response(
                            status=502, text=f"device returned {resp.status}"
                        )
                    data = await resp.read()
                    return web.Response(
                        body=data,
                        content_type="image/jpeg",
                        headers={"Cache-Control": "no-store"},
                    )
        except Exception as e:
            # 详细记录：连接器错误需看底层 os_error / 异常链
            reason = getattr(e, 'os_error', None) or e
            import logging
            logging.getLogger(__name__).error(
                "camera proxy failed: %s: %s (os_error=%s)",
                type(e).__name__, e, reason,
            )
            return web.Response(
                status=502,
                text=f"device unreachable: {type(e).__name__}",
            )
