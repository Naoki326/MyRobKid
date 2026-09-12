"""page_domains.py — 页面域的字段归属表（父 spec §7 的机械复算）。

方案文档 §2 的三条规则（R1 求属主 / R2 定分层 / 接入字段名字集）机械算出了
§7 的五张映射表（527 字段）。本模块把**本票落地的两个域**——对话与角色（15）
与系统（13）——搬进代码，成为渲染与计数的单一事实源。

为什么字段归属要写在 Python 侧而不是页面里：

1. **测试缝在这里**。HTTP 契约缝用 ``unittest`` 起来，断言「域页真的渲染出
   这 28 个字段」时需要一个不依赖 DOM 的归属表；把表放在页面 JS 里，这条断言
   只能靠正则抓 HTML，脆且验不到计数。
2. **零字段丢失可复算**（§10.1）。字段总数与分层计数是方案的一条验收判据，
   逐条渲染的页面 HTML 里数不出来，表里可以。
3. **后续域（引擎/插件/设备）照抄形状**：加一个 ``Domain`` 只是往表里加条目，
   渲染器、脏计数、占位页都不用改。

表里**只放本票负责的两域**。其余三域（engine / tools / devices）本票只交付占位
页，它们的字段归属是 #27/#28/#29 的事——在这里预留空表反而会把「未实现」装成
「已裁决」。
"""

from dataclasses import dataclass, field
from typing import List, Optional

#: 分层名（§2.5）：「更多设置」是域内折叠容器，不是第三个域。
LAYER_COMMON = "common"
LAYER_MORE = "more"


@dataclass(frozen=True)
class Field:
    """一个叶子字段在域页上的落位。

    ``path`` 是**页面 getPath 认识的点号语义**（``end_prompt.enable``、
    ``context_providers.0.headers.Authorization``）——与 ``api/full`` 的
    ``config_state`` 信号路径同一口径，否则信号查不到等于没给。

    ``kind`` 决定控件形状：``text`` / ``number`` / ``bool`` / ``list`` / ``url``。
    ``list`` 的 ``path`` 指向数组本身（``wakeup_words``），不是元素。
    """

    path: str
    label: str
    kind: str = "text"
    layer: str = LAYER_COMMON
    hint: str = ""
    #: 分组 id（kebab-case，ASCII）——hash 深链用它定位域内分组（§4.5）。
    group: str = ""
    #: 控件附加属性（如 number 的 step）。
    step: Optional[str] = None


@dataclass(frozen=True)
class Group:
    """域内一个卡片/分组。``id`` 是 hash 深链的锚点（§4.5），必须 ASCII。"""

    id: str
    title: str
    desc: str = ""
    fields: List[Field] = field(default_factory=list)


@dataclass(frozen=True)
class DomainSchema:
    """一个页面域的完整落位：slug（用户契约，ADR-0012）+ 分组 + 字段。"""

    slug: str
    label: str
    groups: List[Group] = field(default_factory=list)

    def all_fields(self) -> List[Field]:
        return [f for g in self.groups for f in g.fields]

    def common_fields(self) -> List[Field]:
        return [f for f in self.all_fields() if f.layer == LAYER_COMMON]

    def more_fields(self) -> List[Field]:
        return [f for f in self.all_fields() if f.layer == LAYER_MORE]

    def group_for(self, path: str) -> Optional[Group]:
        for g in self.groups:
            if any(f.path == path for f in g.fields):
                return g
        return None


# ---------------------------------------------------------------------------
# 对话与角色（15 字段；§7 表逐行照抄，分层由表里的「层」列决定）
#
# R1：提示词系、唤醒/退出词、声纹身份、上下文源——「机器人是谁 / 说什么」。
# 分层：11 常用 / 4 更多设置（§3 合计表）。
#
# 分组按域内语义切三块，hash id 用 ASCII kebab-case（§4.5）：
#   #role        角色设定（prompt 系）
#   #wakeup      唤醒与对话（唤醒词、退出词、提示音）
#   #identity    声纹与上下文源
# ---------------------------------------------------------------------------
DIALOGUE = DomainSchema(
    slug="dialogue",
    label="对话与角色",
    groups=[
        Group(
            id="role",
            title="🎭 角色设定",
            desc="设备的人格与说话风格：系统提示词、提示词模板、出错回复、结束语。",
            fields=[
                Field("prompt", "角色 prompt", "textarea", LAYER_COMMON,
                      "设备的人格与说话风格（系统提示词）"),
                Field("prompt_template", "prompt 模板文件", "text", LAYER_MORE,
                      "系统提示词的模板文件名（agent-base-prompt.txt）"),
                Field("system_error_response", "出错回复", "text", LAYER_COMMON,
                      "模型调用失败时对用户说的话"),
                Field("end_prompt.enable", "开启结束语", "bool", LAYER_COMMON,
                      "", group="role"),
                Field("end_prompt.prompt", "结束语内容", "textarea", LAYER_COMMON,
                      "用户说退出指令后说的告别语"),
            ],
        ),
        Group(
            id="wakeup",
            title="🔔 唤醒与对话",
            desc="唤醒词、退出指令、开场白与说完话的提示音。",
            fields=[
                Field("wakeup_words", "唤醒词列表", "list", LAYER_COMMON,
                      "每个词一项；设备靠它开始聆听"),
                Field("exit_commands", "退出指令", "list", LAYER_COMMON,
                      "用户说这些词结束对话"),
                Field("enable_greeting", "唤醒后回复开场白", "bool", LAYER_COMMON),
                Field("enable_wakeup_words_response_cache", "唤醒词响应缓存", "bool",
                      LAYER_MORE, "开启后唤醒应答走缓存，响应更快"),
                Field("enable_stop_tts_notify", "说完话提示音", "bool",
                      LAYER_COMMON),
                Field("stop_tts_notify_voice", "提示音文件", "text", LAYER_COMMON,
                      "config/assets/tts_notify.mp3"),
            ],
        ),
        Group(
            id="identity",
            title="🪪 声纹与上下文源",
            desc="说话人身份识别，以及注入系统提示词的动态数据源。",
            fields=[
                Field("voiceprint.url", "声纹接口地址", "text", LAYER_MORE,
                      "外部声纹识别服务地址"),
                Field("voiceprint.speakers", "说话人列表", "list", LAYER_COMMON,
                      "每项格式：speaker_id,名称,描述"),
                Field("voiceprint.similarity_threshold", "相似度阈值", "number",
                      LAYER_MORE, "0-1，越高越严格", step="0.05"),
                Field("context_providers.0.url", "上下文源地址", "text",
                      LAYER_COMMON, "系统提示词里注入动态数据用的接口"),
            ],
        ),
    ],
)

# ---------------------------------------------------------------------------
# 系统（13 字段；§7 表逐行照抄）
#
# R1：编排者自身——监听地址、会话/文件生命周期、传输保活、日志、跨用途路径
# （log.data_dir 是「多属主 → 系统」的典型）。§2.5：**域内常用层为空时，折叠区
# 不渲染，全部字段平铺**——所以这里 13 个字段全是 more，页面直接平铺。
# ---------------------------------------------------------------------------
SYSTEM = DomainSchema(
    slug="system",
    label="系统",
    groups=[
        Group(
            id="listen",
            title="🌐 监听与端口",
            desc="服务端监听地址与端口（发给设备的连接地址不在这里，见通信协商）。",
            fields=[
                Field("server.ip", "监听 IP", "text", LAYER_MORE,
                      "0.0.0.0 = 所有网卡"),
                Field("server.port", "WebSocket 端口", "number", LAYER_MORE,
                      "设备语音连接端口"),
                Field("server.http_port", "HTTP/OTA 端口", "number", LAYER_MORE,
                      "配置页与 OTA 接口端口"),
                Field("server.vision_explain", "视觉分析地址", "text",
                      LAYER_MORE, "摄像头拍照识物接口"),
            ],
        ),
        Group(
            id="lifecycle",
            title="⏱ 会话与文件生命周期",
            desc="无语音断连、音频文件留存、WebSocket 保活。",
            fields=[
                Field("delete_audio", "用完删除音频文件", "bool", LAYER_MORE),
                Field("close_connection_no_voice_time", "无语音断开(秒)", "number",
                      LAYER_MORE, "默认 120"),
                Field("enable_websocket_ping", "WebSocket 心跳保活", "bool",
                      LAYER_MORE),
            ],
        ),
        Group(
            id="logging",
            title="📝 日志",
            desc="控制台与文件日志的格式、等级、路径。",
            fields=[
                Field("log.log_format", "控制台日志格式", "text", LAYER_MORE),
                Field("log.log_format_file", "文件日志格式", "text", LAYER_MORE),
                Field("log.log_level", "日志等级", "text", LAYER_MORE,
                      "INFO / DEBUG"),
                Field("log.log_dir", "日志目录", "text", LAYER_MORE),
                Field("log.log_file", "日志文件名", "text", LAYER_MORE),
                Field("log.data_dir", "数据目录", "text", LAYER_MORE,
                      "跨用途路径：数据库、固件库、TTS 输出都落在这里"),
            ],
        ),
    ],
)

#: 本票落地的域表（slug → schema）。未上线的三域**不在**这里——它们没有归属表，
#: 只有占位页；伪造一张空表会把「未裁决」装成「已裁决」。
DOMAIN_SCHEMAS = {d.slug: d for d in (DIALOGUE, SYSTEM)}
