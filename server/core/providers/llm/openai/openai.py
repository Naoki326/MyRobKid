import httpx
import openai
import re
from openai.types import CompletionUsage
from config.logger import setup_logging
from core.utils.util import check_model_key
from core.providers.llm.base import LLMProviderBase
from urllib.parse import urlparse

TAG = __name__
logger = setup_logging()


# 推理档位：网关/模型接受的取值（由低到高）。
# 注意各模型接受度不同：例如 deepseek-v4.1-flash 接受 low..max 但拒绝 none；
# gemini 通道则完全拒绝 reasoning_effort 参数（已在 _create_stream 里自动降级）。
REASONING_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_REASONING_EFFORT = "medium"
# 「不推理」的几种写法：部分模型支持，不支持的由域名规则或默认行为兜底
REASONING_OFF_VALUES = ("none", "off", "disabled", "false", "0")

# 部分平台需要用自家参数关闭思考模式（当 reasoning_effort 配为 none 时生效）。
# thirking.top 未列入：该网关的 reasoning_effort=none 会被部分模型以 400 拒绝，
# 「关闭」时宁可不注入参数（保持平台默认），也不发会被拒的值。
THINKING_DISABLED_DOMAINS = {
    "aliyuncs.com": {"enable_thinking": False},
    "deepseek.com": {"thinking": {"type": "disabled"}},
    "bigmodel.cn": {"thinking": {"type": "disabled"}},
    "moonshot.cn": {"thinking": {"type": "disabled"}},
    "volces.com": {"thinking": {"type": "disabled"}},
}


def resolve_reasoning_effort(value):
    """把配置值归一化为档位字符串。

    - 未配置/空 → 默认档位（medium）
    - low/medium/high/xhigh/max → 原样
    - none/off/disabled/false/0 → "none"（交给域名规则或平台默认）
    - 其他非法值 → 默认档位，并告警
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_REASONING_EFFORT
    normalized = str(value).strip().lower()
    if normalized in REASONING_EFFORT_LEVELS:
        return normalized
    if normalized in REASONING_OFF_VALUES:
        return "none"
    logger.bind(tag=TAG).warning(
        f"reasoning_effort 取值非法: {value!r}，"
        f"已回退为 {DEFAULT_REASONING_EFFORT}（可选 {'/'.join(REASONING_EFFORT_LEVELS)}/none）"
    )
    return DEFAULT_REASONING_EFFORT


class LLMProvider(LLMProviderBase):
    def __init__(self, config):
        self.model_name = config.get("model_name")
        self.api_key = config.get("api_key")
        if "base_url" in config:
            self.base_url = config.get("base_url")
        else:
            self.base_url = config.get("url")
        
        timeout_config = config.get("timeout")
        if isinstance(timeout_config, dict):
            # 细粒度超时配置
            custom_timeout = httpx.Timeout(
                pool=timeout_config.get("pool", 2.0),
                connect=timeout_config.get("connect", 3.0),
                write=timeout_config.get("write", 5.0),
                read=timeout_config.get("read", 60.0)
            )
        elif isinstance(timeout_config, (int, float)) and timeout_config > 0:
            # 兼容旧的单一超时配置（整数或浮点数）
            custom_timeout = httpx.Timeout(timeout_config)
        else:
            # 未配置或配置无效，使用默认值
            custom_timeout = httpx.Timeout(300)

        param_defaults = {
            "max_tokens": int,
            "temperature": lambda x: round(float(x), 1),
            "top_p": lambda x: round(float(x), 1),
            "frequency_penalty": lambda x: round(float(x), 1),
        }

        for param, converter in param_defaults.items():
            value = config.get(param)
            try:
                setattr(
                    self,
                    param,
                    converter(value) if value not in (None, "") else None,
                )
            except (ValueError, TypeError):
                setattr(self, param, None)

        logger.debug(
            f"意图识别参数初始化: {self.temperature}, {self.max_tokens}, {self.top_p}, {self.frequency_penalty}"
        )

        # 推理档位：配置页「模型引擎 → LLM 参数」可选，默认中档（medium）。
        self.reasoning_effort = resolve_reasoning_effort(config.get("reasoning_effort"))

        model_key_msg = check_model_key("LLM", self.api_key)
        if model_key_msg:
            logger.bind(tag=TAG).error(model_key_msg)
        self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=custom_timeout)

    _THINK_TAG_RE = re.compile(r"(</?think>)")

    @staticmethod
    def normalize_dialogue(dialogue):
        """自动修复 dialogue 中缺失 content 的消息"""
        for msg in dialogue:
            if "role" in msg and "content" not in msg:
                msg["content"] = ""
        return dialogue

    def _apply_reasoning_policy(self, request_params: dict):
        """按推理档位与域名规则注入参数。

        - low..max → 注入 reasoning_effort=<档位>；
        - none → 先用平台自家关闭参数（THINKING_DISABLED_DOMAINS），
          无命中则不注入（保持平台默认，不猜参数以免 400）。
        """
        effort = self.reasoning_effort
        if effort in REASONING_EFFORT_LEVELS:
            request_params.setdefault("extra_body", {})["reasoning_effort"] = effort
            logger.bind(tag=TAG).info(f"推理档位: {effort}")
            return

        parsed_url = urlparse(self.base_url)
        domain = parsed_url.netloc
        for disabled_domain, params in THINKING_DISABLED_DOMAINS.items():
            if disabled_domain in domain:
                request_params.setdefault("extra_body", {}).update(params)
                logger.bind(tag=TAG).info(f"为域名 {domain} 关闭思考模式，参数: {params}")
                return
        logger.bind(tag=TAG).info(
            f"域名 {domain} 无可用关闭参数，保持平台默认推理行为"
        )

    def _create_stream(self, request_params: dict):
        """创建流式请求；网关拒绝 reasoning_effort 时去掉该参数重试一次。

        各网关/模型对 reasoning_effort 的接受度不一（有的只收 low..max，
        有的完全不支持），一次自动降级可避免整轮对话因 400 变成
        “我们稍后再试”。
        """
        try:
            return self.client.chat.completions.create(**request_params)
        except openai.BadRequestError as e:
            if "reasoning_effort" not in str(e):
                raise
            extra = request_params.get("extra_body") or {}
            extra.pop("reasoning_effort", None)
            if not extra:
                request_params.pop("extra_body", None)
            logger.bind(tag=TAG).warning(
                f"网关拒绝 reasoning_effort={self.reasoning_effort}，已去掉该参数重试：{e}"
            )
            return self.client.chat.completions.create(**request_params)

    @classmethod
    def _strip_think(cls, content, state: dict):
        """过滤 <think>...</think> 思考内容，state 记录跨 chunk 可见状态。

        按标签分段处理，标签前的正常文本不会被误丢
        （旧实现用 split 一次性切分会丢掉 <think> 前缀的正文）。
        """
        if not content:
            return ""
        # 快速路径：无标签时直接按当前可见性返回（避日常对话的正则开销）
        if "<think>" not in content and "</think>" not in content:
            return content if state["active"] else ""
        out = []
        for token in cls._THINK_TAG_RE.split(content):
            if token == "<think>":
                state["active"] = False
            elif token == "</think>":
                state["active"] = True
            elif state["active"]:
                out.append(token)
        return "".join(out)

    def response(self, session_id, dialogue, **kwargs):
        dialogue = self.normalize_dialogue(dialogue)

        request_params = {
            "model": self.model_name,
            "messages": dialogue,
            "stream": True,
        }

        # 添加可选参数,只有当参数不为None时才添加
        optional_params = {
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
            "top_p": kwargs.get("top_p", self.top_p),
            "frequency_penalty": kwargs.get("frequency_penalty", self.frequency_penalty),
        }

        for key, value in optional_params.items():
            if value is not None:
                request_params[key] = value

        # 按推理档位注入参数
        self._apply_reasoning_policy(request_params)

        responses = self._create_stream(request_params)

        think_state = {"active": True}
        try:
            for chunk in responses:
                try:
                    delta = chunk.choices[0].delta if getattr(chunk, "choices", None) else None
                    content = getattr(delta, "content", "") if delta else ""
                except IndexError:
                    content = ""
                content = self._strip_think(content, think_state)
                if content:
                    yield content
        finally:
            responses.close()

    def response_with_functions(self, session_id, dialogue, functions=None, **kwargs):
        dialogue = self.normalize_dialogue(dialogue)

        request_params = {
            "model": self.model_name,
            "messages": dialogue,
            "stream": True,
            "tools": functions,
        }

        optional_params = {
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
            "top_p": kwargs.get("top_p", self.top_p),
            "frequency_penalty": kwargs.get("frequency_penalty", self.frequency_penalty),
        }

        for key, value in optional_params.items():
            if value is not None:
                request_params[key] = value

        # 按推理档位注入参数
        self._apply_reasoning_policy(request_params)

        stream = self._create_stream(request_params)

        think_state = {"active": True}
        try:
            for chunk in stream:
                if getattr(chunk, "choices", None):
                    delta = chunk.choices[0].delta
                    content = getattr(delta, "content", "")
                    # 带推理时思考内容不能念给用户听
                    content = self._strip_think(content, think_state)
                    tool_calls = getattr(delta, "tool_calls", None)
                    yield content, tool_calls
                elif isinstance(getattr(chunk, "usage", None), CompletionUsage):
                    usage_info = getattr(chunk, "usage", None)
                    logger.bind(tag=TAG).info(
                        f"Token 消耗：输入 {getattr(usage_info, 'prompt_tokens', '未知')}，"
                        f"输出 {getattr(usage_info, 'completion_tokens', '未知')}，"
                        f"共计 {getattr(usage_info, 'total_tokens', '未知')}"
                    )
        finally:
            stream.close()

    def probe_stream(self, dialogue) -> dict:
        """量一次真实请求的分段耗时（配置页「测试」按钮用）。

        走与线上完全相同的参数组装与降级逻辑，所以量到的首句延迟就是
        设备侧会遇到的量级；同时回报思考字数——思考 token 计入
        max_tokens，思考吃光预算时正文就是空的（对话会听起来“没回答”）。
        """
        import time

        request_params = {
            "model": self.model_name,
            "messages": dialogue,
            "stream": True,
        }
        for name in ("max_tokens", "temperature", "top_p", "frequency_penalty"):
            value = getattr(self, name, None)
            if value is not None:
                request_params[name] = value

        self._apply_reasoning_policy(request_params)
        stream = self._create_stream(request_params)

        t0 = time.perf_counter()
        first_chunk = first_content = None
        reasoning_chars = 0
        reply_parts = []
        think_state = {"active": True}
        try:
            for chunk in stream:
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
                if first_chunk is None:
                    first_chunk = time.perf_counter() - t0
                reasoning = getattr(delta, "reasoning", None) or getattr(
                    delta, "reasoning_content", None
                )
                if reasoning:
                    reasoning_chars += len(reasoning)
                content = self._strip_think(getattr(delta, "content", ""), think_state)
                if content:
                    if first_content is None:
                        first_content = time.perf_counter() - t0
                    reply_parts.append(content)
        finally:
            stream.close()

        def ms(value):
            return round(value * 1000) if value is not None else None

        return {
            "model": self.model_name,
            "reasoning_effort": self.reasoning_effort,
            "max_tokens": self.max_tokens,
            "first_chunk_ms": ms(first_chunk),
            "first_content_ms": ms(first_content),
            "total_ms": ms(time.perf_counter() - t0),
            "reasoning_chars": reasoning_chars,
            "reply": "".join(reply_parts).strip(),
        }
