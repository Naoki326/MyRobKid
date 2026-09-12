#!/usr/bin/env python3
"""引擎库的 HTTP 契约缝（issue #27，父 spec §5 / §7 / §9）。

契约（父 spec §5，逐条落实）：

1. **450 字段全量可达**（§5 开头 / §7 引擎表）——68 条引擎 / 442 个块内字段
   + 8 条引擎全局参数（`tts_timeout`、`module_test.test_sentences[i]`、六行
   `selected_module.*`）= 450。同一份归属表（`config/page_domains.py`）既喂
   页面渲染又喂计数，所以「页面遍历到了几个字段」与被测的期望值不是两个来源。
2. **未启用引擎可编辑、可保存**（§5.2）——保存走端到端整树 diff，写未启用
   引擎的路径在协议上合法。契约要钉的是「它的字段确实到了浏览器」：域表里
   有 68 条引擎的全部字段路径。
3. **跨类目搜索的证据**（§5.1）——页面里搜 `mlx` 必须同时命中 LLM 与 TTS；
   这在 HTTP 响应里只能钉一半（渲染在客户端），能钉的是**类目清单与类目标签
   的存在**：类目标签的类名与「跨全部 N 个类目」的文案必须随页面交付。
4. **hash 深链语法**（§4.5）——`#<类目小写id>` 与 `#<类目>/<引擎名>`：类目
   id 必须是 ASCII kebab-case（这里就是类目小写名），引擎卡必须有
   `data-engine="<类目>.<引擎名>"` 这个锚点。
5. **单一事实源**（§9 / CONTEXT.md 引擎库）——引擎类目清单只有一处
   （`page_domains.ENGINE_CATEGORIES`），页面不写第二份；域表注入时必须带上它。

为什么不测 DOM 呈现行为：父 spec 明确**不引入浏览器测试基建**。折叠、搜索
命中、确认层按人工走查口径；本文件只钉 HTTP 契约与交付内容（域表 + 脚本）。

运行：server/.venv/bin/python -m unittest discover -s server/tests -t server
"""
import json
import re
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

#: §7 引擎表的机械复算结果——写死在这里，是「表被改动」时唯一会红的东西。
#: 引擎域 = 442 块内字段 + 8 全局 = 450；常用 302 / 更多设置 148（§3 合计表）。
ENGINE_FIELD_TOTAL = 450
ENGINE_ENTRY_TOTAL = 68
ENGINE_GLOBALS_TOTAL = 8
ENGINE_COMMON_TOTAL = 302
ENGINE_MORE_TOTAL = 148

#: 六个类目（§7 引擎表的分块轴）。
CATEGORIES = ["VAD", "ASR", "LLM", "VLLM", "TTS", "Memory"]

#: 本机 `data/.config.yaml` 真实装了的引擎（含 5 条模板里没有的 `Mlx*TTS`）。
#: 「全部引擎可达」的正例就在这五条上：它们在 `server/config.yaml` 里**没有**
#: 条目，只能从配置树被发现——只按模板表渲染的页面会漏掉它们。
LOCAL_ONLY_TTS = ["MlxTTS", "MlxStreamTTS", "MlxWanwanStreamTTS",
                  "MlxMengwaStreamTTS", "MlxKafeiStreamTTS"]


def _write_yaml(path, data):
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


class EngineLibraryContract(AioHTTPTestCase):
    """引擎域页的交付内容：450 字段 / 类目 / 深链锚点 / 单一事实源。"""

    async def get_application(self):
        self._tmp = tempfile.TemporaryDirectory()
        project = Path(self._tmp.name)
        (project / "data").mkdir()
        (project / "config").mkdir()
        real = SERVER_ROOT / "config"
        for name in ("config_domain_page.html", "config_domain_page.js",
                     "config_state_model.js", "config_page.html"):
            (project / "config" / name).write_text(
                (real / name).read_text(encoding="utf-8"), encoding="utf-8")

        _write_yaml(project / "config.yaml", {
            "server": {"ip": "0.0.0.0", "port": 8002},
            "tts_timeout": 15,
            "module_test": {"test_sentences": ["你好"]},
            "selected_module": {
                "VAD": "SileroVAD", "ASR": "FunASR", "LLM": "ThirkingLLM",
                "VLLM": "ThirkingVLLM", "TTS": "MlxKafeiStreamTTS",
                "Memory": "nomem",
            },
            # 树上的引擎与模板里的引擎**不同源**：本机自加的 Mlx*TTS 只在
            # data/.config.yaml 里，模板 config.yaml 里没有它们。这正是
            # 「可达的全部 ≠ 方案列过的 68 条」的现场。
            "VAD": {"SileroVAD": {"type": "silero", "threshold": 0.5}},
            "LLM": {"ThirkingLLM": {"type": "openai", "model_name": "m",
                                    "api_key": "sk-real", "reasoning_effort": "high"}},
        })
        _write_yaml(project / "data" / ".config.yaml", {
            "TTS": {name: {"type": "mlx", "url": "http://127.0.0.1:9753/tts"}
                    for name in LOCAL_ONLY_TTS},
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

    async def _html(self, slug="engine"):
        resp = await self.client.request("GET", f"/xiaozhi/config/{slug}/")
        self.assertEqual(resp.status, 200)
        return await resp.text()

    def _schema(self, html):
        marker = '<script id="domain-schema" type="application/json">'
        start = html.index(marker) + len(marker)
        end = html.index("</script>", start)
        return json.loads(html[start:end])

    def _paths(self, schema):
        return [f["path"] for g in schema["groups"] for f in g["fields"]]

    # ── 1. 450 字段全量可达（含未启用引擎） ─────────────────────
    async def test_engine_page_carries_all_four_hundred_fifty_fields(self):
        """450 = 442 块内 + 8 全局；常用 302 / 更多设置 148（§3 / §7）。"""
        schema = self._schema(await self._html())
        paths = self._paths(schema)
        self.assertEqual(len(paths), ENGINE_FIELD_TOTAL,
                         "§7 引擎表 450 个字段（list 记 1）必须全部有落位")
        self.assertEqual(len(set(paths)), len(paths), "字段路径不得重复")
        layers = [f["layer"] for g in schema["groups"] for f in g["fields"]]
        self.assertEqual(layers.count("common"), ENGINE_COMMON_TOTAL, "常用 302")
        self.assertEqual(layers.count("more"), ENGINE_MORE_TOTAL, "更多设置 148")
        # 注入的域表必须与 page_domains.py 逐行相等：注入漏一行 = 页面少一个
        # 控件，且脏计数也少一个。两边同源才算「同一份归属表既喂渲染又喂计数」。
        self.assertEqual(paths, [f.path for f in domains.ENGINE.all_fields()])

    async def test_all_sixty_eight_engines_keep_their_declared_field_counts(self):
        """68 条引擎，每条字段数照 §7 表——引擎库要能到达**每一条**。

        按「``<类目>.<引擎名>.`` 前缀」计数，不按段数：表里有些字段是 4 段
        （``LLM.FastgptLLM.variables.k``、``Memory.powermem.llm.config.api_key``），
        按段数分组会把它们藏到「不是引擎字段」里，断言看着绿、字段其实没落位。
        """
        schema = self._schema(await self._html())
        paths = self._paths(schema)
        prefix_of = {f"{cat}.{name}": f"{cat}.{name}."
                     for cat, name, _ in domains.ENGINE_ENTRIES}

        def owner_of(path):
            """路径属于哪条引擎——**最长前缀胜**。

            不能用 ``path.startswith(prefix)`` 直接配：``ASR.FunASR.`` 是
            ``ASR.FunASRServer.type`` 的前缀，按短前缀分组会把 FunASRServer 的
            6 个字段算到 FunASR 头上（总数看着对、分布是错的）。
            """
            best = None
            for key, prefix in prefix_of.items():
                if path.startswith(prefix) and (best is None or len(prefix) > len(best[1])):
                    best = (key, prefix)
            return best[0] if best else None

        seen = {key: [] for key in prefix_of}
        globals_ = []
        for path in paths:
            key = owner_of(path)
            if key is None:
                globals_.append(path)
            else:
                seen[key].append(path)
        self.assertEqual(len(seen), ENGINE_ENTRY_TOTAL, "§7 表 68 条引擎")
        for cat, name, fields in domains.ENGINE_ENTRIES:
            key = cat + "." + name
            self.assertEqual(len(seen[key]), len(fields), f"{key} 字段数照表")
            # 逐条路径对得上（不是只对个数）。
            self.assertEqual(seen[key], [f"{key}.{tail}" for tail, _ in fields],
                             f"{key} 的字段路径逐条对得上")
        # 68 条引擎的字段之和 + 8 条全局 = 450；两者相加必须闭合成总数，
        # 否则「有字段没归到任何引擎名下」这件事就没人报。
        self.assertEqual(len(globals_), ENGINE_GLOBALS_TOTAL,
                         "只有那 8 条引擎全局参数不该归到任何引擎名下")
        self.assertEqual(sum(len(v) for v in seen.values()) + len(globals_),
                         ENGINE_FIELD_TOTAL)

    async def test_engine_globals_and_the_six_selectors_are_all_present(self):
        """8 条引擎全局参数：tts_timeout / 模块测试语句 / 六行 selected_module。"""
        schema = self._schema(await self._html())
        paths = self._paths(schema)
        for path in ("tts_timeout", "module_test.test_sentences[i]"):
            self.assertIn(path, paths)
        for cat in CATEGORIES:
            self.assertIn(f"selected_module.{cat}", paths,
                          "「当前生效」六行是 selected_module.* 的编辑控件")
        self.assertEqual(len(domains.ENGINE_GLOBALS), 2)
        self.assertEqual(len(domains.ENGINE_SELECTORS), len(CATEGORIES))

    async def test_the_blocks_sum_to_four_hundred_forty_two_plus_eight(self):
        """机械对账：§7 块内 442（各块头相加）+ 表头 8 = 450。

        判别力：只看总数 450 没法发现「TTS 多了一条、Memory 少了一条」——
        那种错在总数上刚好抵消，而在使用现场（TTS 里找不到某条引擎）才暴露。
        这条按**类目**把 442 拆开对，同时钉住 §3 合计表的分层数 302/148。
        """
        per_cat = {cat: 0 for cat in CATEGORIES}
        for cat, name, fields in domains.ENGINE_ENTRIES:
            per_cat[cat] += len(fields)
        # §7 各块头写的数字相加：VAD 5 + ASR 109 + LLM 72 + VLLM 16 + TTS 224 + Memory 16。
        self.assertEqual(per_cat, {"VAD": 5, "ASR": 109, "LLM": 72,
                                   "VLLM": 16, "TTS": 224, "Memory": 16})
        self.assertEqual(sum(per_cat.values()) + ENGINE_GLOBALS_TOTAL,
                         ENGINE_FIELD_TOTAL)
        # 分层计数包含那 8 条全局（6 条常用选择器 + 2 条更多设置）。
        self.assertEqual(len(domains.ENGINE.common_fields()), ENGINE_COMMON_TOTAL)
        self.assertEqual(len(domains.ENGINE.more_fields()), ENGINE_MORE_TOTAL)

    async def test_unselected_engine_fields_are_editable_not_omitted(self):
        """未启用引擎的字段同样有落位（§5.2：可编辑、可保存、计入脏标记）。"""
        schema = self._schema(await self._html())
        paths = self._paths(schema)
        # 本机一条都没装的 Memory 引擎：它们的字段仍必须在域表里（可提前配好）。
        memory = [p for p in paths if p.startswith("Memory.")]
        self.assertGreater(len(memory), 0, "Memory 类目的字段必须可达（未启用也要能配）")
        # 选中的是三族各一条，未选中的条目必须也在（表里 68 条全覆盖）。
        self.assertIn("TTS.EdgeTTS.voice", paths)
        self.assertIn("ASR.DoubaoASR.appid", paths)

    # ── 2. 跨类目搜索与类目标签的证据 ───────────────────────────
    async def test_search_is_cross_category_and_hits_carry_a_category_pill(self):
        """§5.1：搜索必须跨类目；命中项带类目标签。

        判别力：类目内搜索在 LLM 下搜 `mlx` 得 0 条，而 TTS 实有 5 条——这是
        「找不到」痛点的直接成因。渲染是客户端的，HTTP 缝能钉的是**证据链**：
        类目标签的类名与「跨全部 N 个类目」的文案必须随页面交付，且搜索框
        存在（没有搜索框就没有跨类目搜索）。
        """
        js = (await (await self.client.request(
            "GET", "/xiaozhi/config/config_domain_page.js")).text())
        self.assertIn("catpill", js, "命中项必须有类目标签（catpill）")
        self.assertIn("ENGINE_CATEGORIES", js, "搜索必须遍历**全部**类目")
        # 跨类目遍历的证据：搜索分支在 "for (const group of groups)" 里逐组
        # 遍历，而引擎库的 ``groups`` 就是 ``ENGINE_CATEGORIES``（spec 参数化，
        # #28 把引擎库与插件库并成了同一份实现——两段像样的代码不算同构）。
        self.assertRegex(
            js, r"for \(const group of groups\)\s*\{[^}]*spec\.namesOf\(group\)",
            "搜索必须逐个组遍历（跨类目），不是只查当前 tab")
        self.assertRegex(
            js, r"groups: ENGINE_CATEGORIES",
            "引擎库的组轴就是全部六个类目（搜索因而是跨类目的）")
        html = await self._html()
        # 搜索框在页面脚本里动态生成（HTML 只有骨架），所以断言 JS 本身。
        # #28 之后搜索框由库的 spec 驱动（引擎库与插件库共用一份实现），
        # 所以这里钉的是「引擎库的 spec 声明了一个搜索框」——两库的搜索框
        # 形状因而是同一个模板，不会慢慢长得不一样。
        self.assertRegex(js, r"searchId: 'engSearch'",
                         "搜索框必须存在（没有搜索框就没有跨类目搜索）")
        self.assertIn('id="${esc(spec.searchId)}"', js,
                      "搜索框由 spec 驱动渲染（两库同一份模板）")
        # 命中项的**类目标签**必须带类别名：没有它，搜出来的 TTS 引擎会被当成
        # LLM 的（这正是「在 LLM 下搜 mlx 得 0 条」那个痛点的另一半）。
        self.assertRegex(js, r"catpill[^`]*\$\{esc\(opts\.tag\)\}",
                         "类目标签要真的把类目名渲染出来")

    async def test_engine_page_renders_a_category_tab_bar(self):
        """类目 tab 把 450 字段切成六块（§5.1）。"""
        js = await (await self.client.request(
            "GET", "/xiaozhi/config/config_domain_page.js")).text()
        self.assertIn("cattab", js, "类目 tab 必须存在")
        # tab 的数据源是库的 spec 组轴，引擎库那一份就是注入的类目清单。
        self.assertRegex(js, r"groups: ENGINE_CATEGORIES")

    # ── 3. 折叠：折叠态 DOM 字段数 = 0 的实现前提 ───────────────
    async def test_engine_list_comes_from_the_tree_not_the_table(self):
        """「全部引擎」的清单来自**配置树 + selected_module.***，不是方案表。

        判别力：只按模板/表渲染的页面会漏掉本机自加的 `Mlx*TTS`
        （`server/config.yaml` 里没有它们），也会漏掉 `selected_module.Memory
        = nomem` 指向但树上没有的引擎（「当前生效」会显示一个点不进去的名字）。
        两个来源的并集才是「可达的全部」。
        """
        js = await (await self.client.request(
            "GET", "/xiaozhi/config/config_domain_page.js")).text()
        self.assertIn("function enginesOf(", js)
        # 树上取：遍历 <类目> 对象的键。
        self.assertRegex(js, r"const tree = getPath\(state, cat\)",
                         "引擎清单必须从配置树取")
        # selected_module 补：指向未装引擎时下拉里必须有它。
        self.assertRegex(js, r"push\(getPath\(state, 'selected_module\.' \+ cat\)\)",
                         "selected_module 指到但树上没有的引擎必须可达")

    async def test_locally_added_engines_are_reachable_though_absent_from_the_template(self):
        """本机自加的 `Mlx*TTS` 必须可达——它们在 `server/config.yaml` 里没有条目。

        判别力：只按模板/方案表渲染的页面会把这五条藏起来，而它们正是当前
        生效的那一族（`selected_module.TTS = MlxKafeiStreamTTS`）。
        夹具里这五条**只**写在用户配置（模板里没有），所以它们能被渲染出来
        就是「引擎清单来自配置树」的证据。
        """
        template_path = Path(self._tmp.name) / "config.yaml"
        template = yaml.safe_load(template_path.read_text("utf-8"))
        self.assertNotIn("MlxTTS", template.get("TTS", {}),
                         "夹具的前提：模板里没有 Mlx*TTS（该断言只是钉住前提）")
        js = await (await self.client.request(
            "GET", "/xiaozhi/config/config_domain_page.js")).text()
        # 引擎清单从树上取（遍历 <类目> 对象的键）。
        self.assertRegex(js, r"for \(const \[k, v\] of Object\.entries\(tree\)\)",
                         "引擎清单必须从配置树遍历得到，不能只认方案表")
        # 表只决定分层：本机自加引擎的字段在表里没有行，仍要渲染。
        self.assertRegex(js, r"const declaredRow = declared\.find",
                         "表外字段按「更多设置」回落到渲染（表说分层、树说存在）")

    async def test_nested_engine_fields_are_flattened_not_dropped(self):
        """嵌套字段（`Memory.powermem.llm.config.api_key`）必须展平渲染。

        判别力：只渲染顶层扇出的实现会把 `powermem` 的 11 个字段丢成 1 个——
        「450 全量可达」就只剩一句口号。方案表里那些 4 段路径正是这件事的现场。
        """
        js = await (await self.client.request(
            "GET", "/xiaozhi/config/config_domain_page.js")).text()
        self.assertIn("function collectFields(", js, "嵌套块要展平")
        # 空 object 也要占一行（§7 口径：空 object 记 1 个字段）。
        self.assertIn("emptyobj", js, "空 object 不能被静默丢掉")

    async def test_engine_body_is_built_lazily_so_collapsed_cards_hold_no_fields(self):
        """折叠是真折叠（§2.5）：「深度 ≤3」成立的依据是折叠态 DOM 字段数 = 0。

        判别力：先渲染再藏的实现在 collapsed 状态下 DOM 里仍有控件，深度与
        「折叠态字段数 = 0」两条都失守。这里钉住的是**实现前提**——引擎体是
        空的、按 `toggle` 才填。
        """
        js = await (await self.client.request(
            "GET", "/xiaozhi/config/config_domain_page.js")).text()
        self.assertIn('data-built="0"', js, "引擎体初始必须是空的（未构建）")
        self.assertRegex(js, r"addEventListener\('toggle'",
                         "展开时才构建体（折叠态 DOM 字段数 = 0 的实现前提）")
        self.assertIn("fillEngineBody", js)

    # ── 4. hash 深链：`#<类目>` 与 `#<类目>/<引擎名>` ────────────
    async def test_hash_deep_links_use_ascii_category_ids_and_engine_anchors(self):
        """§4.5：`#tts` 选 tab、`#tts/MlxKafeiStreamTTS` 直达引擎卡。

        片段只用 ASCII id（不含中文）：中文进 hash 会被不同浏览器编成不同形态，
        「一条链接 = 0 点击直达」就不可转述了。
        """
        js = await (await self.client.request(
            "GET", "/xiaozhi/config/config_domain_page.js")).text()
        self.assertIn("data-engine=", js, "引擎卡必须有 data-engine 锚点")
        self.assertRegex(js, r"id\.includes\('/'\)", "两段形式 `#<类目>/<引擎名>`")
        # 类目 id 是 ASCII（类目名本身就是 VAD/ASR/… 的小写形态）。
        for cat in CATEGORIES:
            self.assertRegex(cat, r"^[A-Za-z]+$", "类目名本身必须是 ASCII")
        # 分组 id 是 kebab-case ASCII（类目卡与 selectors/globals 卡）。
        for g in domains.ENGINE.groups:
            self.assertRegex(g.id, r"^[a-z0-9-]+$",
                             "分组 id 只用 ASCII，深链里不许出现中文")

    # ── 5. 单一事实源：类目清单只有一处 ─────────────────────────
    async def test_engine_categories_are_injected_not_redeclared_in_the_page(self):
        """类目清单的单一事实源在 page_domains.py，随域表注入。"""
        schema = self._schema(await self._html())
        self.assertEqual(schema["engine_categories"], CATEGORIES,
                         "域表必须带上服务端的类目清单")
        js = await (await self.client.request(
            "GET", "/xiaozhi/config/config_domain_page.js")).text()
        # 页面不得再抄一份字面量列表（页面里两份类目 = 两把尺子）。
        self.assertNotRegex(
            js, r"const ENGINE_CATEGORIES = \[",
            "页面不得硬编码类目清单——它必须从注入的域表读")
        self.assertRegex(js, r"SCHEMA\.engine_categories")

    async def test_other_domain_pages_also_carry_the_category_list(self):
        """非引擎域也要带上它：``selected_module.*`` 的脏条目在哪个域都要分组。"""
        for slug in ("dialogue", "system"):
            schema = self._schema(await self._html(slug))
            self.assertEqual(schema["engine_categories"], CATEGORIES)

    # ── 6. 保存整树往返：未启用引擎的改动真能写下去 ──────────────
    async def test_saving_an_engine_field_round_trips_through_the_whole_tree(self):
        """§5.2 的技术前提（#17 已核实，这里钉住它没被改动打破）：

        保存走端到端整树 diff（`before` / `after` 两棵掩码后的树），写**未启用**
        引擎的路径在协议上完全合法。契约要钉的是「真的写下去了」——不是
        「接口回了 ok」。
        """
        full = await (await self.client.request(
            "GET", "/xiaozhi/config/api/full")).json()
        before = full["config"]
        after = yaml.safe_load(yaml.safe_dump(before))
        # 改一条**未选中**引擎的字段（选中的是 MlxKafeiStreamTTS）。
        after["TTS"]["MlxStreamTTS"]["speed"] = 0.42
        after["TTS"]["MlxStreamTTS"]["tts_timeout"] = 33

        resp = await self.client.request(
            "POST", "/xiaozhi/config/api/save",
            json={"before": before, "after": after})
        self.assertTrue((await resp.json())["ok"])

        written = yaml.safe_load(
            (Path(self._tmp.name) / "data" / ".config.yaml").read_text("utf-8"))
        self.assertEqual(written["TTS"]["MlxStreamTTS"]["speed"], 0.42,
                         "未启用引擎的字段必须真的写进用户配置（不是静默丢弃）")
        self.assertEqual(written["TTS"]["MlxStreamTTS"]["tts_timeout"], 33)

    async def test_saving_a_selected_module_switch_round_trips(self):
        """§5.3：切换 selected_module.* 算一次改动，也要能存下去。"""
        full = await (await self.client.request(
            "GET", "/xiaozhi/config/api/full")).json()
        before = full["config"]
        after = yaml.safe_load(yaml.safe_dump(before))
        after["selected_module"]["TTS"] = "MlxStreamTTS"

        resp = await self.client.request(
            "POST", "/xiaozhi/config/api/save",
            json={"before": before, "after": after})
        self.assertTrue((await resp.json())["ok"])
        written = yaml.safe_load(
            (Path(self._tmp.name) / "data" / ".config.yaml").read_text("utf-8"))
        self.assertEqual(written["selected_module"]["TTS"], "MlxStreamTTS")

    # ── 7. 密钥三态在引擎页同样生效（§5.5） ─────────────────────
    async def test_secret_state_signal_reaches_the_engine_page(self):
        """引擎卡的密钥控件同样靠服务端信号判「已配置」，不靠正则猜掩码。"""
        full = await (await self.client.request(
            "GET", "/xiaozhi/config/api/full")).json()
        self.assertTrue(full["config_state"]["LLM.ThirkingLLM.api_key"]["configured"])
        js = await (await self.client.request(
            "GET", "/xiaozhi/config/config_domain_page.js")).text()
        self.assertIn("CONFIG_STATE", js, "密钥三态必须读服务端存在信号")
        self.assertNotRegex(
            js, r"\*\{4\}",
            "页面不得用正则猜掩码形态（那是两个同源显示 bug 的根因）")


if __name__ == "__main__":
    unittest.main()
