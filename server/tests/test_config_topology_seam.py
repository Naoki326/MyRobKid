#!/usr/bin/env python3
"""页面拓扑与共享外壳的 HTTP 契约缝（issue #26，#24 Phase 1）。

契约（父 spec §3 / §4 / §8.1 / §8.2）：

1. **域 slug 路由可达**（§8.1）——``dialogue`` / ``engine`` / ``tools`` /
   ``devices`` / ``system`` + 逃生口 ``raw``，每个都是**用户契约**（ADR-0012）。
   未上线的三域必须给**占位页**：URL 先上线、内容后上线，而不是 404。
2. **尾斜杠规范形 + 应用层 301**（§8.2）——无斜杠访问 301 到有斜杠形态，
   由应用层发，不依赖 nginx（nginx 配置在仓库外，读不出来）。
3. **动作区只在编辑页渲染**（§4.2）——保存 / 重启服务按钮在逃生口只读页与
   占位页上**不渲染**（「不渲染」不是「渲染了再藏」）。
4. **五域 + 逃生口都在侧栏**（§3）——逃生口是横线之下的非域小入口，
   未上线域条目带占位标注而不是被删掉。
5. **28 个字段一条不少**（§7）——对话与角色 15 + 系统 13；同一份归属表
   （``config/page_domains.py``）既喂渲染又喂计数，所以「页面渲染出几个字段」
   与被测的期望值不是同一个来源之外的东西。

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

#: 本票交付内容的两个域（其余三域是占位页，#27/#28/#29 交付内容）。
IMPLEMENTED_SLUGS = ["dialogue", "system"]
PLACEHOLDER_SLUGS = ["engine", "tools", "devices"]


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
        for name in ("config_page.html", "config_state_model.js",
                     "config_domain_page.html", "config_domain_page.js"):
            (project / "config" / name).write_text(
                (SERVER_ROOT / "config" / name).read_text(encoding="utf-8"),
                encoding="utf-8")

        self.handler = ConfigHandler({}, str(project))
        app = web.Application()
        app.add_routes([
            web.get("/xiaozhi/config", self.handler.handle_config_root_redirect),
            web.get("/xiaozhi/config/", self.handler.handle_page),
            web.get("/xiaozhi/config/{slug}", self.handler.handle_domain_redirect),
            web.get("/xiaozhi/config/{slug}/", self.handler.handle_domain_page),
            web.get("/xiaozhi/config/config_state_model.js",
                    self.handler.handle_state_model),
            web.get("/xiaozhi/config/config_domain_page.js",
                    self.handler.handle_domain_page_script),
            web.get("/xiaozhi/config/api/full", self.handler.handle_full),
        ])
        return app

    async def asyncTearDown(self):
        self._tmp.cleanup()
        await super().asyncTearDown()

    async def _get(self, path, allow_redirects=False):
        return await self.client.request("GET", path, allow_redirects=allow_redirects)

    # ── 1. 域 slug 路由可达（未上线域为占位页） ──────────────────
    async def test_every_domain_slug_is_reachable(self):
        for slug in DOMAIN_SLUGS + [ESCAPE_SLUG]:
            resp = await self._get(f"/xiaozhi/config/{slug}/")
            self.assertEqual(resp.status, 200,
                             f"域 slug {slug} 必须可达（slug 是用户契约）")
            self.assertIn("text/html", resp.headers["Content-Type"])

    async def test_unlaunched_domains_render_a_placeholder_not_a_404(self):
        """未上线域是**占位页**，不是 404 §11 Phase 1。

        判别力：三域的 slug 今天就上线（书签从此固定），内容是 #27/#28/#29 的
        事。404 会让「地址先上线」这件事做不到——用户点进去看到的是断链。
        """
        for slug in PLACEHOLDER_SLUGS:
            resp = await self._get(f"/xiaozhi/config/{slug}/")
            self.assertEqual(resp.status, 200)
            html = await resp.text()
            self.assertIn("尚未上线", html, f"{slug} 占位页必须如实说未上线")
            # 未上线域的占位页没有可保存对象 → 动作区不渲染（§4.2）。
            self.assertNotIn("xzhSave()", html)
            self.assertNotIn("saveBtn", html)

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

    async def test_unlaunched_domains_are_marked_in_the_sidebar(self):
        html = await (await self._get("/xiaozhi/config/dialogue/")).text()
        self.assertIn("未上线", html)
        # 标签只在侧栏里出现，恰好在三个未上线域的条目上——多一个少一个都是谎。
        self.assertEqual(html.count('<span class="pending-tag">未上线</span>'),
                         len(PLACEHOLDER_SLUGS))

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
        """
        canonical = "/xiaozhi/config/dialogue/"
        html = await (await self._get(canonical)).text()
        srcs = re.findall(r'<script[^>]*\ssrc="([^"]+)"', html)
        self.assertTrue(srcs, "域页必须引入模块脚本")
        for src in srcs:
            resolved = urljoin(canonical, src)
            self.assertTrue(
                resolved.endswith("/xiaozhi/config/config_domain_page.js"),
                f"模块路径 {src} 在规范 URL 下解成了 {resolved}（应当是 "
                "/xiaozhi/config/config_domain_page.js）")
            resp = await self._get(resolved)
            self.assertEqual(resp.status, 200, f"解出来的路径 {resolved} 取不到")

    async def test_page_is_served_with_its_template_comment_stripped(self):
        """作者注释不得随响应发出（它会污染「页面里有没有某字符串」这类判断）。"""
        html = await (await self._get("/xiaozhi/config/dialogue/")).text()
        self.assertNotIn("服务端不做模板渲染", html,
                         "骨架里的作者注释不该随响应发给浏览器")

    # ── 7. 旧八组页面原样保留在原 URL（expand 阶段，收线是后续票） ─
    async def test_legacy_config_page_still_served_at_its_own_url(self):
        resp = await self._get("/xiaozhi/config/")
        self.assertEqual(resp.status, 200,
                         "旧配置页仍以 /xiaozhi/config/ 服务（本票不做收线）")
        html = await resp.text()
        self.assertIn('id="saveBar"', html, "旧页面原样保留：保存栏还在")


def _write_yaml(path, data):
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
