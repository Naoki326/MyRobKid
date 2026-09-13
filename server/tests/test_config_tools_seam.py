#!/usr/bin/env python3
"""插件与工具域的 HTTP 契约缝（issue #28，父 spec §5.1 / §7 / §9）。

契约（父 spec §7 表 + 移交注记，逐条落实）：

1. **32 字段全量可达**（§7 插件与工具表）——3 条散字段（`tool_call_timeout` /
   `mcp_endpoint` / `selected_module.Intent`）+ 7 个插件卡（3+4+2+3+3+4+4 = 23）
   + 3 条意图分支卡（2+1+3 = 6）= 32；常用 27 / 更多设置 5（§3 合计表）。
   同一份归属表（`config/page_domains.py`）既喂渲染又喂计数。
2. **意图双清单同时可见**（§7 移交注记 1）——`Intent.function_call.functions` 与
   `Intent.intent_llm.functions` 各在自己的分支卡里，切换 `selected_module.Intent`
   不会再让清单消失。这不是「两个清单都在表里」——它们必须落在**两张不同的卡**
   上，否则「切换后清单消失」只是换了个方式复现。
3. **插件库与引擎库同构**（§5.1 / §7 移交注记 2）——搜索框、真折叠（折叠态
   DOM 字段数 = 0，按 `toggle` 才构建体）、`<details>` 卡片。同构的证据是
   **同一份实现**：两库共用一个库渲染函数与一个字段清单函数，不是两段像样的代码。
4. **单一事实源**（§9 / CONTEXT.md）——意图分支清单只有一处
   （`page_domains.INTENT_BRANCHES`），页面不写第二份；域表注入时必须带上它。
5. **脏分组的组件范围**（§5.3 / §5.4）——插件与意图分支是「可选中/可启用组件」，
   它们的三段路径不得被当成「未选中引擎的字段」。这是本票最易踩的一处：
   `plugins.get_weather.api_key` 与 `Intent.function_call.functions` 都是三段。

为什么不测 DOM 呈现行为：父 spec 明确**不引入浏览器测试基建**。折叠、搜索命中按
人工走查口径；本文件只钉 HTTP 契约与交付内容（域表 + 脚本）。

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
from config import page_domains as domains  # noqa: E402

#: §7 插件与工具表的机械复算结果——写死在这里，是「表被改动」时唯一会红的东西。
#: 3 条散字段 + 23 个插件字段 + 6 个意图分支字段 = 32；常用 27 / 更多设置 5。
TOOLS_FIELD_TOTAL = 32
TOOLS_COMMON_TOTAL = 27
TOOLS_MORE_TOTAL = 5

#: 七个插件卡与 §7 表里的字段数（`<插件名>: 字段数`）。
PLUGIN_FIELDS = {
    "get_weather": 3,
    "get_news_from_chinanews": 4,
    "get_news_from_newsnow": 2,
    "home_assistant": 3,
    "play_music": 3,
    "search_from_ragflow": 4,
    "web_search": 4,
}

#: 三条意图分支卡（§7 表的三块）+ 各自的字段数。
INTENT_BRANCH_FIELDS = {
    "function_call": 2,
    "nointent": 1,
    "intent_llm": 3,
}

#: 本机 `data/.config.yaml` 只启用了 get_weather；模板里七个插件都在。
#: 「未启用插件的参数不再折进 `<details>` 黑洞」（AC）的正例就在那六个上。


def _write_yaml(path, data):
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


def _spec_block(js: str, name: str) -> str:
    """截出 ``const <name> = { ... };`` 这段字面量（供静态契约断言）。"""
    start = js.index(f"const {name} = {{")
    end = js.index("\n};", start)
    return js[start:end]


class ToolsDomainContract(AioHTTPTestCase):
    """工具域页的交付内容：32 字段 / 双清单 / 同构库 / 单一事实源。"""

    async def get_application(self):
        self._tmp = tempfile.TemporaryDirectory()
        project = Path(self._tmp.name)
        (project / "data").mkdir()
        (project / "config").mkdir()
        real = SERVER_ROOT / "config"
        # #31 收线：旧八组页面（config_page.html）已删除，不再拷它。
        for name in ("config_domain_page.html", "config_domain_page.js",
                     "config_state_model.js"):
            (project / "config" / name).write_text(
                (real / name).read_text(encoding="utf-8"), encoding="utf-8")

        _write_yaml(project / "config.yaml", {
            "server": {"ip": "0.0.0.0", "port": 8002},
            "tool_call_timeout": 30,
            "mcp_endpoint": "你的接入点 websocket地址",
            "selected_module": {
                "VAD": "SileroVAD", "ASR": "FunASR", "LLM": "ChatGLMLLM",
                "VLLM": "ChatGLMVLLM", "TTS": "EdgeTTS", "Memory": "nomem",
                "Intent": "function_call",
            },
            # 模板里七个插件都在（树上都在 = 库能列出全部七张卡）。
            "plugins": {
                "get_weather": {
                    "api_host": "mj7p3y7naa.re.qweatherapi.com",
                    "api_key": "a861d0d5e7bf4ee1a83d9a9e4f96d4da",
                    "default_location": "广州",
                },
                "get_news_from_chinanews": {
                    "default_rss_url": "https://www.chinanews.com.cn/rss/society.xml",
                    "society_rss_url": "https://www.chinanews.com.cn/rss/society.xml",
                    "world_rss_url": "https://www.chinanews.com.cn/rss/world.xml",
                    "finance_rss_url": "https://www.chinanews.com.cn/rss/finance.xml",
                },
                "get_news_from_newsnow": {
                    "url": "https://newsnow.busiyi.world/api/s?id=",
                    "news_sources": "澎湃新闻;百度热搜;财联社",
                },
                "home_assistant": {
                    "devices": ["客厅,玩具灯,switch.abc"],
                    "base_url": "http://homeassistant.local:8123",
                    "api_key": "你的home assistant api访问令牌",
                },
                "play_music": {
                    "music_dir": "./music",
                    "music_ext": [".mp3", ".wav"],
                    "refresh_time": 300,
                },
                "search_from_ragflow": {
                    "description": "当用户问xxx时调用本方法",
                    "base_url": "http://192.168.0.8",
                    "api_key": "ragflow-xxx",
                    "dataset_ids": ["123456789"],
                },
                "web_search": {
                    "provider": "metaso",
                    "description": "联网搜索工具。",
                    "max_results": 5,
                    "api_key": "mk-xxx",
                },
            },
            # 两条分支的 functions 清单**同时在树上**——「切换后清单不消失」的
            # 事实基础。两份清单内容刻意不同，好让「哪份是哪份」可判。
            "Intent": {
                "nointent": {"type": "nointent"},
                "intent_llm": {
                    "type": "intent_llm", "llm": "ChatGLMLLM",
                    "functions": ["web_search", "get_weather"],
                },
                "function_call": {
                    "type": "function_call",
                    "functions": ["change_role", "get_weather"],
                },
            },
        })
        # 本机用户配置：只启用了 get_weather（六个插件是「未启用」的现场）。
        _write_yaml(project / "data" / ".config.yaml", {
            "plugins": {"get_weather": {"default_location": ""}},
        })

        self.handler = ConfigHandler({}, str(project))
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
        ])
        return app

    async def asyncTearDown(self):
        self._tmp.cleanup()
        await super().asyncTearDown()

    async def _html(self, slug="tools"):
        resp = await self.client.request("GET", f"/xiaozhi/config/{slug}/")
        self.assertEqual(resp.status, 200)
        return await resp.text()

    async def _js(self):
        return await (await self.client.request(
            "GET", "/xiaozhi/config/config_domain_page.js")).text()

    def _schema(self, html):
        marker = '<script id="domain-schema" type="application/json">'
        start = html.index(marker) + len(marker)
        end = html.index("</script>", start)
        return json.loads(html[start:end])

    def _paths(self, schema):
        return [f["path"] for g in schema["groups"] for f in g["fields"]]

    # ── 1. 32 字段全量可达，逐卡对账 ─────────────────────────────
    async def test_tools_page_carries_all_thirty_two_fields(self):
        """32 = 3 散字段 + 23 插件 + 6 意图分支；常用 27 / 更多设置 5（§3 / §7）。"""
        schema = self._schema(await self._html())
        paths = self._paths(schema)
        self.assertEqual(len(paths), TOOLS_FIELD_TOTAL,
                         "§7 插件与工具表 32 个字段（list 记 1）必须全部有落位")
        self.assertEqual(len(set(paths)), len(paths), "字段路径不得重复")
        layers = [f["layer"] for g in schema["groups"] for f in g["fields"]]
        self.assertEqual(layers.count("common"), TOOLS_COMMON_TOTAL, "常用 27")
        self.assertEqual(layers.count("more"), TOOLS_MORE_TOTAL, "更多设置 5")
        # 注入的域表必须与 page_domains.py 逐行相等：注入漏一行 = 页面少一个控件，
        # 且脏计数也少一个。两边同源才算「同一份归属表既喂渲染又喂计数」。
        self.assertEqual(paths, [f.path for f in domains.TOOLS.all_fields()])

    async def test_each_plugin_card_keeps_its_declared_field_count(self):
        """七个插件卡，每张卡的字段数照 §7 表——插件库要能到达**每一个**插件。

        判别力：只看总数 32 发现不了「web_search 多一条、get_weather 少一条」——
        那种错在总数上刚好抵消，而在使用现场（天气插件里找不到 api_key）才暴露。
        按 ``plugins.<插件名>.`` 前缀把 23 拆开对。
        """
        schema = self._schema(await self._html())
        paths = self._paths(schema)
        seen = {}
        for path in paths:
            if not path.startswith("plugins."):
                continue
            name = path.split(".")[1]
            seen.setdefault(name, []).append(path)
        self.assertEqual(sorted(seen), sorted(PLUGIN_FIELDS),
                         "§7 表里的七个插件卡一个不少")
        for name, count in PLUGIN_FIELDS.items():
            self.assertEqual(len(seen[name]), count, f"plugins.{name} 字段数照表")
        # 23 个插件字段 + 6 个意图分支字段 + 3 条散字段 = 32（闭合成总数，
        # 否则「有字段没归到任何卡名下」这件事就没人报）。
        self.assertEqual(sum(PLUGIN_FIELDS.values()) + sum(INTENT_BRANCH_FIELDS.values()) + 3,
                         TOOLS_FIELD_TOTAL)

    async def test_the_three_scattered_fields_land_in_the_tools_domain(self):
        """§7 表头三条：工具调用超时 / 外部 MCP 接入点 / 当前生效意图引擎。

        判别力：`selected_module.Intent` 极易被误留在引擎域（毕竟兄弟六行都在
        那里）。它在 §7 表里属**本域**，`page_domains.ENGINE_CATEGORIES` 里
        没有 `Intent`——两处必须一致，否则页面上的意图选择器会被引擎域页抢走。
        """
        paths = self._paths(self._schema(await self._html()))
        for path in ("tool_call_timeout", "mcp_endpoint", "selected_module.Intent"):
            self.assertIn(path, paths, f"{path} 必须落位在插件与工具域")
        self.assertNotIn("Intent", domains.ENGINE_CATEGORIES,
                         "selected_module.Intent 不属引擎域（§7 表头行）")
        # 也不得混进引擎域的归属表。
        self.assertNotIn("selected_module.Intent",
                         [f.path for f in domains.ENGINE.all_fields()])

    async def test_unenabled_plugins_are_reachable_not_folded_away(self):
        """§7 移交注记 2：未启用插件的参数不再折进 `<details>` 黑洞。

        判别力：只渲染 `Intent.*.functions` 清单里那几个插件的实现，会让另外
        六个插件的参数**完全不可达**（旧页面就是这么干的：「未启用折叠」）。
        契约钉的是「它们有落位」：六个未启用插件的字段都在域表里。
        """
        paths = self._paths(self._schema(await self._html()))
        # 夹具里 function_call 只启用了 get_weather —— 其余六个都是「未启用」。
        for name in PLUGIN_FIELDS:
            prefixed = [p for p in paths if p.startswith(f"plugins.{name}.")]
            self.assertEqual(len(prefixed), PLUGIN_FIELDS[name],
                             f"未启用的插件 {name} 的参数必须照样可达"
                             "（AC：不折进 <details> 黑洞）")

    # ── 2. 意图双清单各自成卡 ────────────────────────────────────
    async def test_both_intent_function_lists_live_in_their_own_branch_cards(self):
        """§7 移交注记 1：两份 functions 启用清单同时可见、各在自己的分支卡里。

        判别力：把两条分支的字段塞进同一张卡，或者只渲染**选中**分支的卡，
        都会让「切换 selected_module.Intent 后清单消失」以另一种方式复现。
        断言的是**卡片分组**：`Intent.function_call.functions` 与
        `Intent.intent_llm.functions` 必须属于两个不同的 group id。
        """
        schema = self._schema(await self._html())
        owner = {}
        for g in schema["groups"]:
            for f in g["fields"]:
                owner[f["path"]] = g["id"]
        fc = owner["Intent.function_call.functions"]
        il = owner["Intent.intent_llm.functions"]
        self.assertNotEqual(fc, il, "两份清单必须各在自己的卡里（同卡 = 切换即消失）")
        for gid in (fc, il):
            self.assertRegex(gid, r"^[a-z0-9-]+$", "分支卡 id 只用 ASCII（深链锚点）")
        # 每条分支的字段数照 §7 表（2 / 1 / 3）。
        per_branch = {}
        for path, gid in owner.items():
            if path.startswith("Intent."):
                per_branch.setdefault(gid, []).append(path)
        for branch, count in INTENT_BRANCH_FIELDS.items():
            gid = owner[f"Intent.{branch}.type"]
            self.assertEqual(len(per_branch[gid]), count,
                             f"Intent.{branch} 卡照 §7 表 {count} 字段")

    async def test_intent_branch_list_is_injected_not_redeclared_in_the_page(self):
        """分支清单的单一事实源在 page_domains.py，随域表注入（§9 / §7）。

        判别力：页面里再抄一份 `['function_call','nointent','intent_llm']`，
        两份会在「上游新增一条分支」时分叉——而分叉的表现是**切过去就切不回来**
        （下拉里没有那条分支）。
        """
        schema = self._schema(await self._html())
        self.assertEqual(schema["intent_branches"], list(domains.INTENT_BRANCHES),
                         "域表必须带上服务端的意图分支清单")
        js = await self._js()
        self.assertNotRegex(
            js, r"const INTENT_BRANCHES = \[",
            "页面不得硬编码分支清单——它必须从注入的域表读")
        self.assertRegex(js, r"SCHEMA\.intent_branches")

    async def test_other_domain_pages_also_carry_the_branch_list(self):
        """非工具域也要带上它：脏分组在哪个域渲染都用同一份（单一事实源）。"""
        for slug in ("dialogue", "engine", "system"):
            schema = self._schema(await self._html(slug))
            self.assertEqual(schema["intent_branches"], list(domains.INTENT_BRANCHES))

    async def test_the_intent_selector_is_a_dropdown_over_the_injected_branches(self):
        """`selected_module.Intent` 的控件是**下拉**，候选 = 注入的分支清单。

        判别力：写成文本框的话，写错一个名字就是「意图引擎静默失效」，
        而它除了下拉没有别的防呆手段（与引擎页六行同理）。
        """
        js = await self._js()
        self.assertIn("data-selcat", js, "选择器控件走 data-selcat（与引擎六行同一路）")
        self.assertRegex(js, r"selcat: 'Intent'",
                         "意图选择器必须把 selcat 指到 Intent")

    # ── 3. 插件库与引擎库同构 ───────────────────────────────────
    async def test_the_plugin_library_reuses_the_engine_library_mechanism(self):
        """§5.1 / §7 移交注记 2：「同构」在代码上的落点是**同一份实现**。

        判别力：两段像样的代码不算同构——它们会在「搜索的跨度」「折叠的时机」
        「命中项的标签」上慢慢分叉，用户看到的是「同一个库两种脾气」。
        契约钉的是**共用**：库的外框渲染、字段清单展平、卡片、按需填体
        各只有一个函数，两个域通过 spec 参数化差异。
        """
        js = await self._js()
        # 用**计数**而不是存在性：「保留原函数但同时另写一份」能绕过 `assertIn`，
        # 绕不过 `count == 1`（存在性断言在这个位置是假的防线）。
        self.assertEqual(js.count("function renderLibrary("), 1,
                         "库的外框必须只有一个实现")
        self.assertEqual(js.count("function fieldsOf(root)"), 1,
                         "字段清单展平必须只有一个实现")
        self.assertEqual(js.count("function componentCard("), 1,
                         "卡片必须只有一个实现")
        self.assertEqual(js.count("function fillEngineBody("), 1,
                         "按需填体必须只有一个实现")
        # 分组卡也必须只有一份（tools 域只许参数化复用，不得另写一张）。
        self.assertEqual(js.count("function groupCard("), 1,
                         "分组卡必须只有一个实现（「更多设置」折叠口径只有一份）")
        # 两个 spec：引擎与插件。
        self.assertIn("ENGINE_LIBRARY", js)
        self.assertIn("PLUGIN_LIBRARY", js)
        # 库渲染函数里的域分支只允许在「选哪个 spec」这一处。
        self.assertNotIn("function renderPluginLibrary(", js,
                         "插件库不得另写一份库渲染（那就是第二把尺子）")

    async def test_the_plugin_library_has_a_search_box_and_names_come_from_the_tree(self):
        """搜索框必须存在；插件名清单**从配置树读**（§9 规则 1）。

        判别力：从域表字段枚举反推插件名，会让「树上存在、表里没列过」的插件
        （上游新增）彻底不可达——与本机自加 `Mlx*TTS` 是同一条规则。
        """
        js = await self._js()
        self.assertIn("id=\"${esc(spec.searchId)}\"", js, "搜索框由 spec 驱动（两库共用）")
        self.assertIn("plugSearch", js, "插件库必须有搜索框（AC：搜索与引擎库一致）")
        self.assertIn("function pluginNames(", js)
        self.assertRegex(js, r"const tree = getPath\(state, 'plugins'\)",
                         "插件名清单必须从配置树取")

    async def test_the_shared_library_code_only_reads_well_formed_specs(self):
        """两库的 spec 必须给出共享代码真的会读的东西（静态契约，无 DOM）。

        判别力：共享代码做的事（`spec.groups` 上遍历 / `reduce`、`spec.searchId`
        进 `getElementById`）要求 spec 字段的形状固定。这里把两个 spec 的字面量
        形状钉住——`groups` 是非空数组、`searchId` 非空、`rootOf`/`namesOf`/
        `cardOf`/`typeOf`/`missingText` 都存在。写错一个（如把插件库的组轴写成
        `null`）就是运行时崩溃，而这类错**不能**用 headless DOM 验证
        （父 spec 不引入浏览器测试基建），只能在交付内容上钉。
        """
        js = await self._js()
        for name in ("ENGINE_LIBRARY", "PLUGIN_LIBRARY"):
            block = _spec_block(js, name)
            for key in ("groups:", "searchId:", "namesOf:", "rootOf:",
                        "typeOf:", "cardOf:", "missingText:"):
                self.assertIn(key, block, f"{name} 缺 {key}（共享代码会读它）")
            self.assertNotRegex(block, r"groups:\s*null",
                                f"{name} 的组轴不得为 null（共享代码会遍历它）")
        # 插件库的组轴是一个非空数组（单组：插件没有类目那样的天然分块）。
        self.assertRegex(_spec_block(js, "PLUGIN_LIBRARY"), r"groups: \[PLUGIN_GROUP\]")
        # 搜索无果的文案与「跨全部 N」文案用的都是**条目数**，不是组数——
        # 说组数就会出现「在全部 1 个插件里都找不到」这种废话。
        self.assertIn("const totalItems = groups.reduce(", js)
        self.assertRegex(js, r"spec\.missingText\(totalItems\)")

    async def test_the_plugin_library_folds_lazily_like_the_engine_library(self):
        """真折叠（§2.5）：折叠态 DOM 字段数 = 0，体按 `toggle` 才构建。

        判别力：先渲染再藏的实现，在折叠态下 DOM 里仍有控件——「深度 ≤3」与
        「折叠态字段数 = 0」两条同时失守。两库共用实现的证据之一就是这条
        测试与引擎缝那条**读的是同一段代码**。
        """
        js = await self._js()
        self.assertIn('data-built="0"', js, "组件体初始必须是空的（未构建）")
        self.assertIn("function fillEngineBody(", js, "按需填体的实现必须只有一个")
        self.assertRegex(js, r"addEventListener\('toggle'",
                         "展开时才构建体（折叠态 DOM 字段数 = 0 的实现前提）")
        # 卡片锚点：域特定属性之外还有一个跨库共用的根路径锚点。
        self.assertIn("data-lib-root=", js,
                      "两个库的重建/脏前缀逻辑必须认同一个锚点（否则两套选择器）")

    async def test_plugin_card_dirty_prefix_matching_covers_the_whole_component(self):
        """插件卡的脏标记按**组件根前缀**匹配（与引擎卡同一实现）。"""
        js = await self._js()
        self.assertRegex(js, r"p\.startsWith\(root \+ '\.'\)",
                         "组件脏标记必须按根路径前缀匹配")

    # ── 4. 脏分组的组件范围（本票最易踩的一处） ──────────────────
    async def test_the_tools_page_passes_a_component_scope_to_dirty_grouping(self):
        """§5.3 / §5.4：插件与意图分支是「可选中/可启用组件」。

        判别力：`plugins.get_weather.api_key` 与 `Intent.function_call.functions`
        都是**三段路径**。沿用「三段 = 引擎块」的判据会把它们误判成「未选中引擎
        的字段」而落进「仅提前配好」——插件不是引擎。所以域页必须把
        「选中分支 + 它的 functions 清单」交给状态模型，而不是只传引擎六族。
        """
        js = await self._js()
        self.assertIn("function currentToolsScope(", js)
        self.assertRegex(js, r"toolsScope\(state, INTENT_BRANCHES\)",
                         "组件范围必须由状态模型的 toolsScope 算（不是页面自己推）")
        self.assertRegex(js,
                         r"groupDirty\(DIRTY, currentSelection\(\), ENGINE_CATEGORIES, tools\)",
                         "域页必须把组件范围作为第四个参数交给 groupDirty")
        # 意图选中值必须进 selection：否则「所有分支都算未选中」。
        self.assertRegex(js, r"getPath\(state, 'selected_module\.Intent'\)",
                         "currentSelection 必须收 Intent（否则分组全归未选中）")

    # ── 5. 保存整树往返：插件与意图字段真能写下去 ────────────────
    async def test_saving_an_unenabled_plugin_field_round_trips(self):
        """未启用插件的字段可编辑、可保存（AC：32 字段可编辑、保存）。

        走端到端整树 diff（`before` / `after` 两棵掩码后的树），写未启用插件的
        路径在协议上完全合法。契约要钉的是「真的写下去了」——不是「接口回了 ok」。
        """
        full = await (await self.client.request(
            "GET", "/xiaozhi/config/api/full")).json()
        before = full["config"]
        after = yaml.safe_load(yaml.safe_dump(before))
        # 改一个**未启用**插件的字段（function_call 只启用了 get_weather）。
        after["plugins"]["web_search"]["max_results"] = 9
        after["plugins"]["home_assistant"]["base_url"] = "http://ha.local:8123"

        resp = await self.client.request(
            "POST", "/xiaozhi/config/api/save",
            json={"before": before, "after": after})
        self.assertTrue((await resp.json())["ok"])

        written = yaml.safe_load(
            (Path(self._tmp.name) / "data" / ".config.yaml").read_text("utf-8"))
        self.assertEqual(written["plugins"]["web_search"]["max_results"], 9,
                         "未启用插件的字段必须真的写进用户配置（不是静默丢弃）")
        self.assertEqual(written["plugins"]["home_assistant"]["base_url"],
                         "http://ha.local:8123")

    async def test_saving_the_intent_lists_and_switch_round_trips(self):
        """两份清单与意图选择器都要能存下去（§5.3：切换算一次改动）。"""
        full = await (await self.client.request(
            "GET", "/xiaozhi/config/api/full")).json()
        before = full["config"]
        after = yaml.safe_load(yaml.safe_dump(before))
        after["Intent"]["intent_llm"]["functions"] = ["web_search"]
        after["selected_module"]["Intent"] = "intent_llm"

        resp = await self.client.request(
            "POST", "/xiaozhi/config/api/save",
            json={"before": before, "after": after})
        self.assertTrue((await resp.json())["ok"])
        written = yaml.safe_load(
            (Path(self._tmp.name) / "data" / ".config.yaml").read_text("utf-8"))
        self.assertEqual(written["Intent"]["intent_llm"]["functions"], ["web_search"])
        self.assertEqual(written["selected_module"]["Intent"], "intent_llm")

    # ── 6. 密钥三态在插件卡上同样生效（§5.5） ───────────────────
    async def test_secret_state_signal_reaches_plugin_cards(self):
        """插件卡的密钥控件同样靠服务端信号判「已配置」，不靠正则猜掩码。

        判别力：`plugins.get_weather.api_key` 是有值的密钥、
        `plugins.web_search.api_key` 是模板占位符（"mk-xxx"）、
        `plugins.home_assistant.api_key` 是中文占位符——三态各不相同。
        """
        full = await (await self.client.request(
            "GET", "/xiaozhi/config/api/full")).json()
        state = full["config_state"]
        self.assertTrue(state["plugins.get_weather.api_key"]["configured"],
                        "真密钥必须被显式判为已配置")
        self.assertFalse(state["plugins.home_assistant.api_key"]["configured"],
                         "中文占位符不算已配置（判据是存在信号，不是掩码形态）")
        # 值绝不出现在 config_state 里（信号只有布尔）。
        self.assertEqual(set(state["plugins.get_weather.api_key"]), {"configured"})

    # ── hash 深链：``#<插件名>`` 直达插件卡 ─────────────────────────
    async def test_plugin_hash_anchor_uses_the_same_attribute_the_card_emits(self):
        """``#get_weather`` 必须能直达插件卡（§4.5 的 ``#<分组id>`` 延伸）。

        判别力（本条是被真 bug 追着加的）：锚点曾经按元素 id 拼字符串
        （``plug-`` + 插件名），而卡片生成的 id 是 ``plug-plugins-<名>``
        ——两个值永远不相等，分支看似在、其实永不进入，深链静默失效。
        所以这条同时钉两边：卡片发出的锚点属性值、与 hash 分支查的属性值，
        必须是同一个表达式（都按 ``plugins.<名>`` 拼），不许各拼各的。
        """
        js = await (await self.client.request(
            "GET", "/xiaozhi/config/config_domain_page.js")).text()
        # 卡片侧：插件卡带 ``data-plugin`` 锚点（componentCard 按 opts.fieldAttr 发出）。
        # 发卡侧钉的必须是**属性名与拼接同源**：``data-plugin=`` 这个字面串
        # 只出现在查询侧，光断言它存在只能证明「查询串在」，钉不住卡片发什么。
        self.assertIn("'data-plugin'", js,
                      "插件库 spec 要声明锚点属性名（componentCard 按它发属性）")
        self.assertIn("data-lib-root=", js, "卡片要发共用的根锚点属性")
        # hash 侧：按属性查、且锚点值由 ``plugins.`` + 插件名拼出。
        self.assertIn("details.engine[data-plugin=", js,
                      "hash 分支必须按 data-plugin 属性查卡片")
        self.assertIn("'plugins.' + id", js,
                      "hash 分支的锚点值必须与卡片同源（plugins.<名>）")
        # 发卡侧的同源拼接也要在：pluginCard 把根路径拼成 ``plugins.<名>``。
        self.assertIn("componentCard('plugins.' + name", js,
                      "插件卡发出的锚点值必须也是 plugins.<名>（与查询侧同源）")
        # 反例：按元素 id 拼字符串的旧写法不得回流——那正是失效的那版。
        self.assertNotIn("getElementById(`plug-", js,
                         "不许按元素 id 拼锚点（卡片 id 与 hash 值不同源）")


if __name__ == "__main__":
    unittest.main()
