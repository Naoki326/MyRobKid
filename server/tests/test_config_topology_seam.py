#!/usr/bin/env python3
"""页面拓扑与共享外壳的 HTTP 契约缝（issue #26 起，#31 收线）。

契约（父 spec §3 / §4 / §8.1 / §8.2，本票 #31 收线后口径）：

1. **域 slug 路由可达**（§8.1）——``dialogue`` / ``engine`` / ``tools`` /
   ``devices`` / ``system`` + 逃生口 ``raw``，每个都是**用户契约**（ADR-0012）。
   五域全部上线；占位页机制保留但当前是空集（新增域时照用）。
2. **尾斜杠规范形 + 应用层 301**（§8.2）——无斜杠访问 301 到有斜杠形态，
   由应用层发，不依赖 nginx（nginx 配置在仓库外，读不出来）。
   根路径 ``/xiaozhi/config/`` 是 **302** 到首域——它不是规范形，只是旧入口
   兑底，所以与域页尾斜杠的 301 不是同一个机制（§4.1）。
3. **动作区只在编辑页渲染**（§4.2）——保存 / 重启服务按钮在逃生口只读页上
   **不渲染**（「不渲染」不是「渲染了再藏」）。
4. **五域 + 逃生口都在侧栏**（§3）——逃生口是横线之下的非域小入口。
5. **28 个字段一条不少**（§7）——对话与角色 15 + 系统 13；同一份归属表
   （``config/page_domains.py``）既喂渲染又喂计数，所以「页面渲染出几个字段」
   与被测的期望值不是同一个来源之外的东西。
6. **旧八组页面已退役**（#31 contract 步）——文件不在仓库、无代码路径渲染，
   根路径 302 而不是 200。

为什么不测 DOM 呈现行为：父 spec 明确不引入浏览器测试基建。折叠、确认层、
hash 滚动按人工走查口径（issue #26 明文）；本文件只钉 HTTP 契约与交付内容。

运行：server/.venv/bin/python -m unittest discover -s server/tests -t server
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urljoin

import yaml
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from core.api.config_handler import ConfigHandler  # noqa: E402
from config import config_shell as shell  # noqa: E402
from config import page_domains as domains  # noqa: E402

#: 五域 slug——用户契约，改一个就是破坏书签（ADR-0012）。这里**写死**而不是
#: 从 config_shell 读回来：写死的这份才是「契约被改动」时唯一会红的东西。
DOMAIN_SLUGS = ["dialogue", "engine", "tools", "devices", "system"]
ESCAPE_SLUG = "raw"

#: 本票已交付内容的域（#29 之后五域全部上线，没有占位页了）。
#: #26 交付 dialogue / system，#27 把 engine 从占位转正（450 字段全量可达），
#: #28 把 tools 从占位转正（32 字段），#29 把 devices 转正（17 字段 + 运行时面）。
IMPLEMENTED_SLUGS = ["dialogue", "engine", "tools", "devices", "system"]
#: 占位页从此只属于「未来新增的域」——五域齐备后它是空集。
PLACEHOLDER_SLUGS = []


class DomainTopologyContract(AioHTTPTestCase):
    """路由（可达 / 301 / 占位）+ 页面交付内容。"""

    async def get_application(self):
        self._tmp = tempfile.TemporaryDirectory()
        project = Path(self._tmp.name)
        (project / "data").mkdir()
        _write_yaml(project / "config.yaml", {
            "server": {"ip": "0.0.0.0", "port": 8002, "http_port": 8003},
            # 系统域的日志字段：本机真实配置里 log 段与 data/.config.yaml 无关，
            # 用基础 config.yaml 提供（合并语义与真机一致）。
            "log": {"log_level": "INFO", "log_dir": "tmp", "log_file": "server.log",
                    "log_format": "{message}", "log_format_file": "{message}",
                    "data_dir": "data"},
            "delete_audio": True,
            "close_connection_no_voice_time": 120,
            "enable_websocket_ping": False,
            # 对话与角色域的字段（含一个数组、一个模板占位符密钥、一个布尔）。
            "prompt": "你是小智",
            "prompt_template": "agent-base-prompt.txt",
            "system_error_response": "稍后再试",
            "end_prompt": {"enable": True, "prompt": "再见"},
            "wakeup_words": ["你好小智"],
            "exit_commands": ["退出"],
            "enable_greeting": True,
            "enable_stop_tts_notify": False,
            "stop_tts_notify_voice": "config/assets/tts_notify.mp3",
            "enable_wakeup_words_response_cache": True,
            "voiceprint": {"url": None, "speakers": ["test1,张三,程序员"],
                           "similarity_threshold": 0.4},
            "context_providers": [{"url": "", "headers": {"Authorization": "Bearer SECRET"}}],
        })
        _write_yaml(project / "data" / ".config.yaml", {})
        # 页面/静态资源用**真实文件**：路由的交付路径（含 MIME）必须被测到，
        # 存根只能证明「路由存在」，证不了「页面真的能加载」。
        (project / "config").mkdir()
        for name in ("config_state_model.js",
                     "config_domain_page.html", "config_domain_page.js",
                     "config_danger_model.js"):
            (project / "config" / name).write_text(
                (SERVER_ROOT / "config" / name).read_text(encoding="utf-8"),
                encoding="utf-8")

        self.handler = ConfigHandler({}, str(project))
        app = web.Application()
        app.add_routes([
            web.get("/xiaozhi/config", self.handler.handle_config_root_redirect),
            web.get("/xiaozhi/config/", self.handler.handle_config_root_default),
            web.get("/xiaozhi/config/{slug}", self.handler.handle_domain_redirect),
            web.get("/xiaozhi/config/{slug}/", self.handler.handle_domain_page),
            web.get("/xiaozhi/config/config_state_model.js",
                    self.handler.handle_state_model),
            web.get("/xiaozhi/config/config_domain_page.js",
                    self.handler.handle_domain_page_script),
            web.get("/xiaozhi/config/config_danger_model.js",
                    self.handler.handle_danger_model),
            web.get("/xiaozhi/config/api/full", self.handler.handle_full),
        ])
        return app

    async def asyncTearDown(self):
        self._tmp.cleanup()
        await super().asyncTearDown()

    async def _get(self, path, allow_redirects=False):
        return await self.client.request("GET", path, allow_redirects=allow_redirects)

    async def _get_text(self, path):
        """取一份 200 响应的正文（路由可达也一并钉住）。"""
        resp = await self._get(path)
        self.assertEqual(resp.status, 200, f"{path} 必须可达")
        return await resp.text()

    # ── 1. 域 slug 路由可达（未上线域为占位页） ──────────────────
    async def test_every_domain_slug_is_reachable(self):
        for slug in DOMAIN_SLUGS + [ESCAPE_SLUG]:
            resp = await self._get(f"/xiaozhi/config/{slug}/")
            self.assertEqual(resp.status, 200,
                             f"域 slug {slug} 必须可达（slug 是用户契约）")
            self.assertIn("text/html", resp.headers["Content-Type"])

    async def test_unlaunched_domains_render_a_placeholder_not_a_404(self):
        """未上线域是**占位页**，不是 404 §11 Phase 1。

        判别力：三域的 slug 今天就上线（书签从此固定），内容是后续票的事。
        404 会让「地址先上线」这件事做不到——用户点进去看到的是断链。

        #29 之后五域齐备（`PLACEHOLDER_SLUGS` 为空集），本用例只剩「占位机制
        本身还在」这条底线：占位页的渲染路径不得随「没有占位域了」被删掉，
        否则下一个新增域上线时又得从 404 开始。
        """
        for slug in PLACEHOLDER_SLUGS:
            resp = await self._get(f"/xiaozhi/config/{slug}/")
            self.assertEqual(resp.status, 200)
            html = await resp.text()
            self.assertIn("尚未上线", html, f"{slug} 占位页必须如实说未上线")
            # 未上线域的占位页没有可保存对象 → 动作区不渲染（§4.2）。
            self.assertNotIn("xzhSave()", html)
            self.assertNotIn("saveBtn", html)
        # 五域齐备：没有任何一个域是占位页，侧栏也不该挂「未上线」标注。
        html = await (await self._get("/xiaozhi/config/dialogue/")).text()
        self.assertEqual(html.count('<span class="pending-tag">未上线</span>'), 0,
                         "五域全部上线，占位标注必须一个不剩")
        self.assertTrue(callable(getattr(shell, "render_placeholder", None)),
                        "占位页渲染器仍在（新增域时照用，不是已被删掉的死码）")

    # ── 2. 尾斜杠：应用层 301，不依赖 nginx ──────────────────────
    async def test_root_without_slash_redirects_to_canonical_slash_form(self):
        resp = await self._get("/xiaozhi/config")
        self.assertEqual(resp.status, 301)
        self.assertTrue(resp.headers["Location"].endswith("/xiaozhi/config/"),
                        "301 的目标必须是尾斜杠规范形（§8.2）")

    async def test_domain_without_slash_redirects_permanently(self):
        for slug in DOMAIN_SLUGS + [ESCAPE_SLUG]:
            resp = await self._get(f"/xiaozhi/config/{slug}")
            self.assertEqual(resp.status, 301, f"/{slug} 必须 301（不是 302/404）")
            self.assertTrue(
                resp.headers["Location"].endswith(f"/xiaozhi/config/{slug}/"),
                f"/{slug} 301 的目标必须是它的尾斜杠规范形")

    async def test_slash_form_is_served_directly_not_redirected(self):
        """规范形自己不许再跳一次（否则每个书签都要多走一个来回）。"""
        resp = await self._get("/xiaozhi/config/dialogue/")
        self.assertEqual(resp.status, 200)

    # ── 3. 逃生口：一页看完整份 YAML，动作区不渲染 ───────────────
    async def test_escape_page_shows_the_whole_config_and_has_no_actions(self):
        await self._get("/xiaozhi/config/api/full")  # 只为确认接口可用（同 app）
        resp = await self._get("/xiaozhi/config/raw/")
        self.assertEqual(resp.status, 200)
        html = await resp.text()
        # 「一页看完整份 YAML」：以只读原文容器承载整棵配置树。
        self.assertIn('id="rawView"', html)
        self.assertIn("原始配置", html)
        # 只读页无可保存对象 → 顶栏动作区不渲染（§4.2 的验收措辞是「不渲染」）。
        self.assertNotIn("xzhSave()", html)
        self.assertNotIn("xzhRestart()", html)

    async def test_escape_entry_is_a_non_domain_small_link_below_a_rule(self):
        """逃生口在侧栏**横线之下的非域小入口**（§3/§4.3）。"""
        resp = await self._get("/xiaozhi/config/dialogue/")
        html = await resp.text()
        sidebar = html[html.index('<aside class="sidebar"'):html.index("</aside>")]
        self.assertIn('class="escape-sep"', sidebar,
                      "底部横线（非域分界）必须在侧栏里")
        self.assertIn("escape-item", sidebar, "逃生口要用非域的小入口样式")
        # 横线必须出现在逃生口链接**之前**，且逃生口在五域之后。
        self.assertLess(sidebar.index('class="escape-sep"'),
                        sidebar.index("escape-item"))
        # 逃生口不在五域序列里（它不是第六域）：侧栏的**域条目**恰为 5 个。
        self.assertEqual(sidebar.count('data-domain="'), len(DOMAIN_SLUGS) + 1,
                         "侧栏 = 5 域 + 1 个逃生口入口")
        for slug in DOMAIN_SLUGS:
            self.assertIn(f'data-domain="{slug}"', sidebar)
        self.assertIn('data-domain="raw"', sidebar)

    # ── 4. 侧栏：五域 + 未上线标注 ───────────────────────────────
    async def test_sidebar_links_all_five_domains_on_every_page(self):
        for slug in DOMAIN_SLUGS + [ESCAPE_SLUG]:
            html = await (await self._get(f"/xiaozhi/config/{slug}/")).text()
            for target in DOMAIN_SLUGS:
                self.assertIn(f'href="/xiaozhi/config/{target}/"', html,
                              f"{slug} 页的侧栏缺 {target} 的入口")
            self.assertIn(f'href="/xiaozhi/config/{ESCAPE_SLUG}/"', html)

    async def test_sidebar_marks_only_the_still_unlaunched_domains(self):
        html = await (await self._get("/xiaozhi/config/dialogue/")).text()
        # 占位标注的样式仍在壳里（新增域时照用）。断样式定义，不断「未上线」
        # 这个字符串本身——壳里到处都是它，断它等于没断。
        self.assertIn(".pending-tag{", html,
                      "占位标注的样式仍在壳里（新增域时照用）")
        # 标注只在侧栏里出现，且只在**仍未上线**的域条目上——多一个少一个都是谎。
        # #27 之后 engine 已上线、#28 tools、#29 devices：五域一个都没有了。
        self.assertEqual(html.count('<span class="pending-tag">未上线</span>'),
                         len(PLACEHOLDER_SLUGS))
        self.assertNotIn('data-domain="engine"><span class="ic">🤖</span>'
                         '<span class="lbl">引擎</span><span class="pending-tag">',
                         html, "引擎域已上线，侧栏不得再挂「未上线」标注")

    # ── 5. 28 个字段（对话与角色 15 + 系统 13） ──────────────────
    #
    # 域页的控件是**客户端**按服务端注入的域表渲染的（config_domain_page.js），
    # 所以「字段是否真的渲染出来」在 HTTP 响应里查不到 data-path。契约能钉住的
    # 是另一半、也是同样会漏人的那一半：**渲染的依据（域表）确实到了浏览器**，
    # 而且内容与 §7 表逐行相等。域表漏一行 = 页面少一个控件，且脏计数也会跟着少。
    def _schema(self, html):
        marker = '<script id="domain-schema" type="application/json">'
        start = html.index(marker) + len(marker)
        end = html.index("</script>", start)
        return json.loads(html[start:end])

    async def test_dialogue_page_carries_all_fifteen_fields(self):
        html = await (await self._get("/xiaozhi/config/dialogue/")).text()
        schema = self._schema(html)
        paths = [f["path"] for g in schema["groups"] for f in g["fields"]]
        self.assertEqual(paths, [f.path for f in domains.DIALOGUE.all_fields()],
                         "注入的域表必须与 page_domains.py 逐行相等")
        self.assertEqual(len(paths), 15, "§7 对话与角色表 15 个字段（list 记 1）")
        layers = {f["path"]: f["layer"] for g in schema["groups"] for f in g["fields"]}
        self.assertEqual(sum(1 for v in layers.values() if v == "common"), 11, "常用 11")
        self.assertEqual(sum(1 for v in layers.values() if v == "more"), 4, "更多设置 4")

    async def test_system_page_carries_all_thirteen_fields_flat(self):
        html = await (await self._get("/xiaozhi/config/system/")).text()
        schema = self._schema(html)
        paths = [f["path"] for g in schema["groups"] for f in g["fields"]]
        self.assertEqual(paths, [f.path for f in domains.SYSTEM.all_fields()])
        self.assertEqual(len(paths), 13, "§7 系统表 13 个字段")
        # §2.5：域内常用层为空时**折叠区不渲染，全部字段平铺**。
        self.assertEqual(len(domains.SYSTEM.common_fields()), 0,
                         "系统域常用层为空（这是平铺的依据）")
        self.assertTrue(all(f["layer"] == "more"
                            for g in schema["groups"] for f in g["fields"]))
        # 折叠的开合判据是**域内常用层是否为空**，不是「这个分组有没有 more
        # 字段」。区分不开的话系统域（13 个字段全是 more）会被折成一个空壳——
        # 页面上一篇空白，字段一个也看不见（本票现场踩到过）。
        js = await (await self._get("/xiaozhi/config/config_domain_page.js")).text()
        self.assertIn("domainHasCommon", js,
                      "平铺判据必须是域内常用层为空，不是分组内 more 非空（§2.5）")
        self.assertIn("const fold = domainHasCommon && more.length > 0", js,
                      "折叠只在「域内有常用层且本组有更多设置」时生成")

    async def test_domain_pages_expose_hash_anchors_for_their_groups(self):
        """hash 深链 ``#<分组id>``（§4.5）：分组 id 是 ASCII kebab-case。"""
        for schema_def in (domains.DIALOGUE, domains.SYSTEM):
            html = await (await self._get(f"/xiaozhi/config/{schema_def.slug}/")).text()
            schema = self._schema(html)
            ids = [g["id"] for g in schema["groups"]]
            self.assertEqual(ids, [g.id for g in schema_def.groups])
            for gid in ids:
                self.assertRegex(gid, r"^[a-z0-9-]+$",
                                 "分组 id 只用 ASCII，深链里不许出现中文")
            # 分组 id 真的被用成 DOM 锚点（域页脚本按 id 建 section）。
            js = await (await self._get("/xiaozhi/config/config_domain_page.js")).text()
            self.assertIn('id="${esc(g.id)}"', js, "分组 id 必须成为 DOM 锚点")

    # ── 6. 编辑页有动作区、有脏守卫 ──────────────────────────────
    async def test_editing_pages_render_the_action_area_and_dirty_guard(self):
        for slug in IMPLEMENTED_SLUGS:
            html = await (await self._get(f"/xiaozhi/config/{slug}/")).text()
            self.assertIn("saveBtn", html, f"{slug} 是编辑页，必须有保存按钮")
            self.assertIn("重启服务", html)
            # 跨页脏标记与离开确认的钩子（§4.6）：页面把脏计数挂给壳。
            self.assertIn("__xzhDirtyGuard", html)
            self.assertIn("config-dirty/", html, "脏摘要的 localStorage 键前缀")

    async def test_shell_guards_refresh_and_cross_page_switch(self):
        """壳脚本必须挂 beforeunload（刷新/关页兜底）与跨域切换确认（§4.6）。"""
        html = await (await self._get("/xiaozhi/config/dialogue/")).text()
        self.assertIn("beforeunload", html)
        self.assertIn("data-domain", html)
        self.assertIn("config-dirty/", html)

    async def test_domain_page_script_is_served_as_javascript(self):
        resp = await self._get("/xiaozhi/config/config_domain_page.js")
        self.assertEqual(resp.status, 200)
        self.assertIn("javascript", resp.headers["Content-Type"])

    async def test_domain_page_script_computes_dirty_through_the_state_model(self):
        """域页的脏计算必须复用状态模型，不是自己在页面里另写一套（§9 单一事实源）。"""
        js = await (await self._get("/xiaozhi/config/config_domain_page.js")).text()
        for fn in ("computeDirty", "initialState", "dirtySummary", "domainDirty"):
            self.assertIn(fn, js, f"域页脚本没有用状态模型的 {fn}")

    async def test_domain_page_script_does_not_reimplement_path_helpers(self):
        """点号路径的 get/set 也必须用状态模型那一份。

        判别力：页面自己写一份 ``getPath`` 看上去无害（行数少、直观），但
        服务端的 ``config_state`` 信号与脏计算都建立在同一套点号语义上。
        两边分叉后，``context_providers.0.headers.Authorization`` 会一边
        解得开、一边解不开，密钥三态就会静默回落成猜测。
        """
        js = await (await self._get("/xiaozhi/config/config_domain_page.js")).text()
        self.assertRegex(js, r"import\s*\{[^}]*\bgetPath\b",
                         "getPath 必须从状态模型 import")
        self.assertRegex(js, r"import\s*\{[^}]*\bsetPath\b",
                         "setPath 必须从状态模型 import")
        self.assertNotRegex(js, r"(?m)^\s*function\s+getPath\s*\(",
                            "页面不得另写一份 getPath（两把尺子迟早分叉）")
        self.assertNotRegex(js, r"(?m)^\s*function\s+setPath\s*\(",
                            "页面不得另写一份 setPath")

    async def test_domain_page_loads_the_state_model_as_a_module(self):
        html = await (await self._get("/xiaozhi/config/dialogue/")).text()
        # 路径必须**绝对**：规范 URL 是目录形式（/xiaozhi/config/<slug>/），
        # ``./config_domain_page.js`` 会被浏览器解析成 <slug>/config_domain_page.js,
        # 多一级 slug 段 → 404 → 页面一直停在「正在加载配置…」。
        self.assertIn('src="/xiaozhi/config/config_domain_page.js"', html)
        self.assertNotIn('src="./config_domain_page.js"', html)

    async def test_domain_page_module_path_resolves_against_the_canonical_url(self):
        """模块路径必须在**规范 URL**（目录形式）下解得回自己。

        这是浏览器真会走的算术：``/xiaozhi/config/<slug>/`` + 相对路径
        ``./x.js`` = ``/xiaozhi/config/<slug>/x.js``。写对了写得好看不重要，
        解得回才重要——解不回就是页面永远停在「正在加载配置…」。
        这里用 ``urljoin`` 亲手算一遍，不靠读代码看出来的。

        域页现在引入**两个**模块（页面脚本 + 危险分级模型，#30）；两者都要求
        解回 ``/xiaozhi/config/`` 下的绝对路径并取得到——而**页面脚本那条**
        必须就是 ``config_domain_page.js``（它是域页的编辑逻辑，不许被换掉）。
        """
        canonical = "/xiaozhi/config/dialogue/"
        html = await (await self._get(canonical)).text()
        srcs = re.findall(r'<script[^>]*\ssrc="([^"]+)"', html)
        self.assertTrue(srcs, "域页必须引入模块脚本")
        self.assertIn("/xiaozhi/config/config_domain_page.js", srcs,
                      "域页的页面脚本必须是 config_domain_page.js")
        for src in srcs:
            resolved = urljoin(canonical, src)
            self.assertTrue(
                resolved.startswith("/xiaozhi/config/") and "/../" not in resolved,
                f"模块路径 {src} 在规范 URL 下解成了 {resolved}")
            resp = await self._get(resolved)
            self.assertEqual(resp.status, 200, f"解出来的路径 {resolved} 取不到")

    async def test_page_is_served_with_its_template_comment_stripped(self):
        """作者注释不得随响应发出（它会污染「页面里有没有某字符串」这类判断）。"""
        html = await (await self._get("/xiaozhi/config/dialogue/")).text()
        self.assertNotIn("服务端不做模板渲染", html,
                         "骨架里的作者注释不该随响应发给浏览器")

    # ── 7. 根路径 302 到首域；旧八组页面已退役（#31 contract 步） ────
    async def test_root_302s_to_the_first_domain(self):
        """``/xiaozhi/config/`` 是 **302** 到 ``dialogue``（§4.1），不是 200、也不是 301。

        方案 §4.1 原文：「``/xiaozhi/config/`` **302 到首域**（对话与角色居首）
        ——旧链接/书签/手输不 404，nginx 与 8003 直连两条路都覆盖（302 由
        应用层发，不依赖 nginx）」。#8.1 又把 ``dialogue`` 标为「首域，
        ``/xiaozhi/config/`` 302 到这里」。

        判别力：

        - **302 不是 301**：根路径不是规范形（域页 URL 才是），301 会把
          「对话与角色」当成根路径的永久身份缓存下来。这跟域页尾斜杠的 301
          是两回事（那条有规范形身份，§8.2），断言分开写。
        - **不是 200**：收线前旧八组页面在这里返回 200；本票要它不再
          200——这是「旧页面真的退役了」的机械证据。
        """
        resp = await self._get("/xiaozhi/config/")
        self.assertEqual(resp.status, 302,
                         "根路径必须 302（旧入口兑底，不是规范形，也不是 200 的旧页）")
        self.assertEqual(resp.headers["Location"], "/xiaozhi/config/dialogue/",
                         "302 的目标必须是首域 dialogue 的规范形（§8.1）")
        # （不再单独断“不是 301”：上面那行 ``== 302`` 已蕴含它。一个状态码
        #   只有一个值，写成两条只是把同一个事实说两遍。）

    async def test_root_redirect_preserves_the_query_string(self):
        """跳转保留查询串（旧书签上挂的 hash 之外的参数不能丢）。"""
        resp = await self._get("/xiaozhi/config/?a=1&b=2")
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.headers["Location"],
                         "/xiaozhi/config/dialogue/?a=1&b=2")

    async def test_legacy_config_page_is_gone_from_the_repo(self):
        """旧八组页面不能再回来：文件不在仓库，也没有代码路径渲染它。

        判别力：旧页退役是**不可逆**的一步。这条钉两件事：

        1. 文件 ``server/config/config_page.html`` **不存在**——留着它
            就是留一份会在下一次修改时静默分叉的副本；
        2. 没有任何**代码**还引用它。

        第 2 条的判别力在于「代码」而不是「文本」：文档字符串与注释里提
        这个文件名是**历史记录**（比如 handler 的模块 docstring 说「旧页已
        在 #31 退役」），它们不是渲染路径。所以这里用 ``ast`` 剥掉注释与
        文档字符串，只看真正的代码节点里有没有那个文件名。
        若是断文本，就只能在「写清历史」与「测试变绿」之间二选一——那是
        一根恒真的反向断言（断测试自己不许提旧文件名），不是契约。
        """
        self.assertFalse(
            (SERVER_ROOT / "config" / "config_page.html").exists(),
            "旧八组页面必须已从仓库删除（#31 收线）")
        import ast
        for py in sorted((SERVER_ROOT / "core").rglob("*.py")) \
                + sorted((SERVER_ROOT / "config").rglob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            # 模块/类/函数 docstring 是历史记录的合法位置，不能当渲染路径。
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef, ast.AsyncFunctionDef)):
                    doc = ast.get_docstring(node, clean=False)
                    if doc is not None:
                        docstrings.add(doc)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value in docstrings:
                        continue
                    self.assertNotIn(
                        "config_page.html", node.value,
                        f"{py} 的代码仍引用已删除的旧页文件名")

    async def test_root_redirect_target_is_the_sidebar_first_domain(self):
        """302 的目标与侧栏首域必须是**同一个域**（§3 五域序列 + §4.1）。

        判别力：302 目标写成硬编码字符串、侧栏顺序又被挪动时，用户点
        ``/xiaozhi/config/`` 会落到一个**不是侧栏第一项**的域上——“首域”
        这个说法自己就不成立了。这里从侧栏 HTML 读出真正的第一项再对。
        """
        html = await (await self._get("/xiaozhi/config/dialogue/")).text()
        sidebar = html[html.index('<aside class="sidebar"'):html.index("</aside>")]
        first = re.search(r'data-domain="([a-z0-9-]+)"', sidebar)
        self.assertIsNotNone(first, "侧栏必须有域条目")
        resp = await self._get("/xiaozhi/config/")
        self.assertEqual(resp.headers["Location"],
                         f"/xiaozhi/config/{first.group(1)}/",
                         "302 目标必须就是侧栏的首个域（§4.1「对话与角色居首」）")
        self.assertEqual(first.group(1), "dialogue",
                         "侧栏首域就是 dialogue（§3 五域序列写死的）")

    # ── 8. 验收判据 1：零字段丢失（527 = 15+450+32+17+13；常用 347 / 更多 180）──
    async def test_zero_field_loss_cross_domain_tally(self):
        """五域字段总账：527 = 15+450+32+17+13；常用 347 / 更多 180（§3 / §7 / §10.1）。

        判别力：各域自己的用例（dialogue/engine/tools/devices/system 的字段数
        断言）已分别存在，但它们**合不起来**——某天一个域多 2 个、另一个域
        少 2 个，五个用例各自全绿而总数已经错了。这条是把五个域当一本账对：
        逐域写死数字（改一个就红）+ 总数对账（§10.1 的 527 与归层计数）。

        这是**旧页退役后最重要的一条**：旧页曾渲染过这 527 个字段中的一部分，
        删页时如果少了什么，“各域原来就对”的直觉会掩盖它。数字写死的意义就在
        这里——它们是用 #16 的规则机械算出来的旧页对照口径，不是从当前代码
        里反推出来的（反推的断言是自证式的）。
        """
        expected = {  # §3 合计表：域 → (字段数, 常用, 更多设置)
            "dialogue": (15, 11, 4),
            "engine": (450, 302, 148),
            "tools": (32, 27, 5),
            "devices": (17, 7, 10),
            "system": (13, 0, 13),
        }
        total = common = more = 0
        for slug, (n, c, m) in expected.items():
            schema = domains.DOMAIN_SCHEMAS[slug]
            fields = schema.all_fields()
            self.assertEqual(len(fields), n, f"{slug} 域字段数对不上 §3")
            self.assertEqual(len(schema.common_fields()), c,
                             f"{slug} 常用层数对不上 §3")
            self.assertEqual(len(fields) - len(schema.common_fields()), m,
                             f"{slug} 更多设置数对不上 §3")
            # 归层只能用两个值：多一个第三值就是「两层」约定被破了。
            self.assertLessEqual(
                {f.layer for f in fields}, {"common", "more"},
                f"{slug} 域的字段只许落在 common / more 两层")
            total += n
            common += c
            more += m
        self.assertEqual(total, 527, "§10.1：527 = 15+450+32+17+13")
        self.assertEqual(common, 347, "§3 合计表：常用 347")
        self.assertEqual(more, 180, "§3 合计表：更多设置 180")
        # 五域就是全部——不存在第六个域页承载漏掉的字段。
        self.assertEqual(set(domains.DOMAIN_SCHEMAS), set(expected),
                         "域集合必须就是这五个（逃生口不是域，不载字段）")

    async def test_tally_is_zero_loss_not_just_balanced(self):
        """527 不只是「合计相等」：域间不重不漏（同一路径不得出现在两个域）。

        判别力：「每个域字段数对」与「字段没重复也没遗漏」是两件事：
        从 A 域挪 3 个到 B 域，两个域的数都可能还是对的。零字段丢失的完整
        口径是**每个字段路径恰出现在一个域**，且域内路径无重复。
        """
        seen = {}
        for slug, schema in domains.DOMAIN_SCHEMAS.items():
            paths = [f.path for f in schema.all_fields()]
            self.assertEqual(len(paths), len(set(paths)),
                             f"{slug} 域内有重复字段路径（渲染会多出一个控件）")
            for path in paths:
                self.assertNotIn(
                    path, seen,
                    f"{path} 同时出现在 {seen.get(path)} 与 {slug} 两个域（重复计数）")
                seen[path] = slug
        self.assertEqual(len(seen), 527, "五域合起来恰是 527 个唯一字段路径")

    # ── 9. 验收判据 4：一级 = 6 ≤ 7、深度 ≤ 3（呈现行为的结构前提）────
    async def test_top_level_navigation_is_six_entries_within_seven(self):
        """侧栏一级 = 5 域 + 逃生口 = **6 ≤ 7**（§3 / §10.3）。

        判别力：这是**可用服务端渲染的侧栏 HTML 直接断言**的一条（不需要
        浏览器）：侧栏里的导航项（``.navitem``）恰好 6 个——五个是域、
        一个是逃生口，逃生口不在五域序列里（它不是第六域，而是一级导航的
        最后一项）。数字写死：多一个（比如把摄像头页也塞进来）就红。
        """
        html = await (await self._get("/xiaozhi/config/dialogue/")).text()
        sidebar = html[html.index('<aside class="sidebar"'):html.index("</aside>")]
        entries = re.findall(r'class="navitem[^"]*"', sidebar)
        self.assertEqual(len(entries), 6,
                         "一级导航 = 5 域 + 1 逃生口 = 6 ≤ 7（§10.3）")
        # （不再单独断 ``<= 7``：上行已把它钉成恰当的 6。）
        # 逃生口是**非域**样式（``escape-item``），不是第六个域。
        self.assertEqual(len(re.findall(r'class="navitem escape-item', sidebar)), 1)
        self.assertEqual(len(re.findall(r'data-domain="', sidebar)), 6)
        # 摄像头页**不占一级导航**（§4.4）：侧栏里不得有它的入口。
        self.assertNotIn('data-domain="camera"', sidebar)

    async def test_depth_is_at_most_three_with_folded_dom_at_zero(self):
        """深度 ≤ 3，且折叠态 DOM 字段数 = 0 的**实现前提**在交付内容里。

        判别力：折叠、点击深度是呈现行为，父 spec 不引入浏览器基建——所以
        这里钉的是它的**代码形状**（都是可静态判定的）：

        - 域内两层是 ``<details>``（第一层是侧栏、第二层是卡片/类目、第三层
          是折叠容器 = 侧栏(1) → 卡片(2) → 卡内折叠(3)）；
        - 引擎/插件卡体**延迟到 toggle 才构建**（``data-built="0"`` +
          ``fillEngineBody``），所以折叠态下 DOM 里真的没有字段控件；
        - 深度不靠嵌套更多 ``<details>`` 实现（域内只有两层）。

        真正的「几次点击」是**人工走查项**（见报告）——这里只钉它的前提。
        """
        js = await self._get_text("/xiaozhi/config/config_domain_page.js")
        # 卡体延迟构建（折叠态 DOM 字段数 = 0 的实现前提）。
        self.assertIn('data-built="0"', js)
        self.assertIn("fillEngineBody", js)
        # 域内「更多设置」是原生 details 折叠（不是第三个域、也不是分页）。
        self.assertIn('<details class="more-settings">', js)
        # 域内只有两层：常用平铺 + 更多设置折叠——不得再套一层。
        self.assertNotIn('<details class="more-settings"><details', js)
        # 逃生口是只读页（无二级/三级）——它不参与深度计算。
        raw = await self._get_text("/xiaozhi/config/raw/")
        self.assertNotIn("data-engine", raw)

    # ── 10. 验收判据 2：盲测十题的**结构前提**（点击次数本身是人工走查项）──
    #
    # 十道题（§10.2）的「≤2 次点击」需要浏览器才能量。不引入浏览器基建的
    # 前提下，能机械化钉住的是**它的结构前提**：每个目标字段落在哪个域的哪一层，
    # 以及那一层是不是被折叠包着（包着 = 多一次点击）。
    #
    # 下面逐题断言「域 + 层」的组合。断言里**不拼装 HTML**（那是恒真的温床）；
    # 读的是与渲染同源的域表（``page_domains``）。
    _BLIND_TEST_TARGETS = [
        # (题号, 域, 路径, 允许的最大点击数) —— 1 = 域首屏平铺可达；2 = 需展开一层折叠
        (1, "dialogue", "wakeup_words", 1),
        (2, "engine", "selected_module.TTS", 1),
        (5, "dialogue", "prompt", 1),
        (9, "dialogue", "voiceprint.speakers", 1),
        (10, "system", "log.log_level", 1),
        (7, "devices", "server.auth_key", 2),
        (4, "engine", "tts_timeout", 2),
        (8, "tools", "Intent.function_call.functions", 1),
    ]

    async def test_blind_test_structural_preconditions(self):
        """盲测十题的结构前提：每个目标字段的域 + 层都对（§10.2）。

        判别力：每题断 **域归属**（错域 = 用户会去错的页）与 **层**（错层 = 多
        一次点击）。折叠层归 2 次点击的前提是「它真的被 ``<details>`` 包着」
        ——域内 ``more`` 层就是包着的（见 ``groupCard``）；引擎全局参数的
        ``more`` 也一样（它在外层 ``details.asdetails`` 里）。

        未列入的事项（盲测 3/6：看设备在线、给设备配 Wi-Fi）不是**字段**，而是
        运行时面面板——它们由 ``DEVICES_RUNTIME`` 声明（下面单独断言）。
        """
        for num, slug, path, max_clicks in self._BLIND_TEST_TARGETS:
            schema = domains.DOMAIN_SCHEMAS[slug]
            hit = next((f for g in schema.groups for f in g.fields
                       if f.path == path), None)
            self.assertIsNotNone(
                hit, f"盲测第 {num} 题的目标 {path} 必须落在 {slug} 域（§10.2）")
            # 是否真的多一次点击，取决于**该域有没有常用层**（§2.5）：
            # 域内常用层为空时折叠区不渲染，全部字段平铺——此时 ``more`` 层
            # 的字段也是首屏可达的（系统域就是这样）。只有「域内有常用层」
            # 时，``more`` 层才真的被 ``<details>`` 包着（= 多一次点击）。
            domain_has_common = len(schema.common_fields()) > 0
            expected_clicks = 2 if (hit.layer == "more" and domain_has_common) else 1
            self.assertEqual(
                expected_clicks, max_clicks,
                f"盲测第 {num} 题（{path}）在 {slug} 域的 {hit.layer} 层，"
                f"域内有常用层={domain_has_common} → {expected_clicks} 次点击，"
                f"但 §10.2 写的是 {max_clicks} 次")
        # 运行时面两题（3 看设备在线 / 6 配 Wi-Fi）：面板直接挂在设备域，
        # 域页首屏渲染（不折叠）= 1 次点击。
        panel_ids = [p["id"] for p in domains.DEVICES_RUNTIME]
        self.assertIn("online-devices", panel_ids, "盲测 3：在线设备区在设备域")
        self.assertIn("smartconfig", panel_ids, "盲测 6：配网入口在设备域")
        # 运行时面面板是首屏渲染（不折进 details）——渲染路径直接拼它们。
        # 断**调用点**而不是函数名存在：只留定义不调用，用户在页面上看不到面板。
        js = await self._get_text("/xiaozhi/config/config_domain_page.js")
        self.assertIn("+ runtimePanelsHtml()", js,
                      "设备域渲染必须真的拼上运行时面面板（光有定义不算）")
        self.assertIn("renderDevicesDomain()", js,
                      "设备域渲染路径必须被调用（光有定义不算）")

    # 注：曾有一条 ``test_blind_test_field_order_matches_the_ten_questions``，
    # 逐域重算「字段数 / 常用 / 更多」的同一张 §3 表。它与上面的
    # ``test_zero_field_loss_cross_domain_tally`` 是同一断言的第二份副本
    # （对同一份 ``DOMAIN_SCHEMAS`` 做同样的算术），已删除——重复的算术不会
    # 多发现一件事，只会在改动时多一个要同步的地方。


def _write_yaml(path, data):
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
