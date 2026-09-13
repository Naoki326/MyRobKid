#!/usr/bin/env python3
"""危险分级与统一确认层的 HTTP 契约缝（issue #30 建，#31 收线后改口径）。

契约（父 spec §6，逐条落实）：

1. **落位表**（§6.2）——五个操作的三级归位（常规 / 警示 / 危险）由
   ``config_danger_model.js`` 的纯函数算出，node 缝钉判定（见
   ``test_config_danger_model.mjs``）；本文件钉**页面真的在消费它**，
   而不是各写一套。
2. **共享资产**（§6.5）——统一页内确认层是**壳级资产**：全部域页共用同一
   个容器（``config_shell.CONFIRM_HTML``）、同一段脚本（``SHELL_CONFIRM_JS``）、
   同一份分级模型（``config_danger_model.js``）。#30 时这句是「两页都拿到了
   同一份」；#31 收线删除旧八组页面后，**消费方只剩域页**——但断言不变形：
   仍然针「域页拿到的就是壳里那份常量」（逐字相等），而不是「有个差不多的
   容器」。共享资产**不得因为旧页退役被连带删掉**。
3. **原生 confirm 退场**（AC 4）——本票涉及的操作不得再有 ``window.confirm``。
   判别力：这条断言用**计数**而不是存在性——「保留旧 confirm 又另写一份」
   能绕过存在性断言，计数绕不过。
4. **严重度倒置修复**（§6.4）——重启服务/重启设备 红→琥珀、删除固件 灰→红、
   上传固件 素→红。本文件钉按钮的**分级属性**（不是颜色，颜色是 CSS）。
5. **删除固件的打字摩擦 + 上传固件的动态数字**（§6.3）。
6. **旧页退役的收线清单**（#31）——六个运行时面函数在仓库里只有一份；
   后端接口一个不少；「试连 LLM」从旧页迁到引擎域（§6.2 常规级，零确认）。

为什么不在本文件测分级判定本身：分级判定是纯逻辑，归 node 缝。这里只钉
HTTP 可达的交付内容（域名、资产路由、页面脚本消费方式、按钮声明）。

运行：server/.venv/bin/python -m unittest discover -s server/tests -t server
"""
import re
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from core.api.config_handler import ConfigHandler  # noqa: E402
from core.api.ota_handler import OTAHandler  # noqa: E402
from core.utils import device_registry  # noqa: E402
from config import config_shell as shell  # noqa: E402

#: §6.2 落位表：操作 → 级别。写死在这里，是分级表被改动时唯一会红的东西。
#: 逐条对照（issue 的 AC 要求「§6.2 落位表逐条对照」）：
LEVELS = {
    "save": "normal",
    "test_llm": "normal",
    "overwrite_secret": "normal",
    "restart_server": "warning",
    "smartconfig": "warning",
    "delete_firmware": "danger",
    "upload_firmware": "danger",
    # 重启设备是动态项（警示 / 危险），不在这张静态表里。
}
DYNAMIC = ["reboot_device"]

#: §6.4 点名的三处严重度倒置（旧页曾犯过，新页不得再犯）。
SEVERITY_INVERSIONS = {
    "restart_server": "warn",   # 红 → 琥珀
    "delete_firmware": "danger",  # 灰 → 红
    "upload_firmware": "danger",  # 素 → 红
}


def _write_yaml(path, data):
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


class DangerGradingContract(AioHTTPTestCase):
    """分级模型 + 共享确认层 + 两份副本的一致性。"""

    async def get_application(self):
        self._tmp = tempfile.TemporaryDirectory()
        project = Path(self._tmp.name)
        (project / "data").mkdir()
        (project / "data" / "bin").mkdir()
        (project / "config").mkdir()
        real = SERVER_ROOT / "config"
        # #31 收线：旧八组页面（config_page.html）已删除，不再拷它。
        for name in ("config_domain_page.html", "config_domain_page.js",
                     "config_state_model.js", "config_danger_model.js"):
            (project / "config" / name).write_text(
                (real / name).read_text(encoding="utf-8"), encoding="utf-8")

        _write_yaml(project / "config.yaml", {
            "server": {
                "ip": "0.0.0.0", "port": 8002, "http_port": 8003,
                "websocket": "wss://api.tenclass.net/xiaozhi/v1/",
                "timezone_offset": 8,
                "auth_key": "server-auth-key-0000",
                "auth": {"enabled": True, "allowed_devices": []},
                "mqtt_gateway": None, "mqtt_signature_key": None,
                "udp_gateway": None,
            },
            "tts_audio_send_delay": 0,
            "xiaozhi": {"type": "websocket", "version": 1,
                        "transport": "websocket",
                        "audio_params": {"format": "opus",
                                         "sample_rate": 24000,
                                         "channels": 1,
                                         "frame_duration": 60}},
        })
        _write_yaml(project / "data" / ".config.yaml", {
            "server": {"websocket": "ws://192.168.18.11:8000/xiaozhi/v1/"},
        })

        self.handler = ConfigHandler({}, str(project))
        # OTA handler 只用来验证「设备自报的型号/版本会进注册表」这条数据源。
        # 它的 bin_dir 默认是 ``os.getcwd()/data/bin``，测试里改到临时目录，
        # 避免往仓库根写目录。
        self.ota = OTAHandler({
            "server": {
                "auth_key": "server-auth-key-0000",
                "auth": {"enabled": True, "allowed_devices": []},
                "mqtt_gateway": None, "mqtt_signature_key": None,
                "websocket": "wss://api.tenclass.net/xiaozhi/v1/",
                "websocket_backup": "",
                "port": 8002, "http_port": 8003, "timezone_offset": 8,
            },
            "firmware_cache_ttl": 30,
        })
        self.ota.bin_dir = str(project / "data" / "bin")
        app = web.Application()
        app.add_routes([
            # 根路径现在是 302 到首域（§4.1）——旧页已退役（#31），
            # 这里注册它是为了钉住「不再有页面在根路径渲染」。
            web.get("/xiaozhi/config/",
                    self.handler.handle_config_root_default),
            web.post("/xiaozhi/ota/", self.ota.handle_post),
            web.get("/xiaozhi/ota/", self.ota.handle_get),
            web.get("/xiaozhi/config/{slug}", self.handler.handle_domain_redirect),
            web.get("/xiaozhi/config/{slug}/", self.handler.handle_domain_page),
            web.get("/xiaozhi/config/config_domain_page.js",
                    self.handler.handle_domain_page_script),
            web.get("/xiaozhi/config/config_state_model.js",
                    self.handler.handle_state_model),
            web.get("/xiaozhi/config/config_danger_model.js",
                    self.handler.handle_danger_model),
            web.get("/xiaozhi/config/api/full", self.handler.handle_full),
            web.get("/xiaozhi/config/api/meta", self.handler.handle_meta),
            web.get("/xiaozhi/config/api/devices", self.handler.handle_devices),
            web.get("/xiaozhi/config/api/firmware",
                    self.handler.handle_firmware_list),
            # 旧页退役后仍在的后端接口（收线清单第 4 条）：注册它们，
            # 不为了真的执行副作用（有副作用的用 OPTIONS / 空 body 探路）。
            web.post("/xiaozhi/config/api/auth", self.handler.handle_auth),
            web.post("/xiaozhi/config/api/save", self.handler.handle_save),
            web.post("/xiaozhi/config/api/restart",
                     self.handler.handle_restart),
            web.post("/xiaozhi/config/api/test-llm",
                     self.handler.handle_test_llm),
            web.get("/xiaozhi/config/api/local-wifi",
                    self.handler.handle_local_wifi),
            web.post("/xiaozhi/config/api/smartconfig",
                     self.handler.handle_smartconfig),
            web.post("/xiaozhi/config/api/firmware/upload",
                     self.handler.handle_firmware_upload),
            web.post("/xiaozhi/config/api/firmware/delete",
                     self.handler.handle_firmware_delete),
        ])
        return app

    async def asyncTearDown(self):
        # 注册表是模块级全局单例：清掉本用例塞进去的在线设备与自报信息，
        # 免得污染同进程里跑的下一个用例。
        for did in list(device_registry.get_online().keys()):
            device_registry.unregister(did, device_registry.get_online(did))
        device_registry._reported.clear()
        self._tmp.cleanup()
        await super().asyncTearDown()

    async def _get_text(self, path):
        resp = await self.client.request("GET", path, allow_redirects=False)
        self.assertEqual(resp.status, 200, f"{path} 必须可达")
        return await resp.text()

    async def _devices_html(self):
        return await self._get_text("/xiaozhi/config/devices/")

    async def _engine_html(self):
        return await self._get_text("/xiaozhi/config/engine/")

    async def _model_js(self):
        return await self._get_text("/xiaozhi/config/config_danger_model.js")

    # ── 1. 分级模型是可交付的资产（两份副本共用的前提） ────────────
    async def test_danger_model_is_served_as_an_es_module(self):
        """``config_danger_model.js`` 必须能被浏览器当模块加载。

        判别力：两份在线副本都靠 import 它拿分级判定。它若 404 或 MIME 不对，
        页面里的 import 会整段失败——而失败的表现是「整页空白」，不是
        「分级失效」，排查方向完全不同。模块脚本的 MIME 是硬校验。
        """
        resp = await self.client.request(
            "GET", "/xiaozhi/config/config_danger_model.js")
        self.assertEqual(resp.status, 200)
        self.assertIn("text/javascript", resp.headers["Content-Type"])
        js = await resp.text()
        # 落位表与三级枚举都在交付内容里（导出给两份副本消费）。
        for token in ("OPERATION_LEVELS", "reboot_device", "upload_firmware",
                      "delete_firmware", "smartconfig", "restart_server",
                      "hasNewerFirmware", "operationConsequences",
                      "needsVersionTyping"):
            self.assertIn(token, js, f"分级模型必须交付 {token}")

    async def test_danger_model_has_no_dom_dependency(self):
        """模型是**纯**模块：任何 ``document`` / ``window`` 引用都会让 node 缝失败。

        判别力：这条是「无 DOM 依赖」这条设计约束的唯一可执行证据——
        模型里一旦在模块顶层碰 DOM，node --test 会立刻报 document is not
        defined。这里用文本断言提前把这种漂移钉在 HTTP 缝上。
        """
        js = await self._model_js()
        # 剥掉注释再找（注释里可以讨论 DOM，代码里不行）。
        body = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
        body = re.sub(r"//[^\n]*", "", body)
        self.assertNotIn("document.", body, "分级模型不得引用 document")
        self.assertNotIn("window.", body, "分级模型不得引用 window")

    # ── 2. §6.2 落位表：五个操作与一个动态项都登记了 ──────────────
    async def test_operation_roster_is_registered_in_the_model(self):
        """§6.2 表的每个操作都在模型里显式登记（未登记的操作会抛错）。

        判别力：这条钉的是**落位表的完整性**。漏登记一个操作的表现是
        「这个按钮没有确认层」——它看起来像前端忘了接，而不是表漏了一行。
        """
        js = await self._model_js()
        # 静态表里的操作名必须出现在 OPERATION_LEVELS 的段落里。
        table = js[js.index("OPERATION_LEVELS = {"):]
        table = table[:table.index("};")]
        for op in LEVELS:
            self.assertRegex(table, rf"\b{op}\s*:",
                             f"{op} 必须在 §6.2 落位表里显式登记")
        # 动态项单独声明（不许塞进静态表定死）。
        dyn = js[js.index("DYNAMIC_OPERATIONS = "):]
        dyn = dyn[:dyn.index("]")]
        for op in DYNAMIC:
            self.assertIn(f"'{op}'", dyn, f"{op} 是动态项（§6.2）")

    # ── 3. 共享资产：域页拿到壳里那一份确认层（旧页退役后仍被消费）────
    async def test_domain_pages_carry_the_shells_confirm_layer(self):
        """全部域页都注入**壳里那一份**确认层（§6.5），且共享资产没被连带删。

        判别力：确认层是「共享外壳的一件组件」。#31 收线删除了旧八组页面
        （它曾是第二个消费方），删除时最危险的动作是「把共享资产一起扫出去」
        ——那是本票清单里明写要防的一件事。所以这里仍然对**容器与脚本断同源**
        （针取自壳的同一常量，再在渲染产物里找它），而不是「都有个差不多的
        容器」；并逐域断言，防止某一天哪一域把壳绕过去。

        注：旧页自包含 HTML，它的注入曾发生在 ``handle_page``（已删）。现在
        注入只发生在 ``_fill_skeleton`` 一处，所以「一份常量、一处填」。
        """
        page_js = await self._get_text("/xiaozhi/config/config_domain_page.js")
        # 逐域断：五个域页都从同一处拿到壳的确认层（不挑一个域代表全部）。
        for slug in ("dialogue", "engine", "tools", "devices", "system", "raw"):
            html = await self._get_text(f"/xiaozhi/config/{slug}/")
            self.assertIn(shell.CONFIRM_HTML, html,
                          f"{slug} 页必须注入壳的确认层容器")
            self.assertIn(shell.SHELL_CONFIRM_JS, html,
                          f"{slug} 页必须注入壳的确认层脚本")
            self.assertIn(".btn.warn", html,
                          f"{slug} 页必须注入分级的颜色载体")
        # 分级 CSS 本身仍在壳的常量里（没有被旧页退役一起删掉）。
        self.assertIn(".btn.warn", shell.CONFIRM_CSS)
        # 分级模型**真被消费**（不是外层 ``<script src>`` 空加载：模块只有
        # export、无副作用，空加载不产生任何效果）。消费方是域页脚本。
        self.assertIn("from './config_danger_model.js'", page_js,
                      "域页必须 import 共享的分级模型（空加载不算消费）")

        # 三载体的单一事实源：壳里的 VISUAL_FALLBACK **只是兜底**，正常路径
        # 必须由页面把模块的 LEVEL_VISUALS 传进去（壳优先用 o.visual）。
        # 判别力：删掉壳里对 o.visual 的读取、或删掉页面的 ``visual:`` 传参，
        # 页面上确认层的图标/文案就会静默退回兜底副本——那种退化必须变红。
        self.assertIn("o.visual", shell.SHELL_CONFIRM_JS,
                      "壳必须优先消费调用方传来的模块视觉表（否则兜底副本会偷偷变成事实源）")
        self.assertIn("visual: LEVEL_VISUALS[level]", page_js,
                      "域页必须把模块读到的三载体传给确认层")

    # ── 4. AC 4：本票涉及的操作不再用原生 confirm ────────────────
    async def test_no_native_confirm_left_in_the_pages(self):
        """页面不再有危险操作使用的 ``window.confirm``（AC 4）。

        判别力：**计数断言**。存在性断言（``assertIn('xzhConfirm', js)``）可被
        「保留旧 confirm 又另写一份」绕过；计数绕不过——旧 confirm 只要还在，
        计数就非零。

        判别力（#31 补）：旧页当年正是一个“还在用 confirm 的第二个副本”。
        它已删除，但这条断言不能只对域页跑一遍就完——所以这里同时跑
        **域页 HTML（对话 / 设备两页）与域页脚本**，把壳自身的离开确认
        （§4.6 的 ``window.confirm``，不是危险操作确认层）原文剥掉后再数。
        """
        for name, html in (("设备域页", await self._devices_html()),
                           ("对话域页",
                            await self._get_text("/xiaozhi/config/dialogue/"))):
            # 壳的 SHELL_JS 里有一条 beforeunload/切域前 ``window.confirm``
            # （§4.6 的**离开确认**，不是危险操作确认层），确认层脚本里也有一条
            # 容器缺失时的 ``window.confirm`` 兼底（两者都不是危险操作的
            # 确认机制）——先按**原文**剥掉它们（不能在去注释之后再剥：
            # 去注释正则会把 JS 字符串里的 ``//`` 也当成注释前缀，文本对不上），
            # 否则这条计数断言会把无辜的东西抓到。
            body = html.replace(shell.SHELL_JS, "")
            body = body.replace(shell.SHELL_CONFIRM_JS, "")
            body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
            body = re.sub(r"//[^\n]*", "", body)
            self.assertEqual(
                len(re.findall(r"window\.confirm\s*\(", body)), 0,
                f"{name}仍有 window.confirm——危险操作用它就没有分级编码")
        # 危险操作的五个函数体内不得再有裸 confirm(（域页脚本是它们的唯一实现）。
        devices_js = await self._get_text(
            "/xiaozhi/config/config_domain_page.js")
        for fn in ("rebootDevice", "uploadFirmware", "deleteFirmware",
                   "restartServer", "sendSmartConfig"):
            m = re.search(rf"async function {fn}\([^)]*\)\s*\{{(.*?)\n\}}",
                          devices_js, re.S)
            self.assertIsNotNone(m, f"{fn} 必须还在（唯一实现）")
            self.assertNotIn("confirm(", m.group(1),
                             f"{fn} 内不得再有原生 confirm")
            self.assertIn("confirmDanger(", m.group(1),
                          f"{fn} 必须改走统一页内确认层")

    # ── 5. §6.3 两种确认形态：打字摩擦 + 动态数字 ────────────────
    async def test_delete_firmware_requires_typing_the_version(self):
        """删除固件带「输入固件版本号」的打字摩擦（§6.3）。

        判别力：``needsVersionTyping`` 只在模型里定义了不够——页面必须真的
        把 ``typeToConfirm`` 交给确认层。断言调用了 ``needsVersionTyping``
        且确认层参数里出现 ``typeToConfirm``。

        #31：旧页退役后只断言唯一实现（域页脚本）。
        """
        text = await self._get_text("/xiaozhi/config/config_domain_page.js")
        self.assertIn("needsVersionTyping", text,
                      "域页必须用 needsVersionTyping 决定打字摩擦")
        self.assertIn("typeToConfirm", text,
                      "域页的确认层要带 typeToConfirm（否则打不开按钮）")

    async def test_upload_firmware_confirmation_has_dynamic_numbers(self):
        """上传固件的确认层带动态数字，且武装对象写准（§6.3 / ADR-0002）。

        判别力：动态数字来自**两个既有列表**（§6.6 同源）。断言页面把
        ``devices`` / ``firmwares`` / ``pendingFirmware`` 交给确认层——写成
        硬编码文案（「所有在线设备」）会在这里红。
        """
        devices_js = await self._get_text(
            "/xiaozhi/config/config_domain_page.js")
        m = re.search(r"async function uploadFirmware\([^)]*\)\s*\{(.*?)\n\}",
                      devices_js, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("RUNTIME.devices", body, "在线设备数必须来自既有列表")
        self.assertIn("RUNTIME.firmwares", body, "固件库必须来自既有列表")
        self.assertIn("pendingFirmware", body, "目标固件版本必须随确认层传入")
        # 武装对象的措辞在模型里；页面消费 operationConsequences 即拿到它。
        self.assertIn("operationConsequences", devices_js)
        model = await self._model_js()
        self.assertIn("开机自检", model,
                      "武装对象必须是「未来所有开机自检」（ADR-0002）")

    # ── 6. §6.4 三处严重度倒置修复（按钮的分级声明） ─────────────
    async def test_three_severity_inversions_are_fixed(self):
        """重启服务/重启设备 红→琥珀、删除固件 灰→红、上传固件 素→红。

        判别力：断言的是按钮的**分级属性**（``data-danger-level``）而不是
        CSS 颜色——颜色是载体之一，属性才是分级本身。

        #31：旧页退役后重服务按钮的声明在**每个域页**的顶栏上（§4.2 动作区
        只在编辑页渲染），所以逐域断——只断设备域会漏掉其他四页的顶栏。
        """
        for slug in ("dialogue", "engine", "tools", "devices", "system"):
            html = await self._get_text(f"/xiaozhi/config/{slug}/")
            # 重启服务：警示（琥珀），不是 danger。
            m = re.search(
                r'<button[^>]*data-danger-op="restart_server"[^>]*>', html)
            self.assertIsNotNone(m, f"{slug} 页的重启服务按钮必须带分级声明")
            self.assertIn('data-danger-level="warning"', m.group(0),
                          f"{slug}：重启服务是警示（§6.4 红→琥珀）")
            self.assertIn("btn warn", m.group(0),
                          f"{slug}：重启服务必须用琥珀载体")
            # 不得再有红色的重启服务按钮（旧页当年就是这个倒置）。
            self.assertNotIn(
                '<button class="btn danger" onclick="restartServer()"', html,
                f"{slug}：重启服务仍是红实底（§6.4 点名的严重度倒置）")

        # 设备域页的运行时面：重启设备（初始警示）与删除/上传固件（危险）。
        js = await self._get_text("/xiaozhi/config/config_domain_page.js")
        self.assertIn('data-danger-op="reboot_device"', js)
        self.assertIn('data-danger-level="${esc(lvl)}"', js,
                      "重启设备的级别必须**动态**渲染（§6.2 升降级）")
        self.assertIn('data-danger-level="danger"', js,
                      "删除/上传固件的危险级声明必须在脚本里")
        self.assertGreaterEqual(
            js.count('data-danger-level="danger"'), 2,
            "上传固件与删除固件都是危险级（计数断言，防只改一处）")

    async def test_reboot_device_levels_come_from_firmware_comparison(self):
        """重启设备的级别由固件库版本对比算出（§6.2 / §6.6）。

        判别力：断言调 ``levelOf('reboot_device', …)`` 并把 ``firmwares``
        传进去——只写死 ``data-danger-level="warning"`` 的实现会让「固件库
        放入更高版本后升为危险」这条 AC 无法成立。

        #31：唯一实现在域页脚本里（旧页副本已删）——这条断言同时是「两份
        副本已收成一份」的机械证据。
        """
        devices_js = await self._get_text(
            "/xiaozhi/config/config_domain_page.js")
        self.assertIn("levelOf('reboot_device'", devices_js,
                      "必须用共享模型算重启设备的级别")
        self.assertRegex(devices_js,
                         r"levelOf\('reboot_device',\s*\{[^}]*firmwares",
                         "升级判据必须喂入固件库列表")
        self.assertIn("from './config_danger_model.js'", devices_js,
                      "消费的是共享模型（同一个导出符号）")

    # ── 7. 试连 LLM 归常规（零确认，点名外呼副作用） ─────────────
    async def test_llm_test_is_normal_level_with_no_confirmation(self):
        """试连 LLM 零确认，且它的外呼副作用被点名（§6.2 常规级）。

        判别力：常规级的「零确认」是一个**行为**——按钮的 onclick 直接发请求，
        不经过确认层。断言 testLLM 体内没有 confirmDanger 调用；同时页面文案
        点名「外呼」与「配额」两个副作用。

        #31 迁移：这个按钮**原本只在旧八组页面**（``config_page.html`` 的
        ``testLLM``）。旧页删除会让它彻底消失（方案 §6.2 把它列为常规级，
        属于被期待保留的功能）。本票把它迁进了引擎域——这里的断言也
        从旧页 HTML 改指引擎域，否则「删页导致的净损失」会静默发生。
        """
        # 函数体在域页脚本里（引擎域页共用它）。
        js = await self._get_text("/xiaozhi/config/config_domain_page.js")
        m = re.search(r"async function testLLM\(\)\s*\{(.*?)\n\}", js, re.S)
        self.assertIsNotNone(m, "testLLM 必须存在（#31 从旧页迁到引擎域）")
        body = m.group(1)
        self.assertNotIn("confirmDanger(", body,
                         "试连 LLM 是常规级：零确认（§6.2 明文）")
        self.assertNotIn("confirm(", body, "试连 LLM 不得有原生确认")
        self.assertIn("/xiaozhi/config/api/test-llm", body,
                      "必须调后端 test-llm 路由（它仍保留）")
        # 按钮的**渲染路径**：引擎域页的域表渲染会调 llmTestRow（它造出
        # ``llmTestBtn``）。域页的控件是客户端渲染的，所以按钮字符串在
        # 域页脚本里，不在服务端 HTML 里——断脚本里的渲染函数与调用点，
        # 而不是断 HTML（后者是恒假断言）。
        self.assertIn('id="llmTestBtn"', js, "llmTestRow 必须渲染测试按钮")
        self.assertIn("llmTestRow()", js, "引擎库必须调用 llmTestRow")
        m_slot = re.search(
            r"ENGINE_CATEGORIES\.map\(\(cat\)\s*=>\s*slotRow\(cat\)\s*"
            r"\+\s*\(cat === 'LLM' \? llmTestRow\(\) : ''\)\)", js)
        self.assertIsNotNone(
            m_slot,
            "按钮必须贴在 LLM 那一行之后（贴着被检验的选项）")
        # 副作用点名（外呼 + 配额）在按钮旁的 hint 里（按钮渲染在 js 里）。
        self.assertIn("外呼", js, "必须点名试连 LLM 的外呼副作用")
        self.assertIn("配额", js, "必须点名试连 LLM 消耗少量配额")
        # 零确认还要钉在**行为**上：按钮的 onclick 直连 testLLM，不经过确认层。
        self.assertIn('onclick="testLLM()"', js,
                      "按钮的 onclick 直连（常规级零确认的机械形态）")
        self.assertIn("window.testLLM = testLLM", js,
                      "模块作用域的 testLLM 必须挂到 window，否则内联 onclick 调不到")


    # ── 8. AC3 的数据源：在线设备行必须带 model / version（issue #30） ──
    async def test_devices_payload_carries_model_and_version_keys(self):
        """``api/devices`` 的每个设备对象都带 ``model`` 与 ``version`` 键（AC3）。

        背景（本票修的真 bug）：前端对每台在线设备调
        ``levelOf('reboot_device', {firmwares, device: d})``；``hasNewerFirmware``
        读 ``device.model`` / ``device.version``。接口不返回这两个字段时，
        ``modelOf(d)`` 得空串、库里找不到同型号行，于是重启设备**永远停在
        警示**，AC3「固件库放入更高版本后升为危险」在真实页面上不可达。

        判别力：断言的是**设备对象的结构里有这两个键**（逐键 in dict），
        不是 ``assertIn("model", str(payload))``——后者会被 payload 里
        任何带 ``model`` 字样的字符串（如 ``"device_id"`` 之外的其他字段、
        报错文案、甚至 ``"model"`` 作为别的键名的一部分）意外满足，与
        「这个设备对象真有这两个键」无关。这里钉在结构上：
        取出的值必须是 str（空串也可以），把「只多一个空壳 key」与
        「真的把设备自报值填进去」分开；下面再断言值等于设备自报的那一份。
        """
        class _FakeConn:
            client_ip = "192.168.18.20"

        did = "aa:bb:cc:dd:ee:ff"
        device_registry.register(did, _FakeConn())
        # 设备自报的型号/版本（真实路径：OTA 自检时上报，见下一个用例）。
        device_registry.record_device_info(did, "zhengchen-minicam", "2.4.2")

        payload = await (await self.client.request(
            "GET", "/xiaozhi/config/api/devices")).json()
        row = next(d for d in payload["devices"] if d["device_id"] == did)
        # 键存在（不是靠字串包含蒙混）。
        self.assertIn("model", row, "设备行必须带 model（AC3 的升级判据）")
        self.assertIn("version", row, "设备行必须带 version（AC3 的升级判据）")
        # 值真的是设备自报的那一份（空壳 key 无法满足）。
        self.assertEqual(row["model"], "zhengchen-minicam")
        self.assertEqual(row["version"], "2.4.2")
        self.assertIsInstance(row["model"], str)
        self.assertIsInstance(row["version"], str)

    async def test_devices_payload_model_and_version_are_empty_when_unreported(self):
        """设备没上报过时，字段仍在但为空串（前端保守处理，不许缺 key）。

        判别力：把「取不到」实现成 ``pop``/条件拼字段的实现会让前端读到
        ``undefined``——与空串在 ``hasNewerFirmware`` 里都走「认不出 = 保守
        算有更新」这条，但**缺 key** 会让页面模板与契约测试都变脆。这里
        钉「键总在，值可以为空」。
        """
        class _FakeConn:
            client_ip = "192.168.18.21"

        did = "11:22:33:44:55:66"
        device_registry.register(did, _FakeConn())
        payload = await (await self.client.request(
            "GET", "/xiaozhi/config/api/devices")).json()
        row = next(d for d in payload["devices"] if d["device_id"] == did)
        self.assertIn("model", row)
        self.assertIn("version", row)
        self.assertEqual(row["model"], "")
        self.assertEqual(row["version"], "")

    async def test_ota_self_check_is_the_source_of_model_and_version(self):
        """设备打 OTA 自检时，body 里的 ``board.type`` / ``application.version``
        被记入注册表，并出现在 ``api/devices`` 里（真实数据源链）。

        为什么走 OTA 而不是握手头：固件的 WebSocket 握手头里只有
        ``Protocol-Version`` / ``Device-Id`` / ``Client-Id`` / ``Authorization``
        （见资料包/源码 ``main/protocols/websocket_protocol.cc`` 的 SetHeader），
        **没有**型号与固件版本；hello 报文里的 ``version`` 是协议版本。设备
        唯一上报型号与固件版本的地方就是 OTA 自检的 body。

        判别力：这条是**端到端的数据源链**——真实固件体（与
        ``Board::GetSystemInfoJson`` 同形）+ 真实 OTA handler 路由，断言值
        一路进到设备行。把 ``record_device_info`` 调用删了就变红。
        """
        class _FakeConn:
            client_ip = "192.168.18.22"

        did = "de:ad:be:ef:00:01"
        device_registry.register(did, _FakeConn())
        # 与 Board::GetSystemInfoJson() / GetBoardJson() 同形的固件自检体。
        body = json.dumps({
            "version": 2,
            "mac_address": did,
            "application": {
                "name": "xiaozhi", "version": "2.4.1",
                "compile_time": "2024-01-01T00:00:00Z",
            },
            "board": {"type": "zhengchen-minicam", "name": "MiniCam"},
        })
        resp = await self.client.request(
            "POST", "/xiaozhi/ota/", data=body,
            headers={"device-id": did, "client-id": "uuid-0001",
                     "content-type": "application/json"})
        self.assertEqual(resp.status, 200, "OTA 自检本身要能成功")
        # 数据源被记下。
        info = device_registry.get_device_info(did)
        self.assertEqual(info["model"], "zhengchen-minicam")
        self.assertEqual(info["version"], "2.4.1")
        # 并且它真的到了前端读的那个接口上。
        payload = await (await self.client.request(
            "GET", "/xiaozhi/config/api/devices")).json()
        row = next(d for d in payload["devices"] if d["device_id"] == did)
        self.assertEqual(row["model"], "zhengchen-minicam")
        self.assertEqual(row["version"], "2.4.1")

    async def test_page_reads_model_and_version_from_the_devices_payload(self):
        """页面把设备行的 ``model`` / ``version`` 喂给 ``levelOf``（消费侧）。

        判别力：后端多两个字段而页面没读，等于没修——所以断言要挂在
        **消费侧**（页面脚本对 ``device: d`` 的整对象传递），而不是
        「后端返回了字段」这一半。它与 node 缝里的「同形载荷 → danger」
        合起来才是完整证据链。

        #31：唯一实现在域页脚本里（旧页副本已删）。
        """
        devices_js = await self._get_text(
            "/xiaozhi/config/config_domain_page.js")
        self.assertRegex(
            devices_js, r"levelOf\('reboot_device',\s*\{[^}]*device:\s*d",
            "必须把设备行整对象交给 levelOf（它读 d.model/d.version）")
        # 模型的字段名就是后端契约名（两处必须是同一个词）。
        model = await self._model_js()
        self.assertIn("device.model", model,
                      "模型读的字段名必须是 device.model（与 api/devices 同词）")
        self.assertIn("device.version", model,
                      "模型读的字段名必须是 device.version（与 api/devices 同词）")

    # ── 9. #31 收线的清单：唯一实现 / 共享资产 / 后端接口全部保留 ─────
    #
    # 这三条是 issue #31 下登记的收线清单，逐条机检。它们防的是收线时两类
    # 相反方向的错：**删得不干净**（页面删了，函数剩一份）与**删得过头**
    # （共享资产、后端接口被一并扫走）。
    #
    # 仓库里真正的源文件（不是测试临时工程）：收线的对象是整个仓库，
    # 所以这里直接扫 ``server/config`` 与 ``server/core``。
    _RUNTIME_FUNCTIONS = (
        "rebootDevice", "deleteFirmware", "uploadFirmware",
        "sendSmartConfig", "refreshOta", "restartServer",
    )

    def _repo_page_scripts(self):
        """仓库里**页面脚本**的全部来源（``server/config/*.js``）。

        为什么只扫这一个目录：它是页面脚本的全部居所——``grep`` 全仓的
        ``*.js`` 会扫到 ``server/.venv`` 里的第三方资产（torch / sklearn
        等出货自带的 JS），拿它们的函数名计数只会把断言引入噪声。
        """
        return sorted((SERVER_ROOT / "config").glob("*.js"))

    async def test_six_runtime_functions_have_exactly_one_implementation(self):
        """六个运行时面函数在仓库里**只有一份**（旧页副本已删）。

        判别力：这是收线时最容易静默出错的地方。两种错都靠**计数**钉住：

        - 「删了页面但函数还在」——旧页被删了，却把某些函数的副本留在
          别的文件里（或反过来新域页没接上），行为静默分叉；
        - 「删过头」——新域页的实现被误删，按钮变成一个调不到的函数。

        ``grep -c`` 式的计数断言：每个函数在页面脚本里恰好出现 1 次定义
        （``function name(`` / ``async function name(``）。正则要求函数名
        后紧跟 ``(``，所以 ``rebootDeviceLevel(`` 不会被当成 ``rebootDevice``。
        """
        for fn in self._RUNTIME_FUNCTIONS:
            hits = []
            for js in self._repo_page_scripts():
                count = len(re.findall(rf"(?:async\s+)?function\s+{fn}\s*\(",
                                       js.read_text(encoding="utf-8")))
                hits.extend([js.name] * count)
            self.assertEqual(
                len(hits), 1,
                f"{fn} 在仓库里有 {len(hits)} 份实现 {hits}——"
                "`旧页与其副本必须一并删除，新域页是唯一实现`")

    async def test_old_page_absent_and_new_page_present_in_repo(self):
        """旧页文件不在仓库；新域页脚本与骨架都在（收线的两个方向）。"""
        self.assertFalse((SERVER_ROOT / "config" / "config_page.html").exists(),
                         "旧八组页面 config_page.html 必须已删除（#31）")
        self.assertTrue(
            (SERVER_ROOT / "config" / "config_domain_page.js").exists(),
            "域页脚本必须仍在（唯一实现）")
        self.assertTrue(
            (SERVER_ROOT / "config" / "config_domain_page.html").exists(),
            "域页骨架必须仍在")

    async def test_shared_danger_assets_still_consumed_after_retirement(self):
        """共享资产（分级模型 + 确认层）在旧页退役后**仍被新域页消费**。

        判别力：收线最容易犯的另一个错是「把共享资产当成旧页的一部分扫出去」。
        这条针两个方向：**资产文件还在**，且**新域页真的在用它**。

        资产层：``config_danger_model.js`` 与 ``CONFIRM_*`` 仍可交付；
        消费层：域页脚本 import 分级模型，渲染产物里有确认层容器/脚本。
        只断前者是假绿（文件在但没人用），只断后者也不行。
        """
        # 资产层：文件与服务端常量都在。
        self.assertTrue(
            (SERVER_ROOT / "config" / "config_danger_model.js").exists(),
            "分级模型必须保留（它是域页消费的共享资产，不是旧页的私有物）")
        for name, value in (("CONFIRM_HTML", shell.CONFIRM_HTML),
                            ("SHELL_CONFIRM_JS", shell.SHELL_CONFIRM_JS)):
            self.assertTrue(value.strip(), f"{name} 常量不得被清空（共享资产）")
        self.assertIn(".btn.warn", shell.CONFIRM_CSS)
        # 交付层：分级模型的 HTTP 路由仍可交付且是可用的模块。
        resp = await self.client.request(
            "GET", "/xiaozhi/config/config_danger_model.js")
        self.assertEqual(resp.status, 200)
        self.assertIn("text/javascript", resp.headers["Content-Type"])
        # 消费层：域页脚本 import 它（不是空加载）。
        page_js = await self._get_text(
            "/xiaozhi/config/config_domain_page.js")
        self.assertIn("from './config_danger_model.js'", page_js,
                     "域页必须继续消费分级模型（资产不能被连带删掉）")
        # 消费层：渲染产物里有壳的确认层与分级动作区。
        devices = await self._devices_html()
        self.assertIn(shell.CONFIRM_HTML, devices)
        self.assertIn("data-danger-op=\"restart_server\"", devices)

    async def test_backend_interfaces_survive_the_retirement(self):
        """旧页退役**不得带走任何后端接口**（收线清单第 4 条）。

        判别力：页面退役与后端退役是两件事。新域页在消费这些接口，删它们
        会让页面上的按钮全部报错。这里对**实际注册的路由**断在（不是断
        「config_handler.py 里有这个词」）：每个路由都请求一次，拿到的
        绝不能是 404/405——其中 ``api/restart`` 是最阴的那个（它会真的
        kickstart 服务），所以只断它的路由注册存在，**不发请求**。
        """
        # 读侧接口：真打一次，要的不是 200 而是「路由存在」。
        for path in ("/xiaozhi/config/api/devices",
                     "/xiaozhi/config/api/firmware",
                     "/xiaozhi/config/api/full",
                     "/xiaozhi/config/api/meta"):
            resp = await self.client.request("GET", path)
            self.assertEqual(resp.status, 200, f"{path} 在旧页退役后必须仍在")
        # 写侧接口：只断路由存在——不发真实副作用请求，也不触发 handler
        # 内部的参数校验报错。（``api/restart`` 会 kickstart 服务；
        # ``api/test-llm`` 会外呼；``api/smartconfig`` 会广播；
        # ``api/firmware/upload`` 空体打过去会因 multipart 缺失抛 400。）
        # aiohttp 对「路由在但方法未注册」回 405，对「路由不在」回 404——
        # 所以 ``!= 404`` 就是「路由仍在」的机械判据。
        for path in ("/xiaozhi/config/api/save",
                     "/xiaozhi/config/api/restart",
                     "/xiaozhi/config/api/smartconfig",
                     "/xiaozhi/config/api/test-llm",
                     "/xiaozhi/config/api/local-wifi",
                     "/xiaozhi/config/api/auth",
                     "/xiaozhi/config/api/firmware/upload",
                     "/xiaozhi/config/api/firmware/delete"):
            resp = await self.client.request("OPTIONS", path)
            self.assertNotEqual(resp.status, 404,
                                f"{path} 的路由在旧页退役后必须仍在")

    async def test_retirement_did_not_leave_the_root_rendering_a_page(self):
        """根路径不再渲染任何页面（旧页的机械遗迹）。

        判别力：上面 ``/xiaozhi/config/`` 已注册为 302（本文件其它用例
        也依赖它，否则这里只会拿到 404）。这条把「302 且 Location 指首域」
        钉在同一文件里，免得上面那条路由被改成 200 而没人发现。
        """
        resp = await self.client.request(
            "GET", "/xiaozhi/config/", allow_redirects=False)
        self.assertEqual(resp.status, 302, "根路径必须是 302，不是 200 的旧页")
        self.assertEqual(resp.headers["Location"], "/xiaozhi/config/dialogue/")


if __name__ == "__main__":
    unittest.main()
