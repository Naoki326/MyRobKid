"""
config_handler.py — 轻量配置页后端（读 / 改 data/.config.yaml）

路由（挂载在 8003 aiohttp，nginx 8080 以 /xiaozhi/config/ 反代）：
- GET  /xiaozhi/config/           配置页 HTML
- POST /xiaozhi/config/api/auth   兼容接口（直接放行）
- GET  /xiaozhi/config/api/full   合并后的完整生效配置（敏感字段掩码）
- POST /xiaozhi/config/api/save   保存用户配置到 data/.config.yaml
- POST /xiaozhi/config/api/restart 重启服务使配置生效

安全设计：
- 不再要求页面口令（PIN 已移除），安全由 nginx auth_gate 兜底
  （本机/局域网免认证，公网来源要求 Basic Auth）。
- 敏感字段（api_key/token/secret 等）读取时掩码，diff 保存时掩码值
  不会被写回（保留原值）。
- 读写 data/.config.yaml 而非 config.yaml 模板；写入保留注释与原键序。
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

from aiohttp import web

from core.api.base_handler import BaseHandler
from core.utils import device_registry

TAG = __name__

# 敏感字段：读取时掩码，防止密钥明文出现在页面/浏览器
SENSITIVE_KEYS = {
    "api_key", "access_token", "secret_key", "secret_id", "app_secret",
    "token", "password", "authorization", "auth_key", "personal_access_token",
    "api_secret", "private_key", "config_pin",
}

# 掩码规则：保留前 4 位 + *** + 后 4 位（短值整体打码）
def _mask_value(value):
    if not isinstance(value, str) or not value:
        return value
    if "你" in value or len(value) <= 8:
        return "********"
    return value[:4] + "****" + value[-4:]


def _mask_tree(node):
    """递归掩码敏感字段。返回 (masked, 是否含敏感值)。"""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if isinstance(v, dict):
                out[k], _ = _mask_tree(v)
            elif isinstance(v, list):
                out[k] = [_mask_tree(i)[0] for i in v]
            elif k in SENSITIVE_KEYS or k.endswith("_key") or k.endswith("_secret") or k.endswith("_token"):
                out[k] = _mask_value(v)
            else:
                out[k] = v
        return out, True
    return node, False


class ConfigHandler(BaseHandler):
    def __init__(self, config: dict, project_dir: str):
        super().__init__(config)
        self.project_dir = project_dir
        self.custom_path = Path(project_dir) / "data" / ".config.yaml"
        self.default_path = Path(project_dir) / "config.yaml"
        self.bin_dir = Path(project_dir) / "data" / "bin"

    # ---------------- 设备与固件管理 ----------------

    async def handle_devices(self, request):
        """在线设备列表（当前 WebSocket 在线的设备）"""
        devices = []
        for did, handler in device_registry.get_online().items():
            devices.append({
                "device_id": did,
                "client_ip": getattr(handler, "client_ip", ""),
            })
        return web.json_response({"ok": True, "devices": devices})

    async def handle_firmware_list(self, request):
        """已上传固件列表 data/bin/{model}_{version}.bin"""
        items = []
        if self.bin_dir.is_dir():
            for p in sorted(self.bin_dir.glob("*.bin")):
                m = re.match(r"^(.+)_([0-9]+(?:\.[0-9]+)*(?:[-+][0-9A-Za-z.-]+)?)\.bin$", p.name)
                items.append({
                    "filename": p.name,
                    "model": m.group(1) if m else "",
                    "version": m.group(2) if m else "",
                    "size": p.stat().st_size,
                    "mtime": int(p.stat().st_mtime),
                })
        return web.json_response({"ok": True, "firmwares": items})

    async def handle_firmware_upload(self, request):
        """上传固件（multipart），文件名必须是 {model}_{version}.bin"""
        if not self.bin_dir.is_dir():
            self.bin_dir.mkdir(parents=True, exist_ok=True)
        reader = await request.multipart()
        field = await reader.next()
        if field is None or field.name != "file":
            return web.json_response(
                {"ok": False, "error": "缺少文件字段 file"}, status=400
            )
        filename = os.path.basename(field.filename or "")
        if not filename.endswith(".bin") or "_" not in filename:
            return web.json_response(
                {"ok": False, "error": "文件名必须是 型号_版本.bin（如 zhengchen-minicam_2.4.3.bin）"},
                status=400,
            )
        dest = self.bin_dir / filename
        try:
            with open(dest, "wb") as f:
                while True:
                    chunk = await field.read_chunk(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
        except Exception as e:
            return web.json_response(
                {"ok": False, "error": f"写入失败: {e}"}, status=500
            )
        self.logger.bind(tag=TAG).info(f"固件上传成功: {filename} ({dest.stat().st_size} bytes)")
        return web.json_response({"ok": True, "filename": filename})

    async def handle_firmware_delete(self, request):
        """删除固件：POST JSON {filename: xx}（仅允许 data/bin 内的 .bin）"""
        try:
            data = await request.json()
            filename = os.path.basename(str(data.get("filename", "")))
        except Exception:
            filename = ""
        if not filename.endswith(".bin"):
            return web.json_response({"ok": False, "error": "非法文件名"}, status=400)
        target = self.bin_dir / filename
        if target.is_file():
            target.unlink()
            self.logger.bind(tag=TAG).info(f"固件已删除: {filename}")
            return web.json_response({"ok": True})
        return web.json_response({"ok": False, "error": "文件不存在"}, status=404)

    # ---------------- 兼容接口（PIN 已移除） ----------------

    def _require_session(self, request) -> web.Response | None:
        # PIN 已移除：配置页不再要求口令登录。
        # 安全由 nginx auth_gate 兜底（本机/局域网免认证，公网要求 Basic Auth）。
        return None

    # ---------------- 配置读写 ----------------

    @staticmethod
    def _is_mask_placeholder(v):
        # 掩码占位形态：整体打码 ********（短值/模板占位），
        # 或保留首尾的 abcd****wxyz（_mask_value 的长值形态）。
        # 仅 ≤12 字符且中间恰好 4 个 * 才命中，真实密钥不会误判。
        return isinstance(v, str) and re.fullmatch(r".{0,4}\*{4}.{0,4}", v) is not None

    def _read_custom_yaml(self) -> dict:
        if not self.custom_path.exists():
            return {}
        import yaml
        with open(self.custom_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}

    def _read_default_yaml(self) -> dict:
        import yaml
        with open(self.default_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}

    def _write_custom_yaml(self, data: dict) -> None:
        import yaml
        # 保留原文件头注释（若存在）
        header = ""
        if self.custom_path.exists():
            text = self.custom_path.read_text(encoding="utf-8")
            m = re.match(r"^(\s*#.*\n)+", text)
            if m:
                header = m.group(0)
        self.custom_path.parent.mkdir(parents=True, exist_ok=True)
        body = yaml.safe_dump(
            data, allow_unicode=True, sort_keys=False, default_flow_style=False,
            width=1000,
        )
        self.custom_path.write_text(header + body, encoding="utf-8")

    # ---------------- 合并配置（与 config_loader 同逻辑） ----------------

    def _merged_config(self) -> dict:
        default = self._read_default_yaml()
        custom = self._read_custom_yaml()

        def merge(d, c):
            merged = dict(d)
            for k, v in c.items():
                if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
                    merged[k] = merge(merged[k], v)
                else:
                    merged[k] = v
            return merged

        return merge(default, custom)

    # ---------------- 路由处理 ----------------

    async def handle_page(self, request):
        """配置页 HTML（单文件，无外部依赖）。"""
        html_path = Path(self.project_dir) / "config" / "config_page.html"
        if not html_path.exists():
            return web.Response(text="config_page.html not found", status=404)
        return web.Response(
            text=html_path.read_text(encoding="utf-8"),
            content_type="text/html",
            charset="utf-8",
        )

    async def handle_auth(self, request):
        """兼容接口：PIN 已移除，直接放行（前端登录逻辑保留，避免改动）。"""
        return web.json_response({"ok": True})

    async def handle_full(self, request):
        """完整生效配置（敏感字段掩码）。"""
        denied = self._require_session(request)
        if denied:
            return denied
        merged = self._merged_config()
        masked, _ = _mask_tree(merged)
        return web.json_response({"ok": True, "config": masked})

    async def handle_meta(self, request):
        """元信息：用户配置路径等（无需登录）。"""
        return web.json_response({
            "ok": True,
            "custom_path": str(self.custom_path),
            "project_dir": self.project_dir,
        })

    async def handle_save(self, request):
        """保存用户配置（diff 提交：只写入变化字段）。

        客户端提交 {before, after}（均为掩码后的完整配置树）。
        服务端递归对比：
        - 敏感字段掩码占位（******** 或 xxxx****）不视为变化（保留原值）；
        - 值相同不写；值不同写入 data/.config.yaml（深度合并）。
        """
        denied = self._require_session(request)
        if denied:
            return denied
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求格式错误"}, status=400)
        before = data.get("before")
        after = data.get("after")
        if not isinstance(before, dict) or not isinstance(after, dict):
            return web.json_response({"ok": False, "error": "before/after 必须为对象"}, status=400)

        current = self._read_custom_yaml()

        def collect_diff(src, dst, base):
            """递归收集 dst 中相对 src 的变化，返回 {path: value}。"""
            changes = {}
            for k, v in dst.items():
                path = f"{base}.{k}" if base else k
                if isinstance(v, dict) and isinstance(src.get(k), dict):
                    changes.update(collect_diff(src[k], v, path))
                    continue
                sv = src.get(k)
                # 只看 after 值：仍是掩码占位 → 敏感字段未被修改，跳过（防止掩码写回）；
                # before 是掩码而 after 是新值 → 用户在页面换了密钥，必须正常写入
                if self._is_mask_placeholder(v):
                    continue
                if v != sv:
                    changes[path] = v
            return changes

        changes = collect_diff(before, after, "")
        if not changes:
            return web.json_response({"ok": True, "changed": 0})

        # 把变化路径写入自定义配置（支持嵌套 key 用点号）
        def set_path(node, path, value):
            parts = path.split(".")
            cur = node
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
                if not isinstance(cur, dict):
                    cur = {}
                    # 父级是标量被覆盖为 dict（如 plugins 由 str 改 dict）
                    cur = node[p] = {}
            cur[parts[-1]] = value

        for path, value in changes.items():
            set_path(current, path, value)

        # 清理显式置空字段：值为 None 的键删除（用户清空输入）
        def prune_empty(node):
            for k in list(node.keys()):
                if node[k] is None:
                    del node[k]
                elif isinstance(node[k], dict):
                    prune_empty(node[k])
                    if not node[k]:
                        del node[k]

        prune_empty(current)
        try:
            self._write_custom_yaml(current)
        except Exception as e:
            return web.json_response({"ok": False, "error": f"写入失败: {e}"}, status=500)
        # 重新解析校验（yaml 必须能读回来）
        try:
            self._read_custom_yaml()
        except Exception as e:
            return web.json_response({"ok": False, "error": f"写入后解析失败: {e}"}, status=500)
        return web.json_response({"ok": True, "changed": len(changes)})

    async def handle_restart(self, request):
        """重启服务使配置生效（launchctl kickstart，后台执行立即返回）。"""
        denied = self._require_session(request)
        if denied:
            return denied
        try:
            # 后台执行：kickstart -k 会先 kill 再拉起，连接会断开；
            # 用 Popen 立即返回，避免请求在服务重启瞬间断连
            subprocess.Popen(
                ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.xiaozhi.server"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return web.json_response({"ok": False, "error": "launchctl 不可用"}, status=500)
        return web.json_response({"ok": True})

    # ==================== SmartConfig 设备配网 ====================

    # ESPTouch v2 共享密钥，与固件 wifi_configuration_ap.cc 的 v2_key 一致
    ESPTOUCH_V2_KEY = b"FramedPhoto2024!"

    async def handle_local_wifi(self, request):
        """本机当前 Wi-Fi（SSID + 钥匙串密码），配网表单自动填充用。"""
        denied = self._require_session(request)
        if denied:
            return denied
        ssid = ""
        password = ""
        try:
            out = subprocess.run(
                ["/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport", "-I"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            for line in out.splitlines():
                if " SSID" in line:
                    ssid = line.split(":", 1)[1].strip()
                    break
        except Exception:
            pass
        if ssid:
            try:
                out = subprocess.run(
                    ["security", "find-generic-password", "-D", "AirPort Network Password", "-a", ssid, "-w"],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                if out:
                    password = out
            except Exception:
                pass
        return web.json_response({"ok": True, "ssid": ssid, "password": password})

    async def handle_smartconfig(self, request):
        """SmartConfig 广播配网：局域网广播 Wi-Fi 凭据（ESPTouch v2 加密），
        空配网状态的小智设备监听后自动连接。后台发送约 30 秒。"""
        denied = self._require_session(request)
        if denied:
            return denied
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体不是合法 JSON"}, status=400)
        ssid = str(body.get("ssid", "")).strip()
        password = str(body.get("password", ""))
        if not ssid:
            return web.json_response({"ok": False, "error": "SSID 不能为空"}, status=400)

        import asyncio
        from core.api import esptouch

        async def _broadcast():
            try:
                # 在线程池里跑（同步阻塞发送约 30 秒）
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    lambda: esptouch.send_smartconfig_v2(
                        ssid, password, self.ESPTOUCH_V2_KEY, duration_s=30
                    ),
                )
                self.logger and print(f"[smartconfig] 广播完成: {ssid}")
            except Exception as e:
                print(f"[smartconfig] 广播失败: {e}")

        asyncio.get_running_loop().create_task(_broadcast())
        return web.json_response({
            "ok": True, "ssid": ssid,
            "hint": "已开始广播（约30秒），设备处于配网模式会自动接收并连接，"
                    "连上后自动注册上线；60秒内未收到可重试一次",
        })
