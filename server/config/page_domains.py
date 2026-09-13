"""page_domains.py — 页面域的字段归属表（父 spec §7 的机械复算）。

方案文档 §2 的三条规则（R1 求属主 / R2 定分层 / 接入字段名字集）机械算出了
§7 的五张映射表（527 字段）。本模块把**五个域**——对话与角色（15）、
引擎（450）、插件与工具（32）、设备（17）、系统（13）——搬进代码，成为渲染与
计数的单一事实源。

为什么字段归属要写在 Python 侧而不是页面里：

1. **测试缝在这里**。HTTP 契约缝用 ``unittest`` 起来，断言「域页真的渲染出
   这些字段」时需要一个不依赖 DOM 的归属表；把表放在页面 JS 里，这条断言
   只能靠正则抓 HTML，脆且验不到计数。
2. **零字段丢失可复算**（§10.1）。字段总数与分层计数是方案的一条验收判据，
   逐条渲染的页面 HTML 里数不出来，表里可以。
3. **后续域（设备）照抄形状**：加一个 ``Domain`` 只是往表里加条目，
   渲染器、脏计数、占位页都不用改。

表里**只放已裁决的域**（本票之后五域齐备）。未来新增的域在方案裁决前不进
这张表——预留空表反而会把「未实现」装成「已裁决」。

除了五张字段表，本模块还持有两个**组件轴**（§5.1 / §7 移交注记 2）：
``ENGINE_CATEGORIES``（六族）与 ``INTENT_BRANCHES``（三条意图分支）。它们是
「哪些组件可被选中/启用」的单一事实源，既喂页面下拉的候选，也喂 ``classifyDirty``
的生效性分组——页面与状态模型两边都不再各写一份。
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
    #: 控件附加属性（如 number 的 step）。
    step: Optional[str] = None
    #: 字段级危险标记（§7 移交注记 4 / §6.6）：**只声明「这里有一个危险动作」**，
    #: 页面按它渲染危险视觉（`server.auth_key` 是现场唯一一条）。
    #:
    #: 为什么**不**升成 `level: normal|warn|danger` 三级：#30 的三级分级（§6.2）
    #: 是**操作**的属性（保存 / 重启服务 / 上传固件 / 删除固件…），判定与确认层
    #: 内容由 ``config_danger_model.js`` 机械算出；字段标记是**另一根轴**
    #: （“这个输入框旁边要提醒一句话”），它不参与分级、不触发确认层。把两者
    #: 合并成一个三级枚举会把「字段位置上的一句话提醒」装成「操作级别」——
    #: §6.6 明文：危险是按钮的属性，不是页面位置属性。
    danger: bool = False
    #: 危险标注的一句话说明（“改 = 全部设备 token 失效”）。
    danger_note: str = ""


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
    #: 域内**运行时面**面板（非配置字段：在线设备 / 固件库 / 配网）。
    #: 它们不是可保存的字段，所以不进 ``groups``、不参与计数；但它们的 id 是
    #: §4.5 定的 hash 深链锚点，必须与分组 id 同一套事实源。
    runtime_panels: List[dict] = field(default_factory=list)

    def all_fields(self) -> List[Field]:
        return [f for g in self.groups for f in g.fields]

    def common_fields(self) -> List[Field]:
        return [f for f in self.all_fields() if f.layer == LAYER_COMMON]

    def more_fields(self) -> List[Field]:
        return [f for f in self.all_fields() if f.layer == LAYER_MORE]


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
                      ""),
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

# ---------------------------------------------------------------------------
# 引擎（450 字段；§7 引擎表逐行照抄）
#
# R1：可替换组件——VAD/ASR/LLM/VLLM/TTS/Memory 六族 + 选择器 + 引擎全局参数。
# 写死某字段成常量，受损的是**该引擎的实现**（§2.1）。
#
# 表的形状（父 spec §5 / §7）：68 条引擎 / 442 个块内字段 + 8 条引擎全局参数
# （`tts_timeout`、`module_test.test_sentences[i]`、六行 `selected_module.*`）
# = **450**（§3 合计表口径）。
#
# 三条口径说明：
#
# 1. **表照方案写，不照本机装了什么写**。本机 `data/.config.yaml` 只装了
#    SileroVAD / FunASR / ThirkingLLM / ThirkingVLLM / 5 条 `Mlx*TTS`，
#    `Memory` 一条都没装；其余引擎只在 `server/config.yaml` 模板里以默认值
#    存在。表是「哪些字段有归宿」的**裁决**，不是「本机此刻有没有这个键」的
#    侦察——渲染依据仍是配置树（§9 规则 1），树上没有的键不渲染。
# 2. 表里「类型」一列在本机是 `-`（模板里才有值）。域页的控件形状**不读它**：
#    形状由运行时值决定（`typeof val`），敏感判定由 `isSensitiveKey` 决定。
#    这一列是盘点口径，不是渲染依据。
# 3. 引擎名与类目表里的字段路径**是完整点号形态**（`TTS.MlxKafeiStreamTTS.url`），
#    与 `api/full` 的 `config_state` 信号同一口径——键名分叉就会「信号给了但
#    页面查不到」，回落成猜掩码形态（两个同源显示 bug 的那条路径）。
#
# 引擎清单本身不在这张表里：可用引擎 = 配置树上的键 ∪ `selected_module.*`
# 指到的那一条。本机自加的 `Mlx*TTS` 在模板里没有条目，照样必须可达——
# 表只回答「这条引擎的字段怎么分层」，不回答「有哪些引擎」。
# ---------------------------------------------------------------------------

#: 引擎类目（六族）——**单一事实源**。
#:
#: 消费者三处：引擎域页的类目 tab 顺序、`classifyDirty` 的「是否真在引擎域」
#: 判据、`selected_module.*` 的六行下拉。三处各自硬编码一份就是三把尺子，
#: 分叉的后果是「某类目下改了字段却归错组」——静默且难查。
#: `selected_module.Intent` **不在其中**：它属插件与工具域（#28）。
ENGINE_CATEGORIES = ('VAD', 'ASR', 'LLM', 'VLLM', 'TTS', 'Memory')

#: 意图分支（`Intent.*` 的键）——**单一事实源**。
#:
#: 与 ``ENGINE_CATEGORIES`` 完全同构：`selected_module.Intent` 是它的选择器，
#: 选中分支的字段「重启后生效」，未选中分支是「仅提前配好」（§5.3/§5.4）。
#: 消费者三处：工具域页的意图分支卡、`classifyDirty` 的「是否真在意图域」判据、
#: `selected_module.Intent` 下拉的候选。
#:
#: 三条分支**不来自配置树**：``nointent`` 这类分支在本机可能没配（树上没有），
#: 但「切换意图引擎」的候选必须有它，否则用户切不回来。
INTENT_BRANCHES = ('function_call', 'nointent', 'intent_llm')

#: 引擎全局参数（§7 引擎表头两组）——不属于任何一条引擎。
ENGINE_GLOBALS = [
    Field('tts_timeout', 'TTS 超时(秒)', 'number', LAYER_MORE, '引擎全局参数；各 TTS 卡内同名键可覆盖'),
    Field('module_test.test_sentences[i]', '模块测试语句', 'list', LAYER_MORE, '测试语音管道用的句子'),
]

#: `selected_module.<类目>` —— 六行「当前生效」下拉，本票的编辑控件（§5）。
#: 层是常用（§7 表：六行都记 常用），且**切换即一次脏**（§5.3）。
ENGINE_SELECTORS = [
    Field('selected_module.VAD', '当前生效 VAD', "select", LAYER_COMMON),
    Field('selected_module.ASR', '当前生效 ASR', "select", LAYER_COMMON),
    Field('selected_module.LLM', '当前生效 LLM', "select", LAYER_COMMON),
    Field('selected_module.VLLM', '当前生效 VLLM', "select", LAYER_COMMON),
    Field('selected_module.TTS', '当前生效 TTS', "select", LAYER_COMMON),
    Field('selected_module.Memory', '当前生效 Memory', "select", LAYER_COMMON),
]

#: 68 条引擎的分层表：(类目, 引擎名, [(字段尾, 层), ...])。
#:
#: 这是「卡内两层」的机械依据（§2.3 / §7 末尾的接入字段名字集已被表本身吸收：
#: 表里「层」列就是最终裁决）。域页按它把每条引擎的字段分成常用/更多设置。
ENGINE_ENTRIES = [
    ('VAD', 'SileroVAD', [
        ('type', LAYER_COMMON),
        ('threshold', LAYER_MORE),
        ('threshold_low', LAYER_MORE),
        ('model_dir', LAYER_COMMON),
        ('min_silence_duration_ms', LAYER_MORE),
    ]),
    ('ASR', 'FunASR', [
        ('type', LAYER_COMMON),
        ('model_dir', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
        ('language', LAYER_COMMON),
    ]),
    ('LLM', 'ThirkingLLM', [
        ('type', LAYER_COMMON),
        ('model_name', LAYER_COMMON),
        ('url', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('max_tokens', LAYER_MORE),
        ('temperature', LAYER_MORE),
        ('reasoning_effort', LAYER_MORE),
    ]),
    ('VLLM', 'ThirkingVLLM', [
        ('type', LAYER_COMMON),
        ('model_name', LAYER_COMMON),
        ('url', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
    ]),
    ('TTS', 'MlxKafeiStreamTTS', [
        ('type', LAYER_COMMON),
        ('url', LAYER_COMMON),
        ('speed', LAYER_MORE),
        ('output_dir', LAYER_MORE),
        ('split_sentences', LAYER_MORE),
        ('tts_timeout', LAYER_MORE),
        ('voice', LAYER_COMMON),
        ('ref_audio', LAYER_COMMON),
        ('ref_text', LAYER_COMMON),
    ]),
    ('Memory', 'nomem', [
        ('type', LAYER_COMMON),
    ]),
    ('ASR', 'FunASRServer', [
        ('type', LAYER_COMMON),
        ('host', LAYER_COMMON),
        ('port', LAYER_COMMON),
        ('is_ssl', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
    ]),
    ('ASR', 'SherpaASR', [
        ('type', LAYER_COMMON),
        ('model_dir', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
        ('model_type', LAYER_COMMON),
    ]),
    ('ASR', 'SherpaParaformerASR', [
        ('type', LAYER_COMMON),
        ('model_dir', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
        ('model_type', LAYER_COMMON),
    ]),
    ('ASR', 'DoubaoASR', [
        ('type', LAYER_COMMON),
        ('appid', LAYER_COMMON),
        ('access_token', LAYER_COMMON),
        ('cluster', LAYER_COMMON),
        ('boosting_table_name', LAYER_MORE),
        ('correct_table_name', LAYER_MORE),
        ('output_dir', LAYER_MORE),
    ]),
    ('ASR', 'DoubaoStreamASR', [
        ('type', LAYER_COMMON),
        ('appid', LAYER_COMMON),
        ('access_token', LAYER_COMMON),
        ('resource_id', LAYER_COMMON),
        ('boosting_table_name', LAYER_MORE),
        ('correct_table_name', LAYER_MORE),
        ('enable_multilingual', LAYER_MORE),
        ('end_window_size', LAYER_MORE),
        ('output_dir', LAYER_MORE),
    ]),
    ('ASR', 'DoubaoStreamASRV2', [
        ('type', LAYER_COMMON),
        ('appid', LAYER_COMMON),
        ('access_token', LAYER_COMMON),
        ('resource_id', LAYER_COMMON),
        ('boosting_table_name', LAYER_MORE),
        ('correct_table_name', LAYER_MORE),
        ('enable_multilingual', LAYER_MORE),
        ('end_window_size', LAYER_MORE),
        ('output_dir', LAYER_MORE),
    ]),
    ('ASR', 'TencentASR', [
        ('type', LAYER_COMMON),
        ('appid', LAYER_COMMON),
        ('secret_id', LAYER_COMMON),
        ('secret_key', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
    ]),
    ('ASR', 'AliyunASR', [
        ('type', LAYER_COMMON),
        ('appkey', LAYER_COMMON),
        ('token', LAYER_COMMON),
        ('access_key_id', LAYER_COMMON),
        ('access_key_secret', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
    ]),
    ('ASR', 'AliyunStreamASR', [
        ('type', LAYER_COMMON),
        ('appkey', LAYER_COMMON),
        ('token', LAYER_COMMON),
        ('access_key_id', LAYER_COMMON),
        ('access_key_secret', LAYER_COMMON),
        ('host', LAYER_COMMON),
        ('max_sentence_silence', LAYER_MORE),
        ('output_dir', LAYER_MORE),
    ]),
    ('ASR', 'BaiduASR', [
        ('type', LAYER_COMMON),
        ('app_id', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('secret_key', LAYER_COMMON),
        ('dev_pid', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
    ]),
    ('ASR', 'OpenaiASR', [
        ('type', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('base_url', LAYER_COMMON),
        ('model_name', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
    ]),
    ('ASR', 'GroqASR', [
        ('type', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('base_url', LAYER_COMMON),
        ('model_name', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
    ]),
    ('ASR', 'VoskASR', [
        ('type', LAYER_COMMON),
        ('model_path', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
    ]),
    ('ASR', 'Qwen3ASRFlash', [
        ('type', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('base_url', LAYER_COMMON),
        ('model_name', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
        ('enable_lid', LAYER_MORE),
        ('enable_itn', LAYER_MORE),
        ('context', LAYER_MORE),
    ]),
    ('ASR', 'XunfeiStreamASR', [
        ('type', LAYER_COMMON),
        ('app_id', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('api_secret', LAYER_COMMON),
        ('domain', LAYER_COMMON),
        ('language', LAYER_COMMON),
        ('accent', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
    ]),
    ('ASR', 'AliyunBLStreamASR', [
        ('type', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('model', LAYER_COMMON),
        ('format', LAYER_COMMON),
        ('sample_rate', LAYER_MORE),
        ('disfluency_removal_enabled', LAYER_MORE),
        ('semantic_punctuation_enabled', LAYER_MORE),
        ('max_sentence_silence', LAYER_MORE),
        ('multi_threshold_mode_enabled', LAYER_MORE),
        ('punctuation_prediction_enabled', LAYER_MORE),
        ('inverse_text_normalization_enabled', LAYER_MORE),
        ('output_dir', LAYER_MORE),
    ]),
    ('LLM', 'AliLLM', [
        ('type', LAYER_COMMON),
        ('base_url', LAYER_COMMON),
        ('model_name', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('temperature', LAYER_MORE),
        ('max_tokens', LAYER_MORE),
        ('top_p', LAYER_MORE),
        ('frequency_penalty', LAYER_MORE),
    ]),
    ('LLM', 'AliAppLLM', [
        ('type', LAYER_COMMON),
        ('base_url', LAYER_COMMON),
        ('app_id', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('is_no_prompt', LAYER_MORE),
        ('ali_memory_id', LAYER_MORE),
    ]),
    ('LLM', 'DoubaoLLM', [
        ('type', LAYER_COMMON),
        ('base_url', LAYER_COMMON),
        ('model_name', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
    ]),
    ('LLM', 'DeepSeekLLM', [
        ('type', LAYER_COMMON),
        ('model_name', LAYER_COMMON),
        ('url', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
    ]),
    ('LLM', 'ChatGLMLLM', [
        ('type', LAYER_COMMON),
        ('model_name', LAYER_COMMON),
        ('url', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
    ]),
    ('LLM', 'OllamaLLM', [
        ('type', LAYER_COMMON),
        ('model_name', LAYER_COMMON),
        ('base_url', LAYER_COMMON),
    ]),
    ('LLM', 'DifyLLM', [
        ('type', LAYER_COMMON),
        ('base_url', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('mode', LAYER_COMMON),
    ]),
    ('LLM', 'GeminiLLM', [
        ('type', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('model_name', LAYER_COMMON),
        ('http_proxy', LAYER_MORE),
        ('https_proxy', LAYER_MORE),
    ]),
    ('LLM', 'CozeLLM', [
        ('type', LAYER_COMMON),
        ('bot_id', LAYER_COMMON),
        ('user_id', LAYER_COMMON),
        ('personal_access_token', LAYER_COMMON),
    ]),
    ('LLM', 'VolcesAiGatewayLLM', [
        ('type', LAYER_COMMON),
        ('base_url', LAYER_COMMON),
        ('model_name', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
    ]),
    ('LLM', 'LMStudioLLM', [
        ('type', LAYER_COMMON),
        ('model_name', LAYER_COMMON),
        ('url', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
    ]),
    ('LLM', 'HomeAssistant', [
        ('type', LAYER_COMMON),
        ('base_url', LAYER_COMMON),
        ('agent_id', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
    ]),
    ('LLM', 'FastgptLLM', [
        ('type', LAYER_COMMON),
        ('base_url', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('variables.k', LAYER_MORE),
        ('variables.k2', LAYER_MORE),
    ]),
    ('LLM', 'XinferenceLLM', [
        ('type', LAYER_COMMON),
        ('model_name', LAYER_COMMON),
        ('base_url', LAYER_COMMON),
    ]),
    ('LLM', 'XinferenceSmallLLM', [
        ('type', LAYER_COMMON),
        ('model_name', LAYER_COMMON),
        ('base_url', LAYER_COMMON),
    ]),
    ('VLLM', 'ChatGLMVLLM', [
        ('type', LAYER_COMMON),
        ('model_name', LAYER_COMMON),
        ('url', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
    ]),
    ('VLLM', 'QwenVLVLLM', [
        ('type', LAYER_COMMON),
        ('model_name', LAYER_COMMON),
        ('url', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
    ]),
    ('VLLM', 'XunfeiSparkLLM', [
        ('type', LAYER_COMMON),
        ('base_url', LAYER_COMMON),
        ('model_name', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
    ]),
    ('TTS', 'EdgeTTS', [
        ('type', LAYER_COMMON),
        ('voice', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
    ]),
    ('TTS', 'DoubaoTTS', [
        ('type', LAYER_COMMON),
        ('api_url', LAYER_COMMON),
        ('voice', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
        ('authorization', LAYER_COMMON),
        ('appid', LAYER_COMMON),
        ('access_token', LAYER_COMMON),
        ('cluster', LAYER_COMMON),
        ('speed_ratio', LAYER_MORE),
        ('volume_ratio', LAYER_MORE),
        ('pitch_ratio', LAYER_MORE),
    ]),
    ('TTS', 'HuoshanDoubleStreamTTS', [
        ('type', LAYER_COMMON),
        ('ws_url', LAYER_COMMON),
        ('appid', LAYER_COMMON),
        ('access_token', LAYER_COMMON),
        ('resource_id', LAYER_COMMON),
        ('speaker', LAYER_COMMON),
        ('enable_ws_reuse', LAYER_MORE),
        ('audio_params.speech_rate', LAYER_MORE),
        ('audio_params.loudness_rate', LAYER_MORE),
        ('additions.post_process.pitch', LAYER_MORE),
    ]),
    ('TTS', 'HuoshanDoubleStreamTTSV2', [
        ('type', LAYER_COMMON),
        ('ws_url', LAYER_COMMON),
        ('appid', LAYER_COMMON),
        ('access_token', LAYER_COMMON),
        ('resource_id', LAYER_COMMON),
        ('speaker', LAYER_COMMON),
        ('enable_ws_reuse', LAYER_MORE),
        ('audio_params.speech_rate', LAYER_MORE),
        ('audio_params.loudness_rate', LAYER_MORE),
        ('additions.post_process.pitch', LAYER_MORE),
    ]),
    ('TTS', 'CosyVoiceSiliconflow', [
        ('type', LAYER_COMMON),
        ('model', LAYER_COMMON),
        ('voice', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
        ('access_token', LAYER_COMMON),
        ('response_format', LAYER_COMMON),
    ]),
    ('TTS', 'CozeCnTTS', [
        ('type', LAYER_COMMON),
        ('voice', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
        ('access_token', LAYER_COMMON),
        ('response_format', LAYER_COMMON),
    ]),
    ('TTS', 'VolcesAiGatewayTTS', [
        ('type', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('api_url', LAYER_COMMON),
        ('model', LAYER_COMMON),
        ('voice', LAYER_COMMON),
        ('speed', LAYER_MORE),
        ('output_dir', LAYER_MORE),
    ]),
    ('TTS', 'FishSpeech', [
        ('type', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
        ('response_format', LAYER_COMMON),
        ('reference_id', LAYER_COMMON),
        ('reference_audio', LAYER_COMMON),
        ('reference_text', LAYER_COMMON),
        ('normalize', LAYER_MORE),
        ('max_new_tokens', LAYER_MORE),
        ('chunk_length', LAYER_MORE),
        ('top_p', LAYER_MORE),
        ('repetition_penalty', LAYER_MORE),
        ('temperature', LAYER_MORE),
        ('streaming', LAYER_MORE),
        ('use_memory_cache', LAYER_MORE),
        ('seed', LAYER_MORE),
        ('channels', LAYER_COMMON),
        ('rate', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('api_url', LAYER_COMMON),
    ]),
    ('TTS', 'GPT_SOVITS_V2', [
        ('type', LAYER_COMMON),
        ('url', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
        ('text_lang', LAYER_COMMON),
        ('ref_audio_path', LAYER_COMMON),
        ('prompt_text', LAYER_COMMON),
        ('prompt_lang', LAYER_COMMON),
        ('top_k', LAYER_MORE),
        ('top_p', LAYER_MORE),
        ('temperature', LAYER_MORE),
        ('text_split_method', LAYER_MORE),
        ('batch_size', LAYER_MORE),
        ('batch_threshold', LAYER_MORE),
        ('split_bucket', LAYER_MORE),
        ('return_fragment', LAYER_MORE),
        ('speed_factor', LAYER_MORE),
        ('streaming_mode', LAYER_MORE),
        ('seed', LAYER_MORE),
        ('parallel_infer', LAYER_MORE),
        ('repetition_penalty', LAYER_MORE),
        ('aux_ref_audio_paths', LAYER_COMMON),
    ]),
    ('TTS', 'GPT_SOVITS_V3', [
        ('type', LAYER_COMMON),
        ('url', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
        ('text_language', LAYER_COMMON),
        ('refer_wav_path', LAYER_COMMON),
        ('prompt_language', LAYER_COMMON),
        ('prompt_text', LAYER_COMMON),
        ('top_k', LAYER_MORE),
        ('top_p', LAYER_MORE),
        ('temperature', LAYER_MORE),
        ('cut_punc', LAYER_MORE),
        ('speed', LAYER_MORE),
        ('inp_refs', LAYER_COMMON),
        ('sample_steps', LAYER_MORE),
        ('if_sr', LAYER_MORE),
    ]),
    ('TTS', 'MinimaxTTSHTTPStream', [
        ('type', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
        ('group_id', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('model', LAYER_COMMON),
        ('voice_id', LAYER_COMMON),
    ]),
    ('TTS', 'AliyunTTS', [
        ('type', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
        ('appkey', LAYER_COMMON),
        ('token', LAYER_COMMON),
        ('voice', LAYER_COMMON),
        ('access_key_id', LAYER_COMMON),
        ('access_key_secret', LAYER_COMMON),
    ]),
    ('TTS', 'AliyunStreamTTS', [
        ('type', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
        ('appkey', LAYER_COMMON),
        ('token', LAYER_COMMON),
        ('voice', LAYER_COMMON),
        ('access_key_id', LAYER_COMMON),
        ('access_key_secret', LAYER_COMMON),
        ('host', LAYER_COMMON),
    ]),
    ('TTS', 'TencentTTS', [
        ('type', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
        ('appid', LAYER_COMMON),
        ('secret_id', LAYER_COMMON),
        ('secret_key', LAYER_COMMON),
        ('region', LAYER_COMMON),
        ('voice', LAYER_COMMON),
    ]),
    ('TTS', 'TTS302AI', [
        ('type', LAYER_COMMON),
        ('api_url', LAYER_COMMON),
        ('authorization', LAYER_COMMON),
        ('voice', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
        ('access_token', LAYER_COMMON),
    ]),
    ('TTS', 'OpenAITTS', [
        ('type', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('api_url', LAYER_COMMON),
        ('model', LAYER_COMMON),
        ('voice', LAYER_COMMON),
        ('speed', LAYER_MORE),
        ('output_dir', LAYER_MORE),
    ]),
    ('TTS', 'CustomTTS', [
        ('type', LAYER_COMMON),
        ('method', LAYER_COMMON),
        ('url', LAYER_COMMON),
        ('params.input', LAYER_MORE),
        ('params.response_format', LAYER_COMMON),
        ('params.download_format', LAYER_MORE),
        ('params.voice', LAYER_COMMON),
        ('params.lang_code', LAYER_MORE),
        ('params.return_download_link', LAYER_MORE),
        ('params.speed', LAYER_MORE),
        ('params.stream', LAYER_MORE),
        ('headers', LAYER_MORE),
        ('format', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
    ]),
    ('TTS', 'PaddleSpeechTTS', [
        ('type', LAYER_COMMON),
        ('protocol', LAYER_COMMON),
        ('url', LAYER_COMMON),
        ('spk_id', LAYER_COMMON),
        ('speed', LAYER_MORE),
        ('volume', LAYER_MORE),
        ('save_path', LAYER_MORE),
    ]),
    ('TTS', 'IndexStreamTTS', [
        ('type', LAYER_COMMON),
        ('api_url', LAYER_COMMON),
        ('audio_format', LAYER_COMMON),
        ('voice', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
    ]),
    ('TTS', 'AliBLTTS', [
        ('type', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('model', LAYER_COMMON),
        ('voice', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
    ]),
    ('TTS', 'XunFeiTTS', [
        ('type', LAYER_COMMON),
        ('api_url', LAYER_COMMON),
        ('app_id', LAYER_COMMON),
        ('api_secret', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
        ('voice', LAYER_COMMON),
        ('output_dir', LAYER_MORE),
    ]),
    ('TTS', 'MlxTTS', [
        ('type', LAYER_COMMON),
        ('url', LAYER_COMMON),
        ('speed', LAYER_MORE),
        ('output_dir', LAYER_MORE),
        ('split_sentences', LAYER_MORE),
    ]),
    ('TTS', 'MlxStreamTTS', [
        ('type', LAYER_COMMON),
        ('url', LAYER_COMMON),
        ('speed', LAYER_MORE),
        ('output_dir', LAYER_MORE),
        ('split_sentences', LAYER_MORE),
        ('tts_timeout', LAYER_MORE),
    ]),
    ('TTS', 'MlxWanwanStreamTTS', [
        ('type', LAYER_COMMON),
        ('url', LAYER_COMMON),
        ('speed', LAYER_MORE),
        ('output_dir', LAYER_MORE),
        ('split_sentences', LAYER_MORE),
        ('tts_timeout', LAYER_MORE),
        ('voice', LAYER_COMMON),
        ('ref_audio', LAYER_COMMON),
        ('ref_text', LAYER_COMMON),
    ]),
    ('TTS', 'MlxMengwaStreamTTS', [
        ('type', LAYER_COMMON),
        ('url', LAYER_COMMON),
        ('speed', LAYER_MORE),
        ('output_dir', LAYER_MORE),
        ('split_sentences', LAYER_MORE),
        ('tts_timeout', LAYER_MORE),
        ('voice', LAYER_COMMON),
        ('ref_audio', LAYER_COMMON),
        ('ref_text', LAYER_COMMON),
    ]),
    ('Memory', 'mem0ai', [
        ('type', LAYER_COMMON),
        ('api_key', LAYER_COMMON),
    ]),
    ('Memory', 'powermem', [
        ('type', LAYER_COMMON),
        ('enable_user_profile', LAYER_MORE),
        ('llm.provider', LAYER_COMMON),
        ('llm.config.api_key', LAYER_COMMON),
        ('llm.config.model', LAYER_COMMON),
        ('embedder.provider', LAYER_COMMON),
        ('embedder.config.api_key', LAYER_COMMON),
        ('embedder.config.model', LAYER_COMMON),
        ('embedder.config.openai_base_url', LAYER_MORE),
        ('vector_store.provider', LAYER_COMMON),
        ('vector_store.config', LAYER_MORE),
    ]),
    ('Memory', 'mem_local_short', [
        ('type', LAYER_COMMON),
        ('llm', LAYER_COMMON),
    ]),
]


#: 六个类目的一句话职责（类目卡的 desc；§3 合计表的一句话职责是整域的，
#: 这里细到族）。
CATEGORY_DESC = {
    "VAD": "语音活动检测：判断「有人在说话」，决定断句起点。",
    "ASR": "语音识别：把说话变成文字。",
    "LLM": "语言模型：生成回复、调用工具。",
    "VLLM": "视觉语言模型：看懂摄像头画面。",
    "TTS": "语音合成：把回复变成声音。本类目条目最多，建议用搜索。",
    "Memory": "记忆：跨会话保留用户与对话内容。",
}


# ---------------------------------------------------------------------------
# 插件与工具（32 字段；§7 表逐行照抄）
#
# R1：意图编排与工具面——Intent 子树（意图即工具编排器）、plugins.*、
# 外部 MCP、工具调用参数。分层：27 常用 / 5 更多设置（§3 合计表）。
#
# 为什么插件库与**引擎库同构**（§5.1 / §7 移交注记 2）：
#
# 1. 两者形状相同——都是一组「可替换组件」，每个组件有一堆字段、有启用/未选
#    中的差别。旧页面的痛点也相同：未启用插件的参数被折进 `<details>` 黑洞，
#    与「未选中引擎看不见」是同一个病。
# 2. 卡内分层的机械依据也相同：§7 末尾的**接入字段名字集**（`api_key` /
#    `base_url` / `provider` / `functions`…）已经被下表吸收——表里的「层」列
#    就是最终裁决，页面不再自己算一遍名字集。
#
# 三个字段的落位说明：
#
# - `selected_module.Intent` 属**本域**而非引擎域（§7 表头行；
#   `ENGINE_CATEGORIES` 里没有 Intent）。它是意图分支的选择器。
# - `tool_call_timeout` / `mcp_endpoint` 是**域内散字段**（更多设置）：改了就
#   重启生效，没有「选中」这个状态（§5.3 的口径）。
# - **意图分支各自成卡**（§7 移交注记 1）：`function_call` 与 `intent_llm`
#   两份启用清单同时可见、各在自己的卡里，切换 `selected_module.Intent`
#   不再有「清单消失」的问题。`nointent` 分支没有 functions，只有 type。
# ---------------------------------------------------------------------------
TOOLS = DomainSchema(
    slug="tools",
    label="插件与工具",
    groups=[
        Group(
            id="intent-selector",
            title="🎯 当前生效意图引擎",
            desc="意图即工具编排器：它决定设备用哪条路挑插件。切换即改 "
                 "selected_module.Intent，会进脏列表的 [选择] 组；两份启用清单"
                 "都可见（在下面的分支卡里），切走不会让清单消失。",
            fields=[
                Field("selected_module.Intent", "当前生效意图引擎", "select",
                      LAYER_COMMON,
                      ""),
            ],
        ),
        Group(
            id="tool-calls",
            title="🔧 工具调用",
            desc="与选哪条意图路无关的公共参数：超时与外部 MCP 接入点。",
            fields=[
                Field("tool_call_timeout", "工具调用超时(秒)", "number",
                      LAYER_MORE, "插件/工具单次调用的等待上限"),
                Field("mcp_endpoint", "外部 MCP 接入点", "text", LAYER_MORE,
                      "接入外部 MCP 服务的地址（WebSocket）"),
            ],
        ),
        Group(
            id="intent-function-call",
            title="⚡ function_call 分支",
            desc="把插件清单直接交给 LLM 做函数调用：模型自己决定调哪个插件。"
                 "启用的插件在「全部插件」库里算生效（重启后生效）。",
            fields=[
                Field("Intent.function_call.type", "类型", "text", LAYER_COMMON),
                Field("Intent.function_call.functions", "启用的插件清单", "list",
                      LAYER_COMMON,
                      "每项一个插件名；这就是「插件已启用」的定义"),
            ],
        ),
        Group(
            id="intent-nointent",
            title="🚫 nointent 分支",
            desc="不做意图识别，也不调插件：纯聊天。",
            fields=[
                Field("Intent.nointent.type", "类型", "text", LAYER_COMMON),
            ],
        ),
        Group(
            id="intent-intent-llm",
            title="🤖 intent_llm 分支",
            desc="先用一条 LLM 判定意图、再从清单里挑插件：比函数调用省 token。"
                 "它与 function_call 的清单**同时可见**，切换不影响。",
            fields=[
                Field("Intent.intent_llm.type", "类型", "text", LAYER_COMMON),
                Field("Intent.intent_llm.llm", "意图识别 LLM", "text",
                      LAYER_COMMON,
                      "做意图判定的引擎名（按名字引用 LLM 引擎）"),
                Field("Intent.intent_llm.functions", "启用的插件清单", "list",
                      LAYER_COMMON, "每项一个插件名"),
            ],
        ),
        Group(
            id="plugins",
            title="🧩 全部插件",
            desc="每个插件的接入参数。卡片列表与引擎库同构：跨插件搜索、真折叠，"
                 "未启用插件的参数不再折进黑洞——它和已启用的一样可编、可存。",
            fields=[
                # plugins.get_weather（3；旧归属：意图与插件·已启用）
                Field("plugins.get_weather.api_host", "天气接口域名", "text",
                      LAYER_COMMON, "和风天气 API 的域名"),
                Field("plugins.get_weather.api_key", "天气 API 密钥", "text",
                      LAYER_COMMON),
                Field("plugins.get_weather.default_location", "默认城市", "text",
                      LAYER_COMMON, "用户没说城市时用这个"),
                # plugins.get_news_from_chinanews（4；旧归属：意图与插件·未启用折叠）
                Field("plugins.get_news_from_chinanews.default_rss_url", "默认 RSS",
                      "text", LAYER_COMMON),
                Field("plugins.get_news_from_chinanews.society_rss_url", "社会新闻 RSS",
                      "text", LAYER_COMMON),
                Field("plugins.get_news_from_chinanews.world_rss_url", "国际新闻 RSS",
                      "text", LAYER_COMMON),
                Field("plugins.get_news_from_chinanews.finance_rss_url", "财经新闻 RSS",
                      "text", LAYER_COMMON),
                # plugins.get_news_from_newsnow（2；旧归属：意图与插件·未启用折叠）
                Field("plugins.get_news_from_newsnow.url", "接口地址", "text",
                      LAYER_COMMON),
                Field("plugins.get_news_from_newsnow.news_sources", "新闻源", "text",
                      LAYER_COMMON, "分号分隔的源名列表"),
                # plugins.home_assistant（3；旧归属：意图与插件·未启用折叠）
                Field("plugins.home_assistant.devices[i]", "设备列表", "list",
                      LAYER_COMMON, "每项格式：房间,名称,实体 ID"),
                Field("plugins.home_assistant.base_url", "HA 地址", "text",
                      LAYER_COMMON, "Home Assistant 的访问地址"),
                Field("plugins.home_assistant.api_key", "HA 访问令牌", "text",
                      LAYER_COMMON),
                # plugins.play_music（3；旧归属：意图与插件·未启用折叠）
                Field("plugins.play_music.music_dir", "音乐目录", "text",
                      LAYER_COMMON, "./music"),
                Field("plugins.play_music.music_ext[i]", "音频扩展名", "list",
                      LAYER_MORE, "每项如 .mp3"),
                Field("plugins.play_music.refresh_time", "刷新间隔(秒)", "number",
                      LAYER_MORE, "扫描音乐目录的周期"),
                # plugins.search_from_ragflow（4；旧归属：意图与插件·未启用折叠）
                Field("plugins.search_from_ragflow.description", "用途描述", "text",
                      LAYER_COMMON, "写给模型看的：什么时候该用这个插件"),
                Field("plugins.search_from_ragflow.base_url", "RAGFlow 地址", "text",
                      LAYER_COMMON),
                Field("plugins.search_from_ragflow.api_key", "RAGFlow 密钥", "text",
                      LAYER_COMMON),
                Field("plugins.search_from_ragflow.dataset_ids[i]", "知识库 ID", "list",
                      LAYER_COMMON, "每项一个 dataset id"),
                # plugins.web_search（4；旧归属：意图与插件·未启用折叠）
                Field("plugins.web_search.provider", "搜索提供方", "text",
                      LAYER_COMMON, "metaso / tavily 等"),
                Field("plugins.web_search.description", "用途描述", "text",
                      LAYER_COMMON, "写给模型看的：什么时候该用这个插件"),
                Field("plugins.web_search.max_results", "最大结果数", "number",
                      LAYER_MORE),
                Field("plugins.web_search.api_key", "搜索 API 密钥", "text",
                      LAYER_COMMON),
            ],
        ),
    ],
)


def _engine_groups():
    """引擎域的六个类目卡：卡内是该族**全部引擎**的字段并集（点号全路径）。

    为什么用全路径而不是占位形态：条目要被脏分桶 / hash 锚点直接消费；
    占位形态在每一处都要再解一次，而「再解一次」就是第二把尺子。

    卡的 ``fields`` 是**表里列过的**引擎字段。树上还有表里没列的键（本机自加
    引擎的新字段、上游新增的键）：它们不在这里，由域页脚本按配置树补渲染
    （§9 规则 1「遍历配置树渲染」）——表决定分层，树决定存在。
    """
    groups = []
    per_cat = {}
    for cat, name, fields in ENGINE_ENTRIES:
        per_cat.setdefault(cat, []).append((name, fields))
    for cat in ENGINE_CATEGORIES:
        entries = per_cat.get(cat, [])
        fs = []
        for name, fields in entries:
            for tail, layer in fields:
                fs.append(Field(f"{cat}.{name}.{tail}", tail, "text", layer))
        groups.append(Group(
            id=cat.lower(),
            title=f"{cat} · {len(entries)} 条 / {len(fs)} 字段",
            desc=CATEGORY_DESC[cat],
            fields=fs,
        ))
    return groups


ENGINE = DomainSchema(
    slug="engine",
    label="引擎",
    groups=[
        Group(
            id="selectors",
            title="🎯 当前生效",
            desc="六族各自当前生效的引擎。切换即改 selected_module.*，"
                 "会进脏列表的 [选择] 组；改错了切回原值该条自动消失。",
            fields=list(ENGINE_SELECTORS),
        ),
        Group(
            id="globals",
            title="🔧 引擎全局参数",
            desc="不属于任何一条引擎的公共参数。",
            fields=list(ENGINE_GLOBALS),
        ),
    ] + _engine_groups(),
)

# ---------------------------------------------------------------------------
# 设备（17 字段；§7 设备表逐行照抄）+ 运行时面（§4.4）
#
# R1：下发给设备的连接载荷（provisioning）、设备认证、hello 协商、发往设备的
# 节奏与时区——「设备怎么连上、连上后怎么说话」。分层：7 常用 / 10 更多设置
# （§3 合计表）。
#
# 四块，hash id 用 ASCII kebab-case（§4.5）：
#   #provisioning  provisioning 载荷（发给设备的连接地址）
#   #auth          设备认证（启用开关、白名单、auth_key）
#   #hello         hello 协商（设备上报值覆写的那些）
#   #timing        发往设备的节奏与时区
#
# 三条落位说明（方案备注列直接落成字段属性，页面不写第二遍）：
#
# - **危险占位**：``server.auth_key`` 标危险。改它 = 全部设备 token 失效，
#   所以它在更多设置里、带危险视觉。**分级细节（三级 + 统一确认层）是 #30**
#   ——这里只声明「这是危险动作」，不做规则。
# - **设备协商值**（移交注记 3）：``xiaozhi.type/version/transport/
#   audio_params.format`` 标「设备协商值，通常勿改」——hello 握手用设备上报值
#   覆写，改这里不会改变设备实际怎么连；不标的话用户会以为该改它们。
# - ``server.auth.allowed_devices[i]`` 照表写 ``[i]`` 形态（list 记 1 个字段）：
#   数组元素路径是 ``server.auth.allowed_devices.0``，表是计数口径，
#   页面在入口处归一（同 ``wakeup_words``）。
#
# 运行时面（在线设备 / 固件库 / SmartConfig）**不是配置字段**：它们没有可保存
# 的值，是设备的运行状态与物理副作用。所以它们在 ``runtime_panels`` 里，
# 不参与 17 这个数，但 id 与分组 id 同一套锚点规则。
# ---------------------------------------------------------------------------

#: 摄像头入口（§4.4）——**单一事实源在 ``config_shell.CAMERA_PAGE``**
#: （它同时喂侧栏/顶栏的家族互链与摄像头页本身），这里不复制一份：
#: 文案或路径在壳与域表各存一份就会分叉，而分叉后没有测试会红。
#:
#: 这里只留一条约束说明：入口是**常驻的**（设备离线也在），它由壳渲染，
#: 不在在线设备列表里——在线设备行旁的快捷入口（仅带摄像头能力的设备）
#: 是可选增强，不是本入口的载体。

#: 设备域的**运行时面**面板（§4.4 / AC 2）：在线设备 / 固件库 / SmartConfig。
#:
#: 三个面板与 17 个配置字段同页：设备域的职责是「看设备 + 配设备」，
#: 把运行时面留在旧的分组页就是把同一件事劈成两页。
#:
#: ``id`` 是 §4.5 定案的深链锚点（``#online-devices`` / ``#firmware`` /
#: ``#smartconfig``）；``api`` 列的是面板背后的**既有**接口——本票只做迁移，
#: 不新增后端（旧页面的 ``sendSmartConfig()`` 一整套行为原样搬过来）。
DEVICES_RUNTIME = [
    {
        "id": "online-devices",
        "title": "📡 在线设备",
        "icon": "📡",
        "desc": "当前通过 WebSocket 连接到本服务器的设备。点「重启并检查更新」，设备重启后"
                "会自动向服务器检查 OTA：固件库里有更新版本就自动下载刷入。"
                "设备空闲时会断开连接，列表为空时先唤醒设备（说唤醒词或按对话键）。",
        "api": ["/xiaozhi/config/api/devices", "/xiaozhi/ota/reboot"],
    },
    {
        "id": "firmware",
        "title": "💾 固件库",
        "icon": "💾",
        "desc": "固件文件存放于服务器 data/bin/ 目录，命名格式「型号_版本.bin」"
                "（如 zhengchen-minicam_2.4.3.bin）。设备重启检查更新时，"
                "库中版本高于设备当前版本即自动升级。",
        "api": ["/xiaozhi/config/api/firmware",
                "/xiaozhi/config/api/firmware/upload",
                "/xiaozhi/config/api/firmware/delete"],
    },
    {
        "id": "smartconfig",
        "title": "📶 SmartConfig 设备配网",
        "icon": "📶",
        "badge": "ESP-TOUCH v2",
        "desc": "⚠️ 操作顺序：① 先让设备处于配网模式（无 Wi-Fi 配置时开机自动进入，"
                "开机约 5 秒后开始监听，窗口 60 秒）→ ② 在窗口内点「开始广播」。"
                "设备已连着 Wi-Fi 时广播无效（它不会监听）；广播 AES 加密，约 30 秒。",
        "api": ["/xiaozhi/config/api/smartconfig",
                "/xiaozhi/config/api/local-wifi"],
    },
]

DEVICES = DomainSchema(
    slug="devices",
    label="设备",
    groups=[
        Group(
            id="provisioning",
            title="🔌 发给设备的连接载荷",
            desc="设备开机连接时拿到的服务器地址（provisioning）。设备连不上时，"
                 "先核对这里；改完要重启服务并重启设备。",
            fields=[
                Field("server.websocket", "WebSocket 地址", "text", LAYER_COMMON,
                      "provisioning 载荷：设备语音连接的主地址（备用地址在下面一行）"),
                Field("server.mqtt_gateway", "MQTT 网关", "text", LAYER_COMMON,
                      "provisioning 载荷：走 MQTT 时下发"),
                Field("server.mqtt_signature_key", "MQTT 签名密钥", "text",
                      LAYER_COMMON, "provisioning 载荷：MQTT 接入签名用"),
                Field("server.udp_gateway", "UDP 网关", "text", LAYER_COMMON,
                      "provisioning 载荷：走 UDP 时下发"),
                Field("server.websocket_backup", "备用 WebSocket 地址", "text",
                      LAYER_COMMON, "provisioning 载荷：主地址不可用时的备用地址"),
            ],
        ),
        Group(
            id="auth",
            title="🔐 设备认证",
            desc="OTA / WebSocket 连接认证。token 由 auth_key 签名生成；"
                 "白名单内的设备免 token 校验。",
            fields=[
                Field("server.auth.enabled", "启用认证", "bool", LAYER_COMMON,
                      "关闭 = 任何设备都能连（仅限可信局域网）"),
                Field("server.auth.allowed_devices[i]", "白名单设备 ID", "list",
                      LAYER_COMMON, "白名单内免 token 校验；每项一个设备 ID"),
                Field("server.auth_key", "认证签名密钥", "text", LAYER_MORE,
                      "给设备 token 签名用。区分大小写，改后所有已发 token 立即失效。",
                      danger=True,
                      danger_note="危险：改 = 全部设备 token 失效（所有设备要重新配网/重新验证）"),
            ],
        ),
        Group(
            id="hello",
            title="📇 hello 协商（设备协商值）",
            desc="设备连接时上报的握手参数。**这些是协商值：服务端按设备上报的值工作**，"
                 "在这里改通常不会改变设备的实际行为——改之前先确认你确实要覆盖它。",
            fields=[
                Field("xiaozhi.type", "连接类型", "text", LAYER_MORE,
                      "设备协商值，通常勿改"),
                Field("xiaozhi.version", "协议版本", "number", LAYER_MORE,
                      "设备协商值，通常勿改"),
                Field("xiaozhi.transport", "传输方式", "text", LAYER_MORE,
                      "设备协商值，通常勿改"),
                Field("xiaozhi.audio_params.format", "音频格式", "text",
                      LAYER_MORE, "设备协商值，通常勿改"),
                Field("xiaozhi.audio_params.sample_rate", "采样率", "number",
                      LAYER_MORE, "hello 协商的音频采样率"),
                Field("xiaozhi.audio_params.channels", "声道数", "number",
                      LAYER_MORE, "hello 协商的声道数"),
                Field("xiaozhi.audio_params.frame_duration", "帧时长(ms)", "number",
                      LAYER_MORE, "hello 协商的音频帧时长"),
            ],
        ),
        Group(
            id="timing",
            title="⏱ 发往设备的节奏与时区",
            desc="服务端向设备发消息的节奏，以及时区偏移。",
            fields=[
                Field("server.timezone_offset", "时区偏移(小时)", "number",
                      LAYER_MORE, "UTC 偏移，如东八区填 8。影响发往设备的时间语义"),
                Field("tts_audio_send_delay", "TTS 音频发送延迟(秒)", "number",
                      LAYER_MORE, "每句 TTS 音频之间的间隔（网络差时调大）"),
            ],
        ),
    ],
    runtime_panels=list(DEVICES_RUNTIME),
)

#: 本票落地的域表（slug → schema）。五个域全部上线（#27 引擎 / #28 插件与工具 /
#: #29 设备）——占位页从此只属于「未来新增的域」。
DOMAIN_SCHEMAS = {d.slug: d for d in (DIALOGUE, ENGINE, TOOLS, DEVICES, SYSTEM)}
