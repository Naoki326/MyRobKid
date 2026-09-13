#!/usr/bin/env python3
"""设备域 + 摄像头归位的 HTTP 契约缝（issue #29，父 spec §4.4 / §7 / §8.1 / §8.2）。

契约（父 spec 设备域表 + §4.4 + §8.2，逐条落实）：

1. **17 字段全量可达**（§7 设备表）——provisioning 载荷 5 + 设备认证 3 +
   hello 协商 7 + 节奏与时区 2 = 17；常用 7 / 更多设置 10（§3 合计表）。
   同一份归属表（`config/page_domains.py`）既喂渲染又喂计数。
2. **危险视觉占位**（§7 移交注记 4）——`server.auth_key` 在设备·更多设置内
   带危险标注。三级危险分级与统一确认层是 #30 的事，本票只要**占位**：
   字段自己声明危险、页面把声明渲染出来，分级规则不许在页面里长第二份。
3. **设备协商值标注**（§7 移交注记 3）——`xiaozhi.type / version / transport /
   audio_params.format` 标「设备协商值，通常勿改」，否则用户会以为该改它们。
4. **运行时面集中在本域一页内**（AC 2）——在线设备 / 固件库 / SmartConfig
   三个运行时面板与 17 个配置字段同页；面板 id 是 §4.5 定的深链锚点
   （`#online-devices` / `#firmware` / `#smartconfig`）。
5. **摄像头归位**（§4.4 / §8.1 / §8.2）——摄像头页与设备域**同壳互链**、
   保留独立 URL `/xiaozhi/camera/` 作书签（挂机监控），**设备域内常驻入口**
   （设备离线也在，它不依赖在线设备列表）。无斜杠形态由应用层 **301** 归一，
   取消 `/xiaozhi/camera` 的双注册；`api/*` 子路由**不**301 化。

为什么不测 DOM 呈现行为：父 spec 明确**不引入浏览器测试基建**（上个审查员
为此卡了三小时）。面板的布局、确认框的样式按人工走查口径；本文件只钉 HTTP 契约
与交付内容（域表 + 面板声明 + 页面骨架 + 路由）。

运行：server/.venv/bin/python -m unittest discover -s server/tests -t server
"""
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
from core.api.camera_handler import CameraHandler  # noqa: E402
from config import config_shell as shell  # noqa: E402
from config import page_domains as domains  # noqa: E402

#: §7 设备表的机械复算结果——写死在这里，是「表被改动」时唯一会红的东西。
#: 17 = provisioning 5 + 认证 3 + hello 协商 7 + 节奏与时区 2；常用 7 / 更多 10。
DEVICES_FIELD_TOTAL = 17
DEVICES_COMMON_TOTAL = 7
DEVICES_MORE_TOTAL = 10

#: §7 设备表逐行的字段路径（顺序照表，`[i]` 形态照表写——页面入口会归一成
#: 数组路径，表本身是计数口径）。写死这一份才有判别力：改错一个路径、
#: 把 xiaozhi.* 漏进对话域、或让 auth_key 掉出更多设置，都只在这里红。
DEVICES_TABLE = [
    "server.websocket",
    "server.timezone_offset",
    "server.auth.enabled",
    "server.auth.allowed_devices[i]",
    "server.mqtt_gateway",
    "server.mqtt_signature_key",
    "server.udp_gateway",
    "server.websocket_backup",
    "server.auth_key",
    "tts_audio_send_delay",
    "xiaozhi.type",
    "xiaozhi.version",
    "xiaozhi.transport",
    "xiaozhi.audio_params.format",
    "xiaozhi.audio_params.sample_rate",
    "xiaozhi.audio_params.channels",
    "xiaozhi.audio_params.frame_duration",
]

#: §7 表备注列要求标注「设备协商值，通常勿改」的四条（移交注记 3）。
NEGOTIATED_FIELDS = [
    "xiaozhi.type", "xiaozhi.version", "xiaozhi.transport",
    "xiaozhi.audio_params.format",
]

#: §4.5 定案的设备域深链锚点（运行时面板 id）。
RUNTIME_PANEL_IDS = ["online-devices", "firmware", "smartconfig"]


def _write_yaml(path, data):
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


class DevicesDomainContract(AioHTTPTestCase):
    """设备域页的交付内容：17 字段 / 危险占位 / 协商值标注 / 运行时面 / 摄像头。"""

    async def get_application(self):
        self._tmp = tempfile.TemporaryDirectory()
        project = Path(self._tmp.name)
        (project / "data").mkdir()
        (project / "data" / "bin").mkdir()
        (project / "config").mkdir()
        real = SERVER_ROOT / "config"
        for name in ("config_domain_page.html", "config_domain_page.js",
                     "config_state_model.js", "config_page.html"):
            (project / "config" / name).write_text(
                (real / name).read_text(encoding="utf-8"), encoding="utf-8")

        _write_yaml(project / "config.yaml", {
            "server": {
                "ip": "0.0.0.0", "port": 8002, "http_port": 8003,
                "websocket": "wss://api.tenclass.net/xiaozhi/v1/",
                "websocket_backup": "wss://backup.example.com/xiaozhi/v1/",
                "timezone_offset": 8,
                "auth_key": "server-auth-key-0000",
                "auth": {"enabled": True, "allowed_devices": ["aa:bb:cc:dd:ee:ff"]},
                "mqtt_gateway": None, "mqtt_signature_key": None,
                "udp_gateway": None,
            },
            "tts_audio_send_delay": 0,
            "xiaozhi": {
                "type": "websocket",
                "version": 1,
                "transport": "websocket",
                "audio_params": {
                    "format": "opus", "sample_rate": 24000,
                    "channels": 1, "frame_duration": 60,
                },
            },
        })
        _write_yaml(project / "data" / ".config.yaml", {
            "server": {"websocket": "ws://192.168.18.11:8000/xiaozhi/v1/"},
        })

        self.handler = ConfigHandler({}, str(project))
        self.camera = CameraHandler({})
        app = web.Application()
        app.add_routes([
            web.get("/xiaozhi/config/{slug}", self.handler.handle_domain_redirect),
            web.get("/xiaozhi/config/{slug}/", self.handler.handle_domain_page),
            web.get("/xiaozhi/config/config_domain_page.js",
                    self.handler.handle_domain_page_script),
            web.get("/xiaozhi/config/config_state_model.js",
                    self.handler.handle_state_model),
            web.get("/xiaozhi/config/api/full", self.handler.handle_full),
            web.post("/xiaozhi/config/api/save", self.handler.handle_save),
            web.get("/xiaozhi/config/api/devices", self.handler.handle_devices),
            web.get("/xiaozhi/config/api/firmware",
                    self.handler.handle_firmware_list),
            web.get("/xiaozhi/config/api/local-wifi",
                    self.handler.handle_local_wifi),
            web.post("/xiaozhi/config/api/smartconfig",
                     self.handler.handle_smartconfig),
            # 摄像头：页面路由的 301 与规范形**由应用层发**（§8.2），
            # api/* 子路由保持原名、不 301 化。
            web.get("/xiaozhi/camera", self.handler.handle_camera_redirect),
            web.get("/xiaozhi/camera/", self.handler.handle_camera_page),
            web.get("/xiaozhi/camera/api/device-info",
                    self.camera.handle_device_info),
        ])
        return app

    async def asyncTearDown(self):
        self._tmp.cleanup()
        await super().asyncTearDown()

    async def _get(self, path, allow_redirects=False):
        return await self.client.request("GET", path, allow_redirects=allow_redirects)

    async def _html(self, slug="devices"):
        resp = await self._get(f"/xiaozhi/config/{slug}/")
        self.assertEqual(resp.status, 200)
        return await resp.text()

    async def _js(self):
        return await (await self._get(
            "/xiaozhi/config/config_domain_page.js")).text()

    def _schema(self, html):
        marker = '<script id="domain-schema" type="application/json">'
        start = html.index(marker) + len(marker)
        end = html.index("</script>", start)
        return json.loads(html[start:end])

    # ── 1. 17 字段全量可达，逐行对账 ─────────────────────────────
    async def test_devices_page_carries_all_seventeen_fields(self):
        """§7 设备表 17 行一条不少、不多、不重复；常用 7 / 更多设置 10（§3）。

        判别力：只测总数 17 发现不了「xiaozhi.transport 溜进对话域、
        tts_audio_send_delay 被漏掉」这类互换——它们总数刚好抵消，
        而现场表现是「该改的找不到、不该动的出现在别处」。所以此处对**集合**。

        顺序**不**按表：§7 表的行序是旧页归属的遗留顺序，而域内分组
        （provisioning / 认证 / hello / 节奏）是本方案按职责重切的（§2.5）。
        能要求的是**组内顺序**：必须与该组字段在§7 表里的相对次序一致
        （下面单独钉），否则「同一份表两种排法」就没人管了。
        """
        schema = self._schema(await self._html())
        paths = [f["path"] for g in schema["groups"] for f in g["fields"]]
        self.assertEqual(len(paths), DEVICES_FIELD_TOTAL)
        self.assertEqual(sorted(paths), sorted(DEVICES_TABLE),
                         "§7 设备表 17 行必须逐行落位（集合相等）")
        self.assertEqual(len(set(paths)), len(paths), "字段路径不得重复")
        layers = [f["layer"] for g in schema["groups"] for f in g["fields"]]
        self.assertEqual(layers.count("common"), DEVICES_COMMON_TOTAL, "常用 7")
        self.assertEqual(layers.count("more"), DEVICES_MORE_TOTAL, "更多设置 10")
        # 注入的域表必须与 page_domains.py 逐行相等：注入漏一行 = 页面少一个
        # 控件，且脏计数也少一个（同一份表既喂渲染又喂计数）。
        self.assertEqual(paths, [f.path for f in domains.DEVICES.all_fields()])

    async def test_each_group_keeps_the_table_order_of_its_fields(self):
        """组内顺序照 §7 表——分组可以重切（职责），组内次序不行（表是口径）。"""
        schema = self._schema(await self._html())
        table_pos = {p: i for i, p in enumerate(DEVICES_TABLE)}
        for g in schema["groups"]:
            seq = [table_pos[f["path"]] for f in g["fields"]]
            self.assertEqual(seq, sorted(seq),
                             f"分组 {g['id']} 的字段次序必须与 §7 表一致")

    async def test_devices_domain_is_registered_and_launched(self):
        """`devices` 从占位页**转正**：域表进 DOMAIN_SCHEMAS、侧栏摘掉「未上线」。

        判别力：只把 DOMAIN_SCHEMAS 加上而忘了 shell 的 implemented 标记，
        页面内容是对的、侧栏却仍标「未上线」——用户看到的是自相矛盾的两处。
        """
        self.assertIn("devices", domains.DOMAIN_SCHEMAS)
        devices = [d for d in shell.DOMAINS if d["slug"] == "devices"]
        self.assertEqual(len(devices), 1, "devices 必须是五域之一（slug 用户契约）")
        self.assertTrue(devices[0]["implemented"],
                        "设备域本票上线，implemented 必须为真")
        html = await self._html()
        self.assertEqual(html.count('<span class="pending-tag">未上线</span>'), 0,
                         "五个域本票全部上线，侧栏不该再有「未上线」标注")

    async def test_devices_page_is_an_editing_page(self):
        """设备域是编辑页：有保存 / 重启动作区与脏守卫（§4.2）。"""
        html = await self._html()
        self.assertIn("saveBtn", html)
        self.assertIn("xzhRestart()", html)
        self.assertIn("__xzhDirtyGuard", html)

    # ── 2. 危险视觉占位（分级归 #30） ────────────────────────────
    async def test_auth_key_carries_a_danger_marker_and_stays_in_more_settings(self):
        """`server.auth_key`：更多设置 + 危险标注（§7 移交注记 4 / AC 1）。

        「改 = 全部设备 token 失效」这句是**危险视觉**的内容，不是分级规则；
        三级分级与统一确认层是 #30。这里钉两件事：它自己在域表里声明了危险，
        且它真的落在更多设置里（现场最容易被顺手提到常用层）。
        """
        schema = self._schema(await self._html())
        field = next(f for g in schema["groups"] for f in g["fields"]
                     if f["path"] == "server.auth_key")
        self.assertEqual(field["layer"], "more", "auth_key 属更多设置（§7 表）")
        self.assertTrue(field.get("danger"), "auth_key 必须带危险标记（#30 会消费它）")
        # 危险标记的单一事实源在 page_domains.py：页面的危险渲染必须读它。
        js = await self._js()
        self.assertRegex(js, r"f\.danger",
                         "危险渲染必须由域表的 danger 标记驱动（不许页面硬编码路径）")
        # 密钥判定的既有单一事实源也必须覆盖它（值绝不出现在页面数据里）。
        full = await (await self.client.request(
            "GET", "/xiaozhi/config/api/full")).json()
        self.assertIn("server.auth_key", full["config_state"])
        self.assertEqual(set(full["config_state"]["server.auth_key"]), {"configured"})

    # ── 3. 设备协商值标注 ────────────────────────────────────────
    async def test_hello_negotiated_fields_say_do_not_change(self):
        """§7 移交注记 3：四条 hello 协商值标「设备协商值，通常勿改」。"""
        schema = self._schema(await self._html())
        hints = {f["path"]: f["hint"] for g in schema["groups"] for f in g["fields"]}
        for path in NEGOTIATED_FIELDS:
            self.assertIn("设备协商值", hints.get(path, ""),
                          f"{path} 必须标「设备协商值，通常勿改」（否则用户以为该改它）")
        # 它们都在更多设置——「通常勿改」与「默认折叠」是同一件事的两面。
        layers = {f["path"]: f["layer"]
                  for g in schema["groups"] for f in g["fields"]}
        for path in NEGOTIATED_FIELDS:
            self.assertEqual(layers[path], "more", f"{path} 属更多设置")

    async def test_provisioning_fields_are_in_the_common_layer(self):
        """provisioning 载荷（§7 备注列）在**常用**层：它们是发给设备的连接载荷。

        判别力：把它们当「高级参数」折进更多设置，会让「设备连不上」这类
        现场第一件事（核对服务器地址）多一层点击。
        """
        schema = self._schema(await self._html())
        layers = {f["path"]: f["layer"]
                  for g in schema["groups"] for f in g["fields"]}
        for path in ("server.websocket", "server.mqtt_gateway",
                     "server.mqtt_signature_key", "server.udp_gateway",
                     "server.websocket_backup"):
            self.assertEqual(layers[path], "common", f"{path} 是 provisioning 载荷")

    # ── 4. 运行时面集中在本域一页内 ──────────────────────────────
    async def test_runtime_panels_are_declared_with_the_spec_hash_ids(self):
        """在线设备 / 固件 / SmartConfig 的面板声明随域页注入（AC 2 + §4.5）。

        判别力：面板 id 是 §4.5 定的深链锚点（`#online-devices` 等）。页面里
        自由起名会让深链悄悄失效；单一事实源必须在这里。
        """
        schema = self._schema(await self._html())
        panels = schema["runtime_panels"]
        self.assertEqual([p["id"] for p in panels], RUNTIME_PANEL_IDS,
                         "运行时面板 id 必须照 §4.5（深链锚点）")
        self.assertEqual(panels, [dict(p) for p in domains.DEVICES_RUNTIME],
                         "面板声明必须逐条来自 page_domains.py（单一事实源）")
        for panel in panels:
            self.assertRegex(panel["id"], r"^[a-z0-9-]+$", "id 只用 ASCII（深链）")
            self.assertTrue(panel["title"] and panel["desc"])

    async def test_runtime_panels_are_rendered_by_the_shared_domain_page(self):
        """三个面板由域页脚本渲染，且**调用既有的设备/固件 API**（不重写后端）。

        判别力：本票的边界是「把旧页面的运行时面迁进设备域」。若页面改用新的
        API 路径，后端就多出一份影子实现——AC 2 要的是「一页内可用」，
        不是「新写一套设备管理」。
        """
        js = await self._js()
        self.assertIn("runtime_panels", js, "面板由注入的域表驱动渲染")
        for api_path in ("/xiaozhi/config/api/devices",
                         "/xiaozhi/config/api/firmware",
                         "/xiaozhi/config/api/firmware/upload",
                         "/xiaozhi/config/api/firmware/delete",
                         "/xiaozhi/ota/reboot",
                         "/xiaozhi/config/api/smartconfig",
                         "/xiaozhi/config/api/local-wifi"):
            self.assertIn(api_path, js, f"运行时面必须调用既有接口 {api_path}")
        # 危险动作的确认机制已在 #30 改为统一页内确认层（§6.5），原生
        # ``window.confirm`` 不得再出现。
        self.assertNotIn("window.confirm(", js,
                         "危险操作不得用原生 confirm（#30 改为页内确认层）")
        self.assertIn("confirmDanger(", js,
                      "重启/上传/删除必须走统一页内确认层")

    async def test_the_runtime_endpoints_behind_the_panels_are_alive(self):
        """面板背后的接口真能用（不是「页面里调了个 404」）。"""
        dev = await (await self.client.request(
            "GET", "/xiaozhi/config/api/devices")).json()
        self.assertTrue(dev["ok"])
        self.assertIn("devices", dev, "在线设备列表的形状被页面消费")
        fw = await (await self.client.request(
            "GET", "/xiaozhi/config/api/firmware")).json()
        self.assertTrue(fw["ok"])
        self.assertIn("firmwares", fw)

    # ── 5. 摄像头归位（§4.4 / §8.2） ─────────────────────────────
    async def test_camera_page_keeps_its_own_url_and_wears_the_shared_shell(self):
        """摄像头页**保留独立 URL**（书签挂机监控）+ 同壳（§4.4）。

        判别力：把摄像头页并进设备域页（或做成设备域的一个 tab）会毁掉
        「挂机监控的书签」——那条 URL 必须自己活得下去，而且它得在壳里，
        否则用户在摄像头页上找不到回设备域的路。
        """
        resp = await self._get("/xiaozhi/camera/")
        self.assertEqual(resp.status, 200)
        self.assertIn("text/html", resp.headers["Content-Type"])
        html = await resp.text()
        # 同壳：五域侧栏 + 逃生口 + 壳脚本（跨页脏点/离开确认）都在。
        self.assertIn('<aside class="sidebar"', html)
        for slug in ("dialogue", "engine", "tools", "devices", "system", "raw"):
            self.assertIn(f'href="/xiaozhi/config/{slug}/"', html,
                          f"摄像头页的侧栏缺 {slug} 的入口（它必须在同一副壳里）")
        self.assertIn("config-dirty/", html, "壳脚本必须随页到位（跨页脏点）")
        # 无编辑对象 → 动作区不渲染（§4.2）。
        self.assertNotIn("xzhSave()", html)
        self.assertNotIn("saveBtn", html)
        # 快照 / 设备定位的调用点还在（页面能力没被搬家弄丢）。
        self.assertIn("/xiaozhi/camera/api/device-info", html)

    async def test_camera_page_and_devices_domain_link_both_ways(self):
        """同壳互链（§4.4）：设备域 → 摄像头、摄像头 → 设备，两处都在。"""
        camera_html = await (await self._get("/xiaozhi/camera/")).text()
        devices_html = await self._html()
        self.assertIn(f'href="{shell.CAMERA_PAGE["path"]}"', devices_html,
                      "设备域必须有摄像头入口（AC：常驻入口）")
        self.assertIn(f'href="{shell.page_url("devices")}"', camera_html,
                      "摄像头页必须有回设备域的路（互链是双向的）")
        # 入口文案的单一事实源在壳里：页面不许自己写一份。
        self.assertIn(shell.CAMERA_PAGE["label"], devices_html)
        # 「同壳」的证据也是同一段代码：两页的顶栏/侧栏由同一份壳生成。
        self.assertEqual(
            shell.render_sidebar("devices") in devices_html, True)
        self.assertIn("📟", camera_html, "摄像头页的顶栏家族链要标出设备域图标")

    async def test_camera_entry_is_permanent_not_a_runtime_row(self):
        """常驻入口**不依赖设备在线**（AC 4）：它由壳渲染，不在在线设备列表里。

        判别力：把入口做成「在线设备行旁的快捷方式」时，设备一离线入口就消失
        ——这是最自然也最错的一版（issue 明文：设备离线也在）。
        """
        html = await self._html()
        # 入口在**顶栏**（壳渲染，与在线设备无关），不在客户端填充的容器里。
        topbar = html[html.index('<div class="topbar">'):html.index('<div class="layout">')]
        self.assertIn(shell.CAMERA_PAGE["path"], topbar,
                      "摄像头入口必须在壳的顶栏（常驻），不在运行时列表里")
        self.assertIn("data-family-link", topbar,
                      "家族互链要有可辨识的锚点（同壳互链的实现落点）")

    async def test_camera_without_slash_redirects_permanently_to_canonical(self):
        """§8.2：`/xiaozhi/camera` 应用层 301 到 `/xiaozhi/camera/`，取消双注册。

        判别力：现状是**双注册**（两条路由各自返回同一份 body），于是同一
        命名空间里 `camera` 与 `camera/` 并存、行为取决于从 8080 还是 8003 进。
        「取消双注册」的证据不是「301 存在」——而是规范形**只由一条路由**提供，
        且无斜杠那条**不再返回页面 body**。
        """
        resp = await self._get("/xiaozhi/camera")
        self.assertEqual(resp.status, 301, "无斜杠必须 301（不是 302 / 直出页面）")
        self.assertTrue(resp.headers["Location"].endswith("/xiaozhi/camera/"),
                        "301 的目标必须是尾斜杠规范形")
        # 无斜杠形态不得再直接返回页面（双注册的旧行为）。
        self.assertLess(len(await resp.text()), 2000,
                        "301 响应不该是整页 HTML（那说明页面又被双注册了）")

    async def test_camera_api_subroutes_are_not_redirected(self):
        """§8.2 的 301 **只针对页面路由**：`api/*` 子路由不改名、不 301。

        判别力：把「归一化」写成一个通配路由是很容易的顺手一笔，而设备
        `api/device-info` 一旦 301，浏览器 fetch 会跟着跳、快照代理与设备
        定位就多一跳（并且 nginx 之外还有设备固件直接调这些路径）。
        """
        # 摄像头页的 path 是页面 URL，不含 api 子路径（这不是判据，但写在这里
        # 当契约锁）；真正的判据是下面那条 HTTP 实测：api 子路由直出 200。
        self.assertTrue(shell.CAMERA_PAGE["path"].endswith("/"),
                        "摄像头页规范形必须尾斜杠收尾")
        self.assertNotIn("api", shell.CAMERA_PAGE["path"],
                         "页面 URL 不该带 api 段（api 是子路由，不跟着 301）")
        resp = await self._get("/xiaozhi/camera/api/device-info")
        self.assertEqual(resp.status, 200, "api 子路由必须直出，不得 301")
        body = await resp.json()
        self.assertIn("host", body)

    # ── 6. 单一事实源 ────────────────────────────────────────────
    async def test_camera_entry_text_is_declared_once(self):
        """摄像头入口的**文案与路径**只有一处声明（壳），页面不写字面量。

        判别力：文案在壳、路径在页面、标题在 handler 是「单一事实源」最常见的
        破法——三处分叉后，改一侧就是死链或错别字，而且没有测试会红。
        """
        self.assertEqual(shell.CAMERA_PAGE["path"], "/xiaozhi/camera/",
                         "路径是 §8.1 定的规范形，写死在这里是契约")
        js = await self._js()
        self.assertNotIn("/xiaozhi/camera/", js,
                         "域名页脚本不得硬编码摄像头 URL（由壳注入）")
        html = await self._html()
        self.assertIn(shell.CAMERA_PAGE["path"], html)

    async def test_devices_schema_json_carries_the_camera_entry_and_panels(self):
        """域表注入必须带上摄像头入口与运行时面板（页面只有一个数据来源）。

        断的是**消费侧证据**，不是「注入值 == 源值」：后者在页面根本不读
        ``SCHEMA.camera`` 时也会通过（把源引用原样抄一遍就绿了）。这里要求
        页面脚本真的从 ``SCHEMA`` 取该字段，且渲染出的 HTML 真有那个链接。
        """
        js = await (await self._get(
            "/xiaozhi/config/config_domain_page.js")).text()
        html = await self._html()
        schema = self._schema(html)
        self.assertIn("SCHEMA.camera", js,
                      "页面必须从注入的域表读摄像头入口（单一数据来源）")
        self.assertRegex(js, r"SCHEMA\.runtime_panels",
                         "页面必须从注入的域表读运行时面板")
        self.assertEqual([p["id"] for p in schema["runtime_panels"]],
                         RUNTIME_PANEL_IDS)
        # 渲染出的链接真的指向摄像头页（不是只把值注进去就完事）。
        self.assertIn(f'href="{shell.CAMERA_PAGE["path"]}"', html,
                      "设备域要真的渲染出摄像头入口链接")
        # 别的域页也带上同一份壳级事实（单一事实源，各页不必各写一份）。
        for slug in ("dialogue", "engine", "tools", "system"):
            other = self._schema(await self._html(slug))
            self.assertEqual(other["camera"]["path"], shell.CAMERA_PAGE["path"])


if __name__ == "__main__":
    unittest.main()
