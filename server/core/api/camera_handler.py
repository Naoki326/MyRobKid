"""摄像头监控页：网页实时查看设备摄像头画面（设备域的实时视图，§4.4）。

本模块**只交付摄像头页的正文**：样式片段、正文标记、页面脚本，以及两个 API。

页面骨架（侧栏/顶栏/壳脚本）与 `/xiaozhi/camera` → `/xiaozhi/camera/` 的
应用层 301 都在 `core/api/config_handler.py` 里 —— 壳是共享模板（§4.2 拍板
「一个 Python 模块生成侧栏 + 顶栏」），摄像头页必须用**同一份**，不许自带
第二副骨架（各页自带外壳被方案明确否掉：会漂移）。所以这里把原来自包含的
整页拆成三段可注入片段：

- ``CAMERA_CSS``：暗色局部覆盖（§4.2 允许摄像头页局部覆盖为暗色，
  壳 tokens 不动）——控制条、画面框、状态行、提示语；
- ``CAMERA_BODY``：正文（拍照按钮、自动刷新间隔、画面框、状态行）；
- ``CAMERA_JS``：定位设备 → 拍照 → 画到 canvas 的逻辑。

路由（挂载在 8003 aiohttp，nginx 8080 以 /xiaozhi/ 反代）：

- GET /xiaozhi/camera/                监控页 HTML（规范形，**保留原 URL**）
- GET /xiaozhi/camera                 301 → 规范形（应用层发，取消双注册）
- GET /xiaozhi/camera/api/snapshot    代理设备快照（JPEG）
- GET /xiaozhi/camera/api/device-info 代解析 mDNS → 设备 IP

工作方式：

- 设备端固件内置 HTTP 快照服务（端口 8080，mDNS 域名 xiaozhi-XXXX.local）
- 服务器代理设备快照，页面同源访问，避免浏览器 mixed-content / CORS 问题
- 每次快照都是设备实时拍摄（按需拍照，不占用带宽）
"""

from aiohttp import web, ClientTimeout

# 设备快照地址（mDNS 域名，IP 变化无需改配置）
DEVICE_HOST = "xiaozhi-8144.local"
DEVICE_SNAPSHOT_URL = f"http://{DEVICE_HOST}:8080/camera/snapshot"

#: 摄像头页的局部样式（暗色监控页与亮色壳并存，§4.2）。变量沿用壳 tokens，
#: 只覆盖这页自己的几个类 —— 不重定义骨架。
CAMERA_CSS = """
.camwrap{max-width:980px}
.camtoolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.camtoolbar label{font-size:12.5px;color:var(--dim);display:flex;align-items:center;gap:6px}
.camtoolbar select{width:auto}
.camframe{background:#05070d;border:1px solid var(--line);border-radius:14px;padding:10px;display:flex;justify-content:center;min-height:180px}
.camframe img{display:none}
.camframe canvas{max-width:100%;height:auto;border-radius:8px;display:block}
.camstatus{margin-top:10px;font-size:13px;color:var(--dim);min-height:20px}
.camstatus.err{color:var(--err)}
.camstatus.ok{color:var(--ok)}
.camnote{font-size:12px;color:var(--dim);margin-top:12px;line-height:1.6}
"""

#: 摄像头页正文：控制条 + 画面框 + 状态行（结构照原页，只是搬进壳）。
CAMERA_BODY = """
<section class="group camwrap" id="camera-live">
  <h2>📷 摄像头实时画面</h2>
  <div class="desc">画面由浏览器直达设备（服务器只负责定位设备 IP），按需拍照、不占带宽。
    本页保留独立 URL：可以把它当挂机监控的书签，长期开着。</div>
  <div class="camtoolbar">
    <button class="btn primary" type="button" onclick="camCapture()">📸 拍照</button>
    <label>自动刷新
      <select id="camInterval" onchange="camSetInterval()">
        <option value="0">关</option>
        <option value="1000">1 秒</option>
        <option value="5000" selected>5 秒</option>
        <option value="30000">30 秒</option>
      </select>
    </label>
  </div>
  <div class="camframe">
    <img id="cam" alt="等待拍摄..." onerror="camOnErr()" onload="camOnOk()">
    <canvas id="camView"></canvas>
  </div>
  <div class="camstatus" id="camStatus">正在定位设备…</div>
  <div class="camnote">照片在固件层已转正（顺时针 90°），右键保存即正图。
    设备离线时这里会显示「设备不可达」——检查设备电源与 Wi-Fi。</div>
</section>
"""

#: 摄像头页脚本（与正文一并注入壳）。
#:
#: 逐字搬自原自包含页面的逻辑（本票只做归位，不改行为）：先请服务器代解析
#: mDNS 拿设备 IP（浏览器解析不了 `.local`），再直接向设备抓快照。
CAMERA_JS = """
(() => {
  const MDS_HOST = '""" + DEVICE_HOST + """';
  const PORT = 8080;
  let deviceBase = null;   // 例如 http://192.168.18.122:8080
  let timer = null, fetching = false;
  const img = document.getElementById('cam');
  const status = document.getElementById('camStatus');
  const canvas = document.getElementById('camView');
  if (!img || !status || !canvas) return;   // 非摄像头页（防御性，不该发生）
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
        window.camCapture();
        return;
      }
    } catch (e) {}
    // 兑底：直接用 mDNS 域名（Safari 等支持）
    deviceBase = 'http://' + MDS_HOST + ':' + PORT;
    window.camCapture();
  }

  window.camCapture = function () {
    if (!deviceBase || fetching) return;
    fetching = true;
    status.textContent = '拍摄中…';
    status.className = 'camstatus';
    img.src = deviceBase + '/camera/snapshot?t=' + Date.now();
  };
  window.camOnOk = function () {
    fetching = false;
    drawPhoto();
    status.textContent = '✓ 已更新 ' + new Date().toLocaleTimeString()
      + '  (' + deviceBase.replace('http://', '') + ')';
    status.className = 'camstatus ok';
  };
  window.camOnErr = function () {
    fetching = false;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    canvas.width = canvas.height = 0;
    status.textContent = '✗ 设备不可达（检查设备电源 / Wi-Fi）';
    status.className = 'camstatus err';
  };
  window.camSetInterval = function () {
    if (timer) { clearInterval(timer); timer = null; }
    const v = parseInt(document.getElementById('camInterval').value);
    if (v > 0) { timer = setInterval(window.camCapture, v); window.camCapture(); }
  };
  locateDevice();
})();
"""


class CameraHandler:
    def __init__(self, config):
        self.config = config

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
