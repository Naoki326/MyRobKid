#!/usr/bin/env python3
"""配置 API 的 HTTP 契约缝（issue #25，#24 Phase 0 地基）。

契约（父 spec §5.5 / §9 规则 3）：``api/full`` 对敏感字段**同时**给出

  1. 「配置里是否存在该键」的**显式信号**（``configured``），与
  2. **掩码值**（``value``），

二者**分离**。页面判定必须建立在服务端信号上，不许再用正则猜掩码形态。

为什么单列一个文件：这是**页面显示态不再撒谎**的唯一可观察出口。两个已知
同源显示 bug（全库密钥误判「已配置」、openai 引擎注入值伪装已配置）的根因
都是「页面把服务端掩码/注入后的显示态当成配置里真实存在的值」。修法是把
「存在信号」搬到服务端算 —— 但「搬到服务端」本身不构成验收，必须有一道
断言钉住它**真的分开了**。

判别力（本文件最易糊弄处）：
  1. **掩码值照旧返回**（向后兼容）—— 只加信号不改值，老页面不会瞎；
  2. **存在信号不能从掩码形态推出来** —— 模板占位符 ``你的xxx`` 与真实密钥
     在掩码后**长得一样**（都是 ``********``），所以「有掩码 = 已配置」这条
     推理必然把占位符判成已配置。本文件专门钉住这个反例：**掩码存在但信号为
     false**。把 configured 实现成 ``bool(masked_value)`` 会让这条立刻红。
  3. **空值 / 未配置键不出现在信号里为 true** —— ``未配置`` 必须真的是 false。
  4. ``api/save`` **往返**：改一个非敏感字段再读回，值必须变；掩码值不得被
     回写（防「保存一次把密钥写成星号」）。

不做的事：不起真实服务端进程（用 aiohttp 自带 ``test_utils`` 直接挂 handler）、
不连数据库、不发固件请求。本文件只钉「存在信号 × 掩码值」这条新缝。

运行：server/.venv/bin/python -m unittest discover -s server/tests -t server -v
"""
import sys
import unittest
from pathlib import Path

import yaml
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from core.api.config_handler import ConfigHandler  # noqa: E402

#: 真实密钥（够长，掩码形态是 ``sk-8****8aTk``）与模板占位符（短/含「你」，
#: 掩码形态是 ``********``）。两者掩码后**不同形**，但都属于"有掩码值"。
REAL_SECRET = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
PLACEHOLDER = "你的deepseek web key"


def _write_yaml(path, data):
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


class ConfigApiContract(AioHTTPTestCase):
    """``api/full`` 的存在信号 × 掩码值分离 + ``api/save`` 往返。"""

    async def get_application(self):
        # 用一个带真实密钥 + 一个模板占位符 + 一个完全未配置键的临时工程目录，
        # 三个反例同处一棵树 —— 「信号不是从掩码形态猜的」才可断言。
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        project = Path(self._tmp.name)
        (project / "config").mkdir()
        # 页面与状态模型用真实文件：路由的交付路径（含 MIME）必须被测到，
        # 存根 HTML 只能证明“路由存在”，证明不了“页面真的能加载模块”。
        real_config = SERVER_ROOT / "config"
        (project / "config" / "config_page.html").write_text(
            (real_config / "config_page.html").read_text(encoding="utf-8"),
            encoding="utf-8")
        (project / "config" / "config_state_model.js").write_text(
            (real_config / "config_state_model.js").read_text(encoding="utf-8"),
            encoding="utf-8")
        _write_yaml(project / "config.yaml", {
            "server": {"ip": "0.0.0.0", "port": 8002},
            "LLM": {
                "RealLLM": {
                    "type": "openai",
                    "base_url": "https://example.invalid/v1",
                    "api_key": REAL_SECRET,
                },
                "TemplateLLM": {
                    "type": "openai",
                    "base_url": "https://example.invalid/v1",
                    "api_key": PLACEHOLDER,
                },
                # 完全没有 api_key 这个键 —— 「未配置」的正例。
                "NoKeyLLM": {
                    "type": "openai",
                    "base_url": "https://example.invalid/v1",
                },
            },
            # 数组内的敏感键：用真实 config.yaml 的 shape（context_providers 是
            # **list**，页面按 ``context_providers.0`` 点号路径渲染）。掩码与存在
            # 信号必须覆盖同一批键，且路径必须是页面 getPath 查得到的那种。
            "context_providers": [
                {
                    "name": "a",
                    "api_key": PLACEHOLDER,
                    "headers": {
                        # 大写形态：与 authorization 是同一把尺，不得收窄。
                        "Authorization": "Bearer " + REAL_SECRET,
                    },
                },
                {"name": "b", "api_key": REAL_SECRET},
            ],
        })
        (project / "data").mkdir()
        _write_yaml(project / "data" / ".config.yaml", {})

        self.handler = ConfigHandler({}, str(project))
        app = web.Application()
        app.add_routes([
            web.get("/xiaozhi/config/", self.handler.handle_page),
            web.get("/xiaozhi/config/config_state_model.js",
                    self.handler.handle_state_model),
            web.get("/xiaozhi/config/api/full", self.handler.handle_full),
            web.post("/xiaozhi/config/api/save", self.handler.handle_save),
        ])
        return app

    async def asyncTearDown(self):
        self._tmp.cleanup()
        await super().asyncTearDown()

    # ── 取信号的小工具 ─────────────────────────────────────────
    async def _full(self):
        resp = await self.client.request("GET", "/xiaozhi/config/api/full")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertTrue(body["ok"])
        return body

    def _signals(self, body):
        """从响应里取存在信号表。

        信号形态：``config_state``（路径 → {"configured": bool}）。用独立字典
        而不是塞进值里，是因为「值」与「存在」必须能在协议层面分开取到 ——
        混在同一个字符串里等于没分离。
        """
        state = body.get("config_state")
        self.assertIsInstance(
            state, dict,
            "api/full 必须返回显式的存在信号表 config_state（与 config 分离）")
        return state

    # ── 1. 存在信号真的存在，且与掩码值分离 ────────────────────
    async def test_full_returns_explicit_existence_signal_separate_from_mask(self):
        body = await self._full()
        signals = self._signals(body)

        # 掩码值照旧在 config 里（向后兼容：老页面的读取路径不瞎）。
        self.assertEqual(body["config"]["LLM"]["RealLLM"]["api_key"], "sk-a****7890")
        # 存在信号在另一处，且说的是「配置里有没有这个键」。
        self.assertTrue(signals["LLM.RealLLM.api_key"]["configured"])

    async def test_placeholder_is_not_reported_as_configured(self):
        """模板占位符 ``你的xxx`` **不是**已配置。

        判别力：这是「全库密钥误判已配置」那个 bug 的最小复现。占位符掩码后
        同样是 ``********``，所以任何形如 ``configured = bool(masked_value)``
        或 ``configured = isMaskShape(masked_value)`` 的实现都会在这里红。
        判据必须是「这个键在配置里存在且值不是占位/空」。
        """
        body = await self._full()
        signals = self._signals(body)
        cell = signals["LLM.TemplateLLM.api_key"]
        self.assertFalse(
            cell["configured"],
            "模板占位符（你的deepseek web key）只有掩码形态、没有真实值，"
            "不得报成已配置 —— 这正是旧页面拿正则猜掩码形态造的谎")

    async def test_missing_key_is_not_reported_as_configured(self):
        """配置里不存在的敏感键：信号表里**没有**它（= 未配置），绝不报 true。

        判据是「不得报成已配置」。缺键不产生信号，是因为「哪些键该有」属于
        页面元信息（父 spec §9 规则 2 的声明式元信息表）的职责，不是配置树的
        职责 —— 本节只钉住服务端不会把「不存在」说成「已配置」。
        """
        body = await self._full()
        signals = self._signals(body)
        cell = signals.get("LLM.NoKeyLLM.api_key")
        if cell is not None:
            self.assertFalse(cell["configured"])
        self.assertFalse(
            any(p.endswith("NoKeyLLM.api_key") and c["configured"]
                for p, c in signals.items()),
            "配置里根本没有这个键，不得报成已配置")

    async def test_empty_string_is_not_configured(self):
        """空串 = 未配置（页面要显「未配置」，不是「已配置」）。"""
        project = Path(self._tmp.name)
        data = yaml.safe_load((project / "config.yaml").read_text("utf-8"))
        data["LLM"]["EmptyLLM"] = {"type": "openai", "api_key": ""}
        _write_yaml(project / "config.yaml", data)

        body = await self._full()
        signals = self._signals(body)
        self.assertFalse(signals["LLM.EmptyLLM.api_key"]["configured"])

    async def test_real_secret_masks_to_opaque_value_not_plaintext(self):
        """真实密钥绝不以明文出现在响应里（掩码是安全底线，不能被这次改动破）。"""
        resp = await self.client.request("GET", "/xiaozhi/config/api/full")
        raw = await resp.text()
        self.assertNotIn(REAL_SECRET, raw)
        self.assertNotIn(PLACEHOLDER, raw)

    async def test_non_sensitive_fields_are_not_signalled_as_secrets(self):
        """非敏感字段不产生密钥信号（信号表的意义是「这里是密钥」）。"""
        body = await self._full()
        signals = self._signals(body)
        self.assertNotIn("LLM.RealLLM.base_url", signals)
        self.assertNotIn("server.port", signals)

    async def test_signal_does_not_leak_the_secret_value(self):
        """存在信号里只有布尔，不夹带值 —— 分离的第二个必要条件。"""
        body = await self._full()
        signals = self._signals(body)
        cell = signals["LLM.RealLLM.api_key"]
        self.assertEqual(set(cell.keys()), {"configured"})
        self.assertIsInstance(cell["configured"], bool)

    # ── 2. save 往返 ────────────────────────────────────────────
    async def test_save_roundtrip_writes_non_sensitive_change(self):
        body = await self._full()
        config = body["config"]
        before = config
        after = yaml.safe_load(yaml.safe_dump(config))  # deep copy
        after["LLM"]["RealLLM"]["base_url"] = "https://changed.invalid/v1"

        resp = await self.client.request(
            "POST", "/xiaozhi/config/api/save",
            json={"before": before, "after": after})
        self.assertEqual(resp.status, 200)
        saved = await resp.json()
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["changed"], 1)

        reloaded = await self._full()
        self.assertEqual(reloaded["config"]["LLM"]["RealLLM"]["base_url"],
                         "https://changed.invalid/v1")

    async def test_save_never_writes_the_mask_back_over_a_real_secret(self):
        """掩码值不得被回写：保存一次把密钥变成 ``sk-a****7890`` 是灾难。

        判别力：``before``/``after`` 都带掩码时，服务端必须跳过掩码占位。
        这条同时保护「未改动的密钥」与「只改了别的字段」两种往返。

        断言的是**合并后的生效配置**（``api/full`` 读回）而不是用户自定义文件
        本身：密钥原本只存在于基础 config.yaml，用户配置里不该凭空多出一条
        被掩码污染的副本。
        """
        body = await self._full()
        before = body["config"]
        after = yaml.safe_load(yaml.safe_dump(before))
        after["LLM"]["RealLLM"]["some_new_field"] = "hi"

        resp = await self.client.request(
            "POST", "/xiaozhi/config/api/save",
            json={"before": before, "after": after})
        self.assertTrue((await resp.json())["ok"])

        project = Path(self._tmp.name)
        written = yaml.safe_load((project / "data" / ".config.yaml").read_text("utf-8"))
        written_llm = written.get("LLM", {}).get("RealLLM", {})
        self.assertNotIn(
            "api_key", written_llm,
            "掩码占位不得被当成「新值」写进用户配置")
        # 生效配置里的真实密钥不受保存影响（读回仍是掩码，但底层值没被改）。
        reloaded = await self._full()
        self.assertEqual(reloaded["config"]["LLM"]["RealLLM"]["api_key"],
                         "sk-a****7890")
        self.assertTrue(
            reloaded["config_state"]["LLM.RealLLM.api_key"]["configured"])

    async def test_save_rejects_malformed_payload(self):
        resp = await self.client.request(
            "POST", "/xiaozhi/config/api/save", json={"before": [], "after": []})
        self.assertEqual(resp.status, 400)

    # ── 3. 页面与状态模型的交付路径 ─────────────────────────────
    async def test_array_nested_secret_reports_existence_signal(self):
        """数组内的敏感键也必须出现在信号表里，且判据同样不看掩码形态。

        为什么单独钉：``_mask_tree`` 与 ``_secret_state`` 是同一文件里两个
        遍历函数，口径一旦不一致，就会出现「有掩码值、无信号」的键 ——
        页面拿不到信号只能回落到 ``val !== ''``，把占位符谎报成「已配置」，
        正是本票要根除的那个 bug 的另一条路径。

        路径必须是**页面 getPath 认识的**形态：页面用 ``context_providers.0``
        点号索引渲染（config_page.html 的 engFields 调用），不是 ``[0]``。
        信号给了但查不到，等于没给 —— 所以这里直接钉路径形状。
        """
        body = await self._full()
        signals = self._signals(body)
        placeholder_path = "context_providers.0.api_key"
        real_path = "context_providers.1.api_key"

        self.assertIn(
            placeholder_path, signals,
            "数组内敏感键的信号路径必须是页面点号形态（context_providers.0...），"
            "否则页面查不到、仍会回落谎报")
        self.assertFalse(
            signals[placeholder_path]["configured"],
            "数组内的模板占位符不得报成已配置")
        self.assertTrue(
            signals[real_path]["configured"],
            "数组内的真实密钥必须报成已配置")

    async def test_uppercase_sensitive_key_is_masked_and_signalled(self):
        """``Authorization``（大写形态）与 ``authorization`` 是同一把尺。

        收窄的代价：该键在页面上从密码框降级为普通文本框，且掩码与信号
        双双漏掉它。判据必须大小写不敏感。路径同样必须是页面点号形态。
        """
        body = await self._full()
        signals = self._signals(body)
        path = "context_providers.0.headers.Authorization"

        self.assertNotEqual(
            body["config"]["context_providers"][0]["headers"]["Authorization"],
            "Bearer " + REAL_SECRET,
            "大写 Authorization 必须被掩码，不得明文外泄")
        self.assertIn(path, signals, "大写 Authorization 必须有存在信号")
        self.assertTrue(signals[path]["configured"])

    async def test_state_model_module_is_served_as_javascript(self):
        """状态模型模块必须能用 text/javascript 取到——浏览器对模块脚本硬校验 MIME。"""
        resp = await self.client.request(
            "GET", "/xiaozhi/config/config_state_model.js")
        self.assertEqual(resp.status, 200)
        self.assertIn("javascript", resp.headers["Content-Type"])
        body = await resp.text()
        # 导出的判定函数必须在模块里（页面与 node 缝共用同一份）。
        self.assertIn("isSensitiveKey", body)
        self.assertIn("computeDirty", body)

    async def test_page_loads_the_state_model_module(self):
        """页面必须以 module 方式引入状态模型（否则 import 语句直接语法错）。"""
        resp = await self.client.request("GET", "/xiaozhi/config/")
        self.assertEqual(resp.status, 200)
        html = await resp.text()
        self.assertIn('type="module"', html)
        self.assertIn("./config_state_model.js", html)


if __name__ == "__main__":
    unittest.main()
