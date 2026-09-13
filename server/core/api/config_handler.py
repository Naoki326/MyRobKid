"""
config_handler.py — 轻量配置页后端（读 / 改 data/.config.yaml）

路由（挂载在 8003 aiohttp，nginx 8080 以 /xiaozhi/config/ 反代）：

页面（父 spec §8.1 的 slug 是**用户契约**，ADR-0012）：
- GET  /xiaozhi/config/           旧八组配置页（本票 expand 阶段原样保留）
- GET  /xiaozhi/config/<slug>/   域页（dialogue / system 有内容，其余为占位页）
- GET  /xiaozhi/config/raw/      逃生口：整份原始配置，只读一页看完
- 无尾斜杠形态 301 到规范形（**应用层发**，不依赖仓库外的 nginx 配置）

接口：
- POST /xiaozhi/config/api/auth   兼容接口（直接放行）
- GET  /xiaozhi/config/api/full   合并后的完整生效配置（敏感字段掩码）+ 存在信号
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
from dataclasses import asdict
from pathlib import Path

from aiohttp import web

from core.api.base_handler import BaseHandler
from core.utils import device_registry

# 页面外壳与字段归属表（父 spec §4.2 的「服务端共享模板」与 §7 的机械复算）。
# 放在 server/config/ 而不是 core/api/：它们是页面渲染资产，与 config_page.html
# 同居，改页面不用动 handler。
from config import config_shell as shell
from config import page_domains

TAG = __name__

#: 逃生口页的脚本：把整棵掩码配置树填进只读容器。
#: 内联在 handler 里而不是另开一个静态文件：逃生口页的脚本只有这几行，
#: 而它的**唯一要求**是「与编辑页读同一份数据」（``api/full`` 的掩码树，
#: §4.3 原文「整份 YAML 一页看完」）。多一个文件就多一个可能漂移的读取路径。
RAW_PAGE_JS = """
(function () {
  var box = document.getElementById('rawView');
  if (!box) return;
  fetch('/xiaozhi/config/api/full')
    .then(function (r) { return r.json(); })
    .then(function (d) {
      box.textContent = JSON.stringify(d.config || {}, null, 2);
    })
    .catch(function (e) {
      box.textContent = '加载失败：' + e.message;
    });
})();
"""

# 敏感字段：读取时掩码，防止密钥明文出现在页面/浏览器
SENSITIVE_KEYS = {
    "api_key", "access_token", "secret_key", "secret_id", "app_secret",
    "token", "password", "authorization", "auth_key", "personal_access_token",
    "api_secret", "private_key", "config_pin",
}


def _is_sensitive_key(key) -> bool:
    """键名是否敏感（掩码与存在信号共用同一把尺）。

    大小写不敏感：``headers.Authorization`` 与 ``authorization`` 是同一个东西，
    漏掉大写形态会让该键在页面上从密码框降级为普通文本框。
    """
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    return (
        lowered in SENSITIVE_KEYS
        or lowered.endswith("_key")
        or lowered.endswith("_secret")
        or lowered.endswith("_token")
    )


def _is_placeholder_secret(value) -> bool:
    """模板占位符（``你的xxx``）：形如密钥，但不是真值。

    这是「全库密钥误判已配置」那个 bug 的根因所在：占位符与真实密钥在掩码后
    长得一样（``********``），所以「有掩码 = 已配置」这条推理必然把占位符判成
    已配置。存在信号必须绕开掩码形态、直接看原值。
    """
    return isinstance(value, str) and "你" in value


def _is_configured_secret(value) -> bool:
    """这个敏感键在配置里**真的有值**吗（未配置 / 占位符都算没有）。"""
    if value is None:
        return False
    if not isinstance(value, str):
        return True
    if value == "":
        return False
    return not _is_placeholder_secret(value)


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
            elif _is_sensitive_key(k):
                out[k] = _mask_value(v)
            else:
                out[k] = v
        return out, True
    return node, False


def _secret_state(node, prefix="", out=None):
    """遍历配置树，收集**敏感键的存在信号**（路径 → {"configured": bool}）。

    与 ``_mask_tree`` 输出分离，是本票（父 spec §5.5 / §9 规则 3）的核心：
    「配置里是否存在该键」与「掩码后的显示值」是两件事，页面判定只许用前者。

    ``configured`` 的判据是**原值**（非空且非模板占位符），不是掩码形态 ——
    这正是「密钥误判已配置」与「注入值伪装已配置」两个 bug 的修法。
    """
    if out is None:
        out = {}
    if isinstance(node, dict):
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                _secret_state(v, path, out)
            elif isinstance(v, list):
                # 与 _mask_tree 保持同一遍历口径，且路径用**页面 getPath 认识的点号语义**
                # （``context_providers.0.headers.Authorization``，不是 ``[0]``）：
                # 信号必须能被消费方真的查到，否则等于没给。
                for i, item in enumerate(v):
                    _secret_state(item, f"{path}.{i}", out)
            elif _is_sensitive_key(k):
                out[path] = {"configured": _is_configured_secret(v)}
    return out


def _strip_template_comment(html: str) -> str:
    """去掉骨架开头的作者注释。

    那条注释写的是「这一页怎么搭起来的」（给改代码的人看），不该随响应发给
    浏览器——它会让「页面里有没有某个字符串」这类检测（包括我们自己的契约
    测试）把注释当成内容，得出假阳/假阴。

    只删 ``<!DOCTYPE html>`` 与 ``<html>`` 之间那一段：那是骨架里唯一的注释区。
    """
    doctype = html.find("<!DOCTYPE html>")
    if doctype < 0:
        return html
    html_tag = html.find("<html", doctype)
    if html_tag < 0:
        return html
    head = html[doctype:html_tag]
    if "<!--" not in head:
        return html
    start = head.find("<!--")
    end = head.find("-->", start)
    if end < 0:
        return html
    cleaned = head[:start] + head[end + 3:].lstrip("\n") + "\n"
    return html[:doctype] + cleaned + html[html_tag:]


class ConfigHandler(BaseHandler):
    def __init__(self, config: dict, project_dir: str):
        super().__init__(config)
        self.project_dir = project_dir
        self.custom_path = Path(project_dir) / "data" / ".config.yaml"
        self.default_path = Path(project_dir) / "config.yaml"
        self.bin_dir = Path(project_dir) / "data" / "bin"

    # ---------------- 设备与固件管理 ----------------

    async def handle_devices(self, request):
        """在线设备列表（当前 WebSocket 在线的设备）

        ``model`` / ``version`` 是设备**自报**的型号与固件版本。它们不来自
        WebSocket 握手头（固件只发 Protocol-Version / Device-Id / Client-Id /
        Authorization），而是设备开机自检打 OTA 接口时上报的（见 ota_handler
        的 ``board.type`` / ``application.version``），后由 device_registry 记下。
        取不到就是空串（前端 ``hasNewerFirmware`` 会保守处理）。
        """
        devices = []
        for did, handler in device_registry.get_online().items():
            info = device_registry.get_device_info(did)
            devices.append({
                "device_id": did,
                "client_ip": getattr(handler, "client_ip", ""),
                "model": getattr(handler, "device_model", "") or info["model"],
                "version": getattr(handler, "device_version", "") or info["version"],
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
        """旧八组配置页 HTML（自包含，外链两个 ES 模块）。

        旧页自带骨架与样式（它是 #31 收线前仍然在线的副本），但**危险分级
        与统一确认层是壳级共享资产**（父 spec §6.5）：服务端在这里把
        ``config_shell`` 的同一份 CSS/HTML/JS 注进占位符，两页共用一套
        分级视觉与确认层——而不是在旧页里再写一份。
        """
        html_path = Path(self.project_dir) / "config" / "config_page.html"
        if not html_path.exists():
            return web.Response(text="config_page.html not found", status=404)
        html = html_path.read_text(encoding="utf-8")
        for name, value in (
            ("__CONFIRM_CSS__", shell.CONFIRM_CSS),
            ("__CONFIRM_JS__", shell.SHELL_CONFIRM_JS),
            ("__CONFIRM__", shell.CONFIRM_HTML),
        ):
            html = html.replace(name, value)
        return web.Response(
            text=html,
            content_type="text/html",
            charset="utf-8",
        )

    async def handle_state_model(self, request):
        """页面状态模型（无 DOM 依赖的 ES 模块）。

        页面以 ``<script type="module">`` 引入它，所以必须用 ``text/javascript``
        返回（浏览器对模块脚本的 MIME 类型是硬校验的）。
        """
        js_path = Path(self.project_dir) / "config" / "config_state_model.js"
        if not js_path.exists():
            return web.Response(text="config_state_model.js not found", status=404)
        return web.Response(
            text=js_path.read_text(encoding="utf-8"),
            content_type="text/javascript",
            charset="utf-8",
        )

    async def handle_domain_page_script(self, request):
        """域页的编辑脚本（无 DOM 依赖的模块，五个域页共用同一份）。"""
        js_path = Path(self.project_dir) / "config" / "config_domain_page.js"
        if not js_path.exists():
            return web.Response(text="config_domain_page.js not found", status=404)
        return web.Response(
            text=js_path.read_text(encoding="utf-8"),
            content_type="text/javascript",
            charset="utf-8",
        )

    async def handle_danger_model(self, request):
        """危险分级模型（无 DOM 依赖的 ES 模块，父 spec §6）。

        与 ``config_state_model.js`` 同一机制：两份在线副本（新设备域页 /
        旧八组页面）共用同一份分级判定与后果文案——分级只有一处实现，
        两页的表现才不会分叉。"""
        js_path = Path(self.project_dir) / "config" / "config_danger_model.js"
        if not js_path.exists():
            return web.Response(text="config_danger_model.js not found", status=404)
        return web.Response(
            text=js_path.read_text(encoding="utf-8"),
            content_type="text/javascript",
            charset="utf-8",
        )

    # ---------------- 页面拓扑：五域 + 逃生口（父 spec §4 / §8.1 / §8.2） ----------------
    #
    # 三条规则都落在这一节里：
    #   1. 每个页面恰一个规范 URL（尾斜杠收尾），由应用层 301 从无斜杠形态归一
    #      —— 现状 slash 行为取决于从 8080 还是 8003 进（§8.2），根因在仓库外的
    #      nginx 配置；应用层自己发 301 就不依赖它了；
    #   2. 域 slug（dialogue / engine / tools / devices / system / raw）一经上线
    #      即用户契约（ADR-0012）—— **本次新增，不动旧的 /xiaozhi/config/**；
    #   3. 保存 / 重启动作区**只在配置编辑页渲染**：只读页（逃生口）与未上线域的
    #      占位页拿不到按钮（§4.2）。

    #: 未上线域（本票只交付占位页，内容是 #27/#28/#29 的事）。
    _DOMAIN_BY_SLUG = {d["slug"]: d for d in shell.DOMAINS}
    #: 逃生口与未上线域都是「无编辑对象」的页：动作区不渲染（§4.2）。
    _RAW_SLUG = shell.RAW_ESCAPE["slug"]

    @staticmethod
    def _permanent_redirect(request) -> web.Response:
        """301 到**尾斜杠规范形**，保留查询串。

        用 301 而非 302：slug 是用户契约、规范形是长期形态（§8.2）。
        目标从请求路径算（加个尾斜杠），不硬编码 host。
        """
        target = request.path + "/"
        if request.query_string:
            target += "?" + request.query_string
        return web.HTTPMovedPermanently(location=target)

    async def handle_config_root_redirect(self, request):
        """/xiaozhi/config（无斜杠）→ /xiaozhi/config/（规范形）。"""
        return self._permanent_redirect(request)

    async def handle_domain_redirect(self, request):
        """/xiaozhi/config/<slug> → /xiaozhi/config/<slug>/（规范形）。"""
        return self._permanent_redirect(request)

    def _read_page(self, name: str) -> str:
        return (Path(self.project_dir) / "config" / name).read_text(encoding="utf-8")

    def _render_domain_page(self, slug: str) -> web.Response:
        """渲染一个域页（有内容的域）或占位页（未上线的域）。

        共用 ``config_domain_page.html`` 骨架：域表以 JSON 注入，渲染逻辑在
        ``config_domain_page.js``。占位页复用同一副壳，正文换成 ``placeholder``。
        """
        skeleton = self._read_page("config_domain_page.html")
        if slug == self._RAW_SLUG:
            return self._render_escape_page(skeleton)

        domain = self._DOMAIN_BY_SLUG.get(slug)
        if domain is None:
            return web.Response(text="未知的页面域", status=404)
        schema = page_domains.DOMAIN_SCHEMAS.get(slug)
        actions = ""
        savebar = ""
        page_desc = domain["blurb"]
        page_icon = domain["icon"]
        # 设备家族的两页（设备域 / 摄像头页）在顶栏互链（§4.4）。
        nav = shell.render_topnav("devices") if slug == "devices" else ""
        # 引擎类目（六族）随域表一起注入：``classifyDirty`` 靠它把「真在引擎域里的
        # 路径」与「域内散字段」分开（非引擎散字段归「重启后生效」而不是「仅提前
        # 配好」）。类目清单的单一事实源在 ``page_domains.ENGINE_CATEGORIES``，
        # 页面不写第二份（页面里两份类目字面量就是两把尺子）。
        engine_categories = list(page_domains.ENGINE_CATEGORIES)
        # 意图分支（`Intent.*` 的键）也随域表注入：工具域的 `classifyDirty` 靠它
        # 把「选中 / 未选中分支」分开（未选中分支 = 仅提前配好）。分支清单的
        # 单一事实源在 `page_domains.INTENT_BRANCHES`——树上只有配过的分支，
        # 少一条就会让那条分支的字段归错组（不能从配置树反推）。
        intent_branches = list(page_domains.INTENT_BRANCHES)
        # 摄像头入口（§4.4）与运行时面面板（AC 2）也随域表注入：它们的单一事实源
        # 在 `config_shell.CAMERA_PAGE` / `page_domains.DEVICES_RUNTIME`，页面只消费。
        # 每个域页都带上摄像头入口：它是壳级事实，不止设备域用得上（单一事实源）。
        camera_entry = dict(shell.CAMERA_PAGE)
        if schema is not None:
            body = ""  # 正文由 config_domain_page.js 按注入的域表渲染
            actions = (
                '<button class="btn primary" id="saveBtn" '
                'onclick="xzhSave()" disabled>💾 保存修改</button>'
                # 重启服务 = 警示级（§6.2 / §6.4 修严重度倒置）：琥珀 + ⚠，
                # 不是红色实底。它是自愈操作（断连 10-30s 后拉起），原先把
                # 红色给了它是三处严重度倒置之一。
                '<button class="btn warn" data-danger-op="restart_server" '
                'data-danger-level="warning" onclick="xzhRestart()">⚠ ♻️ 重启服务</button>'
            )
            savebar = (
                '<div class="savebar"><span class="info" id="saveInfo">'
                '暂无未保存修改</span></div>'
            )
            schema_json = json.dumps(
                dict(asdict(schema), engine_categories=engine_categories,
                     intent_branches=intent_branches, camera=camera_entry),
                ensure_ascii=False)
        else:
            # 未上线域：占位正文，且**动作区不渲染**（没有可保存的对象）。
            body = shell.render_placeholder(domain)
            # 占位页不需要域表驱动的编辑，但脚本仍以空表启动（骨架同一副）。
            schema_json = json.dumps(
                {"slug": slug, "label": domain["label"], "groups": [],
                 "engine_categories": engine_categories,
                 "intent_branches": intent_branches, "runtime_panels": [],
                 "camera": camera_entry},
                ensure_ascii=False)

        html = self._fill_skeleton(
            skeleton, title=f"小智 · {domain['label']}", active=slug,
            page_label=domain["label"], page_icon=page_icon, page_desc=page_desc,
            actions=actions, savebar=savebar, schema_json=schema_json, body=body,
            nav=nav)
        return web.Response(text=html, content_type="text/html", charset="utf-8")

    def _render_escape_page(self, skeleton: str) -> web.Response:
        """逃生口只读页：一页看完**整份**原始配置（§4.3）。

        与旧页面的「全部配置」是同一份数据、同一个读取路径（api/full 的掩码树），
        但它是**独立页 + 侧栏底部的非域入口**，不是侧栏第六域。没有动作区 ——
        只读页没有可保存的对象（§4.2）。
        """
        body = (
            '<section class="group"><h2>📦 原始配置 <span class="badge">只读</span></h2>'
            '<div class="desc">当前生效的完整配置（敏感字段已掩码）。'
            '改配置请到对应的域页；这里是孤儿字段的最后兑底，Ctrl+F 全局搜。</div>'
            '<div class="jsonbox" id="rawView">正在加载…</div></section>'
        )
        schema_json = json.dumps(
            {"slug": self._RAW_SLUG, "label": shell.RAW_ESCAPE["label"],
             "groups": [], "engine_categories": list(page_domains.ENGINE_CATEGORIES),
             "intent_branches": list(page_domains.INTENT_BRANCHES),
             "runtime_panels": [], "camera": dict(shell.CAMERA_PAGE)},
            ensure_ascii=False)
        html = self._fill_skeleton(
            skeleton, title="小智 · 原始配置", active=self._RAW_SLUG,
            page_label="原始配置", page_icon=shell.RAW_ESCAPE["icon"],
            page_desc="整份原始配置，只读。",
            actions="", savebar="", schema_json=schema_json, body=body,
            page_script=RAW_PAGE_JS)
        return web.Response(text=html, content_type="text/html", charset="utf-8")

    def _fill_skeleton(self, skeleton: str, **kw) -> str:
        """把壳的拼装结果填进骨架占位符。

        骨架里留的是字面占位符（``__SIDEBAR__`` 等）而不是 ``str.format`` 的
        ``{}``：页面正文与 CSS 里花括号太多，用 ``format`` 会立刻炸。

        ``html.escape`` 用不上——这里填的全是**自己生成**的 HTML 片段，不是
        用户数据；唯一的例外是域表 JSON，它由 ``json.dumps`` 产物再转义 ``<``
        与 ``&``（防 ``</script>`` 提前结束标签）。
        """
        topbar = shell.render_topbar(
            f"小智 · {kw['title'].split('· ', 1)[-1]}", kw.get("actions", ""),
            kw.get("nav", ""))
        schema_json = (kw.get("schema_json", "{}")
                       .replace("<", "\\u003c").replace("&", "\\u0026"))
        html = skeleton
        for name, value in (
            ("__TITLE__", kw["title"]),
            ("__SHELL_CSS__", shell.SHELL_CSS),
            ("__TOPBAR__", topbar),
            ("__SIDEBAR__", shell.render_sidebar(kw["active"])),
            ("__PAGE_ICON__", kw.get("page_icon", "⚙️")),
            ("__PAGE_LABEL__", kw.get("page_label", "配置")),
            ("__PAGE_DESC__", kw.get("page_desc", "")),
            ("__SCHEMA_JSON__", schema_json),
            ("__SAVEBAR__", kw.get("savebar", "")),
            ("__CONFIRM__", shell.CONFIRM_HTML),
            ("__SHELL_JS__", shell.SHELL_JS + shell.SHELL_CONFIRM_JS),
            ("__PAGE_STYLE__", kw.get("page_style", "")),
            ("__PAGE_JS__", kw.get("page_script", "")),
            ("__BODY__", kw.get("body", "")),
        ):
            html = html.replace(name, value)
        return _strip_template_comment(html)

    async def handle_domain_page(self, request):
        """域页 / 逃生口页（尾斜杠规范形）。"""
        slug = request.match_info["slug"]
        return self._render_domain_page(slug)

    # ---------------- 摄像头页归位（父 spec §4.4 / §8.1 / §8.2） ----------------
    #
    # 摄像头页是**设备域的实时视图**：归「看设备」家族，与设备域同壳互链，
    # 保留独立 URL（`/xiaozhi/camera/`）供书签挂机监控，不占一级导航。
    #
    # 三个决定都落在这一节：
    #   1. 页面骨架用**共享壳**（同一份 `config_domain_page.html` + `render_sidebar`
    #      + `render_topbar`），所以它自带五域侧栏与回设备域的路（§4.2「各页自带
    #      外壳被否」）；暗色监控页只做局部样式覆盖（§4.2 明文允许）。
    #   2. 无斜杠形态 301 到规范形，**由应用层发**（§8.2）——不再依赖仓库外的
    #      nginx，也不再用双注册（旧写法两条路由各自返回同一份 body，
    #      同一命名空间两套语义）。
    #   3. `api/*` 子路由**不**跟着 301：它们是 API 而不是页面，设备固件与
    #      页面脚本直接调它们（§8.2 的规则只约束页面 URL）。

    async def handle_camera_redirect(self, request):
        """/xiaozhi/camera → /xiaozhi/camera/（尾斜杠规范形，§8.2）。

        与域页同一机制（`_permanent_redirect`）：301 + 保留查询串。
        """
        return self._permanent_redirect(request)

    async def handle_camera_page(self, request):
        """摄像头监控页（规范形 `/xiaozhi/camera/`）——注入共享壳。

        正文/样式/脚本三段来自 `core.api.camera_handler`（它只交付摄像头
        专有的东西），骨架与导航来自共享壳（§4.2 的唯一落点）。
        没有可保存对象 → 动作区不渲染（§4.2）。
        """
        from core.api.camera_handler import CAMERA_BODY, CAMERA_CSS, CAMERA_JS

        skeleton = self._read_page("config_domain_page.html")
        schema_json = json.dumps(
            {"slug": shell.CAMERA_PAGE["slug"],
             "label": shell.CAMERA_PAGE["label"], "groups": [],
             "engine_categories": list(page_domains.ENGINE_CATEGORIES),
             "intent_branches": list(page_domains.INTENT_BRANCHES),
             "runtime_panels": [], "camera": dict(shell.CAMERA_PAGE)},
            ensure_ascii=False)
        html = self._fill_skeleton(
            skeleton, title=f"小智 · {shell.CAMERA_PAGE['label']}",
            active=shell.CAMERA_PAGE["slug"],
            page_label=shell.CAMERA_PAGE["label"],
            page_icon=shell.CAMERA_PAGE["icon"],
            page_desc="设备摄像头实时画面。保留独立 URL，可当挂机监控的书签。",
            actions="", savebar="", schema_json=schema_json,
            body=CAMERA_BODY, page_style=CAMERA_CSS, page_script=CAMERA_JS,
            nav=shell.render_topnav(shell.CAMERA_PAGE["slug"]))
        return web.Response(text=html, content_type="text/html", charset="utf-8")

    async def handle_auth(self, request):
        """兼容接口：PIN 已移除，直接放行（前端登录逻辑保留，避免改动）。"""
        return web.json_response({"ok": True})

    async def handle_full(self, request):
        """完整生效配置：掩码值 + 敏感字段的显式存在信号（二者分离）。

        - ``config``：合并后的完整配置，敏感字段掩码（向后兼容）。
        - ``config_state``：``{路径: {"configured": bool}}``，只对敏感键给出，
          判据是「配置里真的有值」而不是「掩码长什么样」。

        页面据此判定密钥三态（未配置 / 已配置 / 已配置但要替换），
        不再用正则猜掩码形态 —— 那是两个同源显示 bug 的根因。
        """
        denied = self._require_session(request)
        if denied:
            return denied
        merged = self._merged_config()
        masked, _ = _mask_tree(merged)
        return web.json_response({
            "ok": True,
            "config": masked,
            "config_state": _secret_state(merged),
        })

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

        注意 ``config_state`` 只是读侧信号，保存协议不变（仍只认 before/after
        两棵树）——保存是端到端整树 diff，不因这次改动而改变往返语义。
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

    # ==================== 引擎连通性测试 ====================

    # 测试用提示词：短、无需工具，能暴露「思考吃光 max_tokens」的毛病
    TEST_LLM_PROMPT = "你好，请用一句话介绍你自己。"

    def _resolve_test_config(self, engine: str, page_config) -> dict:
        """把页面传来的引擎参数与已保存配置合并（页面值优先）。

        页面上 api_key 是掩码占位或空值时保留已保存的真实值，
        因此除了密钥本身，页面上改了还没保存的参数也能即时测。
        """
        saved = self._merged_config().get("LLM", {}).get(engine, {})
        merged = dict(saved) if isinstance(saved, dict) else {}
        for key, value in (page_config or {}).items():
            if key == "api_key" and (
                value in (None, "") or self._is_mask_placeholder(value)
            ):
                continue
            merged[key] = value
        return merged

    @staticmethod
    def _probe_llm(cfg: dict) -> dict:
        """同步跑一次真实请求并量耗时（在线程池中调用）。"""
        from core.utils.llm import create_instance

        provider = create_instance(cfg.get("type") or "openai", cfg)
        dialogue = [{"role": "user", "content": ConfigHandler.TEST_LLM_PROMPT}]
        return provider.probe_stream(dialogue)

    async def handle_test_llm(self, request):
        """测试 LLM 引擎能不能用、推理档位的实际代价（页面「测试」按钮）。

        返回 200 + ok 标志：模型/网关的报错原样带回页面，
        因为「哪一步断了」看那句报错就够了。
        """
        denied = self._require_session(request)
        if denied:
            return denied
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求格式错误"})

        engine = str(body.get("engine") or "").strip()
        if not engine:
            return web.json_response({"ok": False, "error": "缺少引擎名"})

        cfg = self._resolve_test_config(engine, body.get("config"))
        if not cfg:
            return web.json_response(
                {"ok": False, "engine": engine, "error": f"未找到引擎配置: {engine}"}
            )

        llm_type = cfg.get("type") or "openai"
        if llm_type != "openai":
            return web.json_response({
                "ok": False, "engine": engine,
                "error": f"暂只支持 openai 类型引擎的测试（当前 type={llm_type}）",
            })

        import asyncio

        timeout = 60.0
        try:
            result = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, self._probe_llm, cfg),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return web.json_response({
                "ok": False, "engine": engine,
                "error": f"请求超过 {int(timeout)} 秒无响应，网关可能不通或 max_tokens 过大",
            })
        except Exception as e:
            return web.json_response({
                "ok": False, "engine": engine,
                "error": f"{type(e).__name__}: {str(e)[:500]}",
            })

        result.update({"ok": True, "engine": engine})
        self.logger.bind(tag=TAG).info(
            f"引擎测试 {engine}: {result.get('model')} "
            f"首句 {result.get('first_content_ms')}ms 思考 {result.get('reasoning_chars')}字"
        )
        return web.json_response(result)

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
