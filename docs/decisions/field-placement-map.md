# 服务端配置页：字段落位裁决映射表

> 裁决票据：[#16 字段落位裁决：六域与两层](https://github.com/Naoki326/MyRobKid/issues/16)（上级地图 [#13](https://github.com/Naoki326/MyRobKid/issues/13)）。
> 输入：#14 的[字段盘点与归属规则](https://github.com/Naoki326/MyRobKid/blob/research/page-field-taxonomy/docs/research/page-field-taxonomy.md)（527 叶子字段，口径：`list` 记 1、`list[dict]` 展开元素键、空 `object` 记 1）。
> 本文是**裁决结论 + 旧→新映射表**，不是方案文档正文（正文由 #18 产出）。**不改代码、不改配置。**

## 一、定案规则（判据 4：任给字段，归属由规则唯一算出）

### R1 — 求属主（定域）：「把这个字段写死成常量，谁会受损？」

- **单个可替换组件受损**（换个引擎/插件就不再适用）→ 该组件所在的域。
- **编排者的策略受损**（与选哪个组件无关）→ 按编排者职责落 `系统` / `设备`。
- **多个互不相干的组件同时受损** → 编排者事实 → `系统`。（「多属主」延伸，替代 #14 草案的 R0 路径特判：
  `log.data_dir` 数据库里躺着数据库、固件库、TTS 输出——多属主 → 系统；而 `ASR.FunASR.model_dir` 只有 FunASR 受损——留在引擎卡内。
  引擎/插件卡内的 `model_dir`/`output_dir` 等**单属主路径不搬家**。)

### R2 — 定分层（常用 / 更多设置）：两问，任一为「是」→ 常用

1. **有没有人引用它？** 别的字段按名字引用它（`selected_module.*`→引擎块名、`Intent.*.functions`→插件名、`prompt_template`→`prompt`），
   或**设备靠它建立连接**（`server.websocket` 一族 provisioning 载荷）。共同点：**改错 = 静默失效**。
2. **它是不是「机器人是谁 / 子系统开不开」的内容？**（`prompt`、`wakeup_words`、`voiceprint.speakers`、`server.auth.enabled`…）——操作者的日常编辑面。

其余（阈值/地址/密钥/格式/调优旋钮）→ 更多设置。
**对 #14 的两处有据修正**：`close_connection_no_voice_time`、`server.timezone_offset` 原标「常用」，按定案 R2 是纯参数 → **更多设置**。

### 颗粒度：两层作用于「域内散字段」与「引擎/插件卡片内部」两处

卡片内：**接入字段 → 常用**（让该引擎/插件能跑起来的必填：端点、凭据、模型与声音标识、协议/语言选择），**调优字段 → 更多设置**（跑起来之后的参数）。
接入判定按字段名集合机械套用（名字集全文见 §六）。密钥在卡内属接入（启用该引擎的必填身份项；Frigate 先例同把凭据留默认层；#17 已定「已配置/未配置」显式标注兜底）。

## 二、一级骨架：五域 + 逃生口（对已定骨架的一次显式修正）

R1 套完后「高级」名下只剩 4 个纯日志字段（路径类已被多属主规则送进系统），撑不起侧栏一级域；
HA 已于 2026 年废除页面级 Advanced Mode（`7bea54851`），先例站在「不为高级单独设域」一边。
**修正：六域 → 五域 + 逃生口**——原始配置只读 JSON 保留为**页脚工具**（孤儿字段兜底视图），不是侧栏域。

| 域 | 职责一句话 | 字段数 | 常用 | 更多设置 |
|---|---|---|---|---|
| **对话与角色** | 提示词系、唤醒/退出词、声纹身份、上下文源 | 15 | 11 | 4 |
| **引擎** | VAD/ASR/LLM/VLLM/TTS/Memory 六族 + 选择器 + 引擎全局参数 | 450 | 302 | 148 |
| **插件与工具** | Intent 子树（意图即工具编排器）、plugins.*、外部 MCP、工具调用参数 | 32 | 27 | 5 |
| **设备** | 下发给设备的连接载荷（provisioning）、设备认证、hello 协商、发往设备的节奏与时区 | 17 | 7 | 10 |
| **系统** | 监听地址、会话/文件生命周期、传输保活、日志、跨用途路径（多属主事实） | 13 | 0 | 13 |
| **合计** | | **527** | **347** | **180** |

渲染注记（给 #18）：`系统` 域常用层为空——折叠区为空时不渲染折叠，全部字段平铺即可；「更多设置」是折叠容器不是第三个域。

## 三、旧→新全量映射（527 字段）

旧页归属沿用 #14 §2 口径。`无归宿（非选中）`= 只在「全部配置」只读 JSON 可见的非选中引擎/意图字段；`—` = 真孤儿。

### 对话与角色（15）

机器人是谁、说什么：提示词系、唤醒/退出词、声纹身份、上下文源。改这里的字段 = 改机器人的性格与语言行为。

| 字段 | 类型 | 旧页归属 | 层 | 备注 |
|---|---|---|---|---|
| `enable_wakeup_words_response_cache` | bool | 角色与对话 | 更多设置 |  |
| `enable_greeting` | bool | 角色与对话 | 常用 |  |
| `enable_stop_tts_notify` | bool | 角色与对话 | 常用 |  |
| `stop_tts_notify_voice` | str | 角色与对话 | 常用 |  |
| `exit_commands[i]` | list | 角色与对话 | 常用 |  |
| `wakeup_words[i]` | list | 角色与对话 | 常用 |  |
| `context_providers[i]` | list | 认证与设备 | 常用 |  |
| `voiceprint.url` | NoneType | 语音与音频 | 更多设置 |  |
| `voiceprint.speakers[i]` | list | — | 常用 |  |
| `voiceprint.similarity_threshold` | float | 语音与音频 | 更多设置 |  |
| `prompt` | str | 角色与对话 | 常用 |  |
| `prompt_template` | str | 角色与对话 | 更多设置 |  |
| `system_error_response` | str | 角色与对话 | 常用 |  |
| `end_prompt.enable` | bool | 角色与对话 | 常用 |  |
| `end_prompt.prompt` | str | 角色与对话 | 常用 |  |

### 引擎（450）

可替换组件：VAD/ASR/LLM/VLLM/TTS/Memory 六族 + 选择器 + 引擎全局参数。写死某字段成常量，受损的是该引擎的实现。

| 字段 | 类型 | 旧页归属 | 层 | 备注 |
|---|---|---|---|---|
| `tts_timeout` | int | 服务器/连接 | 更多设置 | 引擎全局参数（各 TTS 卡内同名键可覆盖） |
| `module_test.test_sentences[i]` | list | 语音与音频 | 更多设置 |  |
| `selected_module.VAD` | str | 模型引擎 | 常用 |  |
| `selected_module.ASR` | str | 模型引擎 | 常用 |  |
| `selected_module.LLM` | str | 模型引擎 | 常用 |  |
| `selected_module.VLLM` | str | 模型引擎 | 常用 |  |
| `selected_module.TTS` | str | 模型引擎 | 常用 |  |
| `selected_module.Memory` | str | 模型引擎 | 常用 |  |

**`VAD.SileroVAD`**（5 字段；旧归属：模型引擎）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `VAD.SileroVAD.type` | str | 常用 |  |
| `VAD.SileroVAD.threshold` | float | 更多设置 |  |
| `VAD.SileroVAD.threshold_low` | float | 更多设置 |  |
| `VAD.SileroVAD.model_dir` | str | 常用 |  |
| `VAD.SileroVAD.min_silence_duration_ms` | int | 更多设置 |  |

**`ASR.FunASR`**（4 字段；旧归属：模型引擎）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.FunASR.type` | str | 常用 |  |
| `ASR.FunASR.model_dir` | str | 常用 |  |
| `ASR.FunASR.output_dir` | str | 更多设置 |  |
| `ASR.FunASR.language` | str | 常用 |  |

**`LLM.ThirkingLLM`**（7 字段；旧归属：模型引擎）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.ThirkingLLM.type` | str | 常用 |  |
| `LLM.ThirkingLLM.model_name` | str | 常用 |  |
| `LLM.ThirkingLLM.url` | str | 常用 |  |
| `LLM.ThirkingLLM.api_key` | str | 常用 |  |
| `LLM.ThirkingLLM.max_tokens` | int | 更多设置 |  |
| `LLM.ThirkingLLM.temperature` | float | 更多设置 |  |
| `LLM.ThirkingLLM.reasoning_effort` | str | 更多设置 |  |

**`VLLM.ThirkingVLLM`**（4 字段；旧归属：模型引擎）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `VLLM.ThirkingVLLM.type` | str | 常用 |  |
| `VLLM.ThirkingVLLM.model_name` | str | 常用 |  |
| `VLLM.ThirkingVLLM.url` | str | 常用 |  |
| `VLLM.ThirkingVLLM.api_key` | str | 常用 |  |

**`TTS.MlxKafeiStreamTTS`**（9 字段；旧归属：模型引擎）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.MlxKafeiStreamTTS.type` | str | 常用 |  |
| `TTS.MlxKafeiStreamTTS.url` | str | 常用 |  |
| `TTS.MlxKafeiStreamTTS.speed` | float | 更多设置 |  |
| `TTS.MlxKafeiStreamTTS.output_dir` | str | 更多设置 |  |
| `TTS.MlxKafeiStreamTTS.split_sentences` | bool | 更多设置 |  |
| `TTS.MlxKafeiStreamTTS.tts_timeout` | int | 更多设置 |  |
| `TTS.MlxKafeiStreamTTS.voice` | str | 常用 |  |
| `TTS.MlxKafeiStreamTTS.ref_audio` | str | 常用 |  |
| `TTS.MlxKafeiStreamTTS.ref_text` | str | 常用 |  |

**`Memory.nomem`**（1 字段；旧归属：模型引擎）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `Memory.nomem.type` | str | 常用 |  |

**`ASR.FunASRServer`**（6 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.FunASRServer.type` | - | 常用 |  |
| `ASR.FunASRServer.host` | - | 常用 |  |
| `ASR.FunASRServer.port` | - | 常用 |  |
| `ASR.FunASRServer.is_ssl` | - | 常用 |  |
| `ASR.FunASRServer.api_key` | - | 常用 |  |
| `ASR.FunASRServer.output_dir` | - | 更多设置 |  |

**`ASR.SherpaASR`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.SherpaASR.type` | - | 常用 |  |
| `ASR.SherpaASR.model_dir` | - | 常用 |  |
| `ASR.SherpaASR.output_dir` | - | 更多设置 |  |
| `ASR.SherpaASR.model_type` | - | 常用 |  |

**`ASR.SherpaParaformerASR`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.SherpaParaformerASR.type` | - | 常用 |  |
| `ASR.SherpaParaformerASR.model_dir` | - | 常用 |  |
| `ASR.SherpaParaformerASR.output_dir` | - | 更多设置 |  |
| `ASR.SherpaParaformerASR.model_type` | - | 常用 |  |

**`ASR.DoubaoASR`**（7 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.DoubaoASR.type` | - | 常用 |  |
| `ASR.DoubaoASR.appid` | - | 常用 |  |
| `ASR.DoubaoASR.access_token` | - | 常用 |  |
| `ASR.DoubaoASR.cluster` | - | 常用 |  |
| `ASR.DoubaoASR.boosting_table_name` | - | 更多设置 |  |
| `ASR.DoubaoASR.correct_table_name` | - | 更多设置 |  |
| `ASR.DoubaoASR.output_dir` | - | 更多设置 |  |

**`ASR.DoubaoStreamASR`**（9 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.DoubaoStreamASR.type` | - | 常用 |  |
| `ASR.DoubaoStreamASR.appid` | - | 常用 |  |
| `ASR.DoubaoStreamASR.access_token` | - | 常用 |  |
| `ASR.DoubaoStreamASR.resource_id` | - | 常用 |  |
| `ASR.DoubaoStreamASR.boosting_table_name` | - | 更多设置 |  |
| `ASR.DoubaoStreamASR.correct_table_name` | - | 更多设置 |  |
| `ASR.DoubaoStreamASR.enable_multilingual` | - | 更多设置 |  |
| `ASR.DoubaoStreamASR.end_window_size` | - | 更多设置 |  |
| `ASR.DoubaoStreamASR.output_dir` | - | 更多设置 |  |

**`ASR.DoubaoStreamASRV2`**（9 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.DoubaoStreamASRV2.type` | - | 常用 |  |
| `ASR.DoubaoStreamASRV2.appid` | - | 常用 |  |
| `ASR.DoubaoStreamASRV2.access_token` | - | 常用 |  |
| `ASR.DoubaoStreamASRV2.resource_id` | - | 常用 |  |
| `ASR.DoubaoStreamASRV2.boosting_table_name` | - | 更多设置 |  |
| `ASR.DoubaoStreamASRV2.correct_table_name` | - | 更多设置 |  |
| `ASR.DoubaoStreamASRV2.enable_multilingual` | - | 更多设置 |  |
| `ASR.DoubaoStreamASRV2.end_window_size` | - | 更多设置 |  |
| `ASR.DoubaoStreamASRV2.output_dir` | - | 更多设置 |  |

**`ASR.TencentASR`**（5 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.TencentASR.type` | - | 常用 |  |
| `ASR.TencentASR.appid` | - | 常用 |  |
| `ASR.TencentASR.secret_id` | - | 常用 |  |
| `ASR.TencentASR.secret_key` | - | 常用 |  |
| `ASR.TencentASR.output_dir` | - | 更多设置 |  |

**`ASR.AliyunASR`**（6 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.AliyunASR.type` | - | 常用 |  |
| `ASR.AliyunASR.appkey` | - | 常用 |  |
| `ASR.AliyunASR.token` | - | 常用 |  |
| `ASR.AliyunASR.access_key_id` | - | 常用 |  |
| `ASR.AliyunASR.access_key_secret` | - | 常用 |  |
| `ASR.AliyunASR.output_dir` | - | 更多设置 |  |

**`ASR.AliyunStreamASR`**（8 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.AliyunStreamASR.type` | - | 常用 |  |
| `ASR.AliyunStreamASR.appkey` | - | 常用 |  |
| `ASR.AliyunStreamASR.token` | - | 常用 |  |
| `ASR.AliyunStreamASR.access_key_id` | - | 常用 |  |
| `ASR.AliyunStreamASR.access_key_secret` | - | 常用 |  |
| `ASR.AliyunStreamASR.host` | - | 常用 |  |
| `ASR.AliyunStreamASR.max_sentence_silence` | - | 更多设置 |  |
| `ASR.AliyunStreamASR.output_dir` | - | 更多设置 |  |

**`ASR.BaiduASR`**（6 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.BaiduASR.type` | - | 常用 |  |
| `ASR.BaiduASR.app_id` | - | 常用 |  |
| `ASR.BaiduASR.api_key` | - | 常用 |  |
| `ASR.BaiduASR.secret_key` | - | 常用 |  |
| `ASR.BaiduASR.dev_pid` | - | 常用 |  |
| `ASR.BaiduASR.output_dir` | - | 更多设置 |  |

**`ASR.OpenaiASR`**（5 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.OpenaiASR.type` | - | 常用 |  |
| `ASR.OpenaiASR.api_key` | - | 常用 |  |
| `ASR.OpenaiASR.base_url` | - | 常用 |  |
| `ASR.OpenaiASR.model_name` | - | 常用 |  |
| `ASR.OpenaiASR.output_dir` | - | 更多设置 |  |

**`ASR.GroqASR`**（5 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.GroqASR.type` | - | 常用 |  |
| `ASR.GroqASR.api_key` | - | 常用 |  |
| `ASR.GroqASR.base_url` | - | 常用 |  |
| `ASR.GroqASR.model_name` | - | 常用 |  |
| `ASR.GroqASR.output_dir` | - | 更多设置 |  |

**`ASR.VoskASR`**（3 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.VoskASR.type` | - | 常用 |  |
| `ASR.VoskASR.model_path` | - | 常用 |  |
| `ASR.VoskASR.output_dir` | - | 更多设置 |  |

**`ASR.Qwen3ASRFlash`**（8 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.Qwen3ASRFlash.type` | - | 常用 |  |
| `ASR.Qwen3ASRFlash.api_key` | - | 常用 |  |
| `ASR.Qwen3ASRFlash.base_url` | - | 常用 |  |
| `ASR.Qwen3ASRFlash.model_name` | - | 常用 |  |
| `ASR.Qwen3ASRFlash.output_dir` | - | 更多设置 |  |
| `ASR.Qwen3ASRFlash.enable_lid` | - | 更多设置 |  |
| `ASR.Qwen3ASRFlash.enable_itn` | - | 更多设置 |  |
| `ASR.Qwen3ASRFlash.context` | - | 更多设置 |  |

**`ASR.XunfeiStreamASR`**（8 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.XunfeiStreamASR.type` | - | 常用 |  |
| `ASR.XunfeiStreamASR.app_id` | - | 常用 |  |
| `ASR.XunfeiStreamASR.api_key` | - | 常用 |  |
| `ASR.XunfeiStreamASR.api_secret` | - | 常用 |  |
| `ASR.XunfeiStreamASR.domain` | - | 常用 |  |
| `ASR.XunfeiStreamASR.language` | - | 常用 |  |
| `ASR.XunfeiStreamASR.accent` | - | 常用 |  |
| `ASR.XunfeiStreamASR.output_dir` | - | 更多设置 |  |

**`ASR.AliyunBLStreamASR`**（12 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.AliyunBLStreamASR.type` | - | 常用 |  |
| `ASR.AliyunBLStreamASR.api_key` | - | 常用 |  |
| `ASR.AliyunBLStreamASR.model` | - | 常用 |  |
| `ASR.AliyunBLStreamASR.format` | - | 常用 |  |
| `ASR.AliyunBLStreamASR.sample_rate` | - | 更多设置 |  |
| `ASR.AliyunBLStreamASR.disfluency_removal_enabled` | - | 更多设置 |  |
| `ASR.AliyunBLStreamASR.semantic_punctuation_enabled` | - | 更多设置 |  |
| `ASR.AliyunBLStreamASR.max_sentence_silence` | - | 更多设置 |  |
| `ASR.AliyunBLStreamASR.multi_threshold_mode_enabled` | - | 更多设置 |  |
| `ASR.AliyunBLStreamASR.punctuation_prediction_enabled` | - | 更多设置 |  |
| `ASR.AliyunBLStreamASR.inverse_text_normalization_enabled` | - | 更多设置 |  |
| `ASR.AliyunBLStreamASR.output_dir` | - | 更多设置 |  |

**`LLM.AliLLM`**（8 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.AliLLM.type` | - | 常用 |  |
| `LLM.AliLLM.base_url` | - | 常用 |  |
| `LLM.AliLLM.model_name` | - | 常用 |  |
| `LLM.AliLLM.api_key` | - | 常用 |  |
| `LLM.AliLLM.temperature` | - | 更多设置 |  |
| `LLM.AliLLM.max_tokens` | - | 更多设置 |  |
| `LLM.AliLLM.top_p` | - | 更多设置 |  |
| `LLM.AliLLM.frequency_penalty` | - | 更多设置 |  |

**`LLM.AliAppLLM`**（6 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.AliAppLLM.type` | - | 常用 |  |
| `LLM.AliAppLLM.base_url` | - | 常用 |  |
| `LLM.AliAppLLM.app_id` | - | 常用 |  |
| `LLM.AliAppLLM.api_key` | - | 常用 |  |
| `LLM.AliAppLLM.is_no_prompt` | - | 更多设置 |  |
| `LLM.AliAppLLM.ali_memory_id` | - | 更多设置 |  |

**`LLM.DoubaoLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.DoubaoLLM.type` | - | 常用 |  |
| `LLM.DoubaoLLM.base_url` | - | 常用 |  |
| `LLM.DoubaoLLM.model_name` | - | 常用 |  |
| `LLM.DoubaoLLM.api_key` | - | 常用 |  |

**`LLM.DeepSeekLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.DeepSeekLLM.type` | - | 常用 |  |
| `LLM.DeepSeekLLM.model_name` | - | 常用 |  |
| `LLM.DeepSeekLLM.url` | - | 常用 |  |
| `LLM.DeepSeekLLM.api_key` | - | 常用 |  |

**`LLM.ChatGLMLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.ChatGLMLLM.type` | - | 常用 |  |
| `LLM.ChatGLMLLM.model_name` | - | 常用 |  |
| `LLM.ChatGLMLLM.url` | - | 常用 |  |
| `LLM.ChatGLMLLM.api_key` | - | 常用 |  |

**`LLM.OllamaLLM`**（3 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.OllamaLLM.type` | - | 常用 |  |
| `LLM.OllamaLLM.model_name` | - | 常用 |  |
| `LLM.OllamaLLM.base_url` | - | 常用 |  |

**`LLM.DifyLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.DifyLLM.type` | - | 常用 |  |
| `LLM.DifyLLM.base_url` | - | 常用 |  |
| `LLM.DifyLLM.api_key` | - | 常用 |  |
| `LLM.DifyLLM.mode` | - | 常用 |  |

**`LLM.GeminiLLM`**（5 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.GeminiLLM.type` | - | 常用 |  |
| `LLM.GeminiLLM.api_key` | - | 常用 |  |
| `LLM.GeminiLLM.model_name` | - | 常用 |  |
| `LLM.GeminiLLM.http_proxy` | - | 更多设置 |  |
| `LLM.GeminiLLM.https_proxy` | - | 更多设置 |  |

**`LLM.CozeLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.CozeLLM.type` | - | 常用 |  |
| `LLM.CozeLLM.bot_id` | - | 常用 |  |
| `LLM.CozeLLM.user_id` | - | 常用 |  |
| `LLM.CozeLLM.personal_access_token` | - | 常用 |  |

**`LLM.VolcesAiGatewayLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.VolcesAiGatewayLLM.type` | - | 常用 |  |
| `LLM.VolcesAiGatewayLLM.base_url` | - | 常用 |  |
| `LLM.VolcesAiGatewayLLM.model_name` | - | 常用 |  |
| `LLM.VolcesAiGatewayLLM.api_key` | - | 常用 |  |

**`LLM.LMStudioLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.LMStudioLLM.type` | - | 常用 |  |
| `LLM.LMStudioLLM.model_name` | - | 常用 |  |
| `LLM.LMStudioLLM.url` | - | 常用 |  |
| `LLM.LMStudioLLM.api_key` | - | 常用 |  |

**`LLM.HomeAssistant`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.HomeAssistant.type` | - | 常用 |  |
| `LLM.HomeAssistant.base_url` | - | 常用 |  |
| `LLM.HomeAssistant.agent_id` | - | 常用 |  |
| `LLM.HomeAssistant.api_key` | - | 常用 |  |

**`LLM.FastgptLLM`**（5 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.FastgptLLM.type` | - | 常用 |  |
| `LLM.FastgptLLM.base_url` | - | 常用 |  |
| `LLM.FastgptLLM.api_key` | - | 常用 |  |
| `LLM.FastgptLLM.variables.k` | - | 更多设置 |  |
| `LLM.FastgptLLM.variables.k2` | - | 更多设置 |  |

**`LLM.XinferenceLLM`**（3 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.XinferenceLLM.type` | - | 常用 |  |
| `LLM.XinferenceLLM.model_name` | - | 常用 |  |
| `LLM.XinferenceLLM.base_url` | - | 常用 |  |

**`LLM.XinferenceSmallLLM`**（3 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.XinferenceSmallLLM.type` | - | 常用 |  |
| `LLM.XinferenceSmallLLM.model_name` | - | 常用 |  |
| `LLM.XinferenceSmallLLM.base_url` | - | 常用 |  |

**`VLLM.ChatGLMVLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `VLLM.ChatGLMVLLM.type` | - | 常用 |  |
| `VLLM.ChatGLMVLLM.model_name` | - | 常用 |  |
| `VLLM.ChatGLMVLLM.url` | - | 常用 |  |
| `VLLM.ChatGLMVLLM.api_key` | - | 常用 |  |

**`VLLM.QwenVLVLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `VLLM.QwenVLVLLM.type` | - | 常用 |  |
| `VLLM.QwenVLVLLM.model_name` | - | 常用 |  |
| `VLLM.QwenVLVLLM.url` | - | 常用 |  |
| `VLLM.QwenVLVLLM.api_key` | - | 常用 |  |

**`VLLM.XunfeiSparkLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `VLLM.XunfeiSparkLLM.type` | - | 常用 |  |
| `VLLM.XunfeiSparkLLM.base_url` | - | 常用 |  |
| `VLLM.XunfeiSparkLLM.model_name` | - | 常用 |  |
| `VLLM.XunfeiSparkLLM.api_key` | - | 常用 |  |

**`TTS.EdgeTTS`**（3 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.EdgeTTS.type` | - | 常用 |  |
| `TTS.EdgeTTS.voice` | - | 常用 |  |
| `TTS.EdgeTTS.output_dir` | - | 更多设置 |  |

**`TTS.DoubaoTTS`**（11 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.DoubaoTTS.type` | - | 常用 |  |
| `TTS.DoubaoTTS.api_url` | - | 常用 |  |
| `TTS.DoubaoTTS.voice` | - | 常用 |  |
| `TTS.DoubaoTTS.output_dir` | - | 更多设置 |  |
| `TTS.DoubaoTTS.authorization` | - | 常用 |  |
| `TTS.DoubaoTTS.appid` | - | 常用 |  |
| `TTS.DoubaoTTS.access_token` | - | 常用 |  |
| `TTS.DoubaoTTS.cluster` | - | 常用 |  |
| `TTS.DoubaoTTS.speed_ratio` | - | 更多设置 |  |
| `TTS.DoubaoTTS.volume_ratio` | - | 更多设置 |  |
| `TTS.DoubaoTTS.pitch_ratio` | - | 更多设置 |  |

**`TTS.HuoshanDoubleStreamTTS`**（10 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.HuoshanDoubleStreamTTS.type` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTS.ws_url` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTS.appid` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTS.access_token` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTS.resource_id` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTS.speaker` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTS.enable_ws_reuse` | - | 更多设置 |  |
| `TTS.HuoshanDoubleStreamTTS.audio_params.speech_rate` | - | 更多设置 |  |
| `TTS.HuoshanDoubleStreamTTS.audio_params.loudness_rate` | - | 更多设置 |  |
| `TTS.HuoshanDoubleStreamTTS.additions.post_process.pitch` | - | 更多设置 |  |

**`TTS.HuoshanDoubleStreamTTSV2`**（10 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.HuoshanDoubleStreamTTSV2.type` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTSV2.ws_url` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTSV2.appid` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTSV2.access_token` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTSV2.resource_id` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTSV2.speaker` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTSV2.enable_ws_reuse` | - | 更多设置 |  |
| `TTS.HuoshanDoubleStreamTTSV2.audio_params.speech_rate` | - | 更多设置 |  |
| `TTS.HuoshanDoubleStreamTTSV2.audio_params.loudness_rate` | - | 更多设置 |  |
| `TTS.HuoshanDoubleStreamTTSV2.additions.post_process.pitch` | - | 更多设置 |  |

**`TTS.CosyVoiceSiliconflow`**（6 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.CosyVoiceSiliconflow.type` | - | 常用 |  |
| `TTS.CosyVoiceSiliconflow.model` | - | 常用 |  |
| `TTS.CosyVoiceSiliconflow.voice` | - | 常用 |  |
| `TTS.CosyVoiceSiliconflow.output_dir` | - | 更多设置 |  |
| `TTS.CosyVoiceSiliconflow.access_token` | - | 常用 |  |
| `TTS.CosyVoiceSiliconflow.response_format` | - | 常用 |  |

**`TTS.CozeCnTTS`**（5 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.CozeCnTTS.type` | - | 常用 |  |
| `TTS.CozeCnTTS.voice` | - | 常用 |  |
| `TTS.CozeCnTTS.output_dir` | - | 更多设置 |  |
| `TTS.CozeCnTTS.access_token` | - | 常用 |  |
| `TTS.CozeCnTTS.response_format` | - | 常用 |  |

**`TTS.VolcesAiGatewayTTS`**（7 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.VolcesAiGatewayTTS.type` | - | 常用 |  |
| `TTS.VolcesAiGatewayTTS.api_key` | - | 常用 |  |
| `TTS.VolcesAiGatewayTTS.api_url` | - | 常用 |  |
| `TTS.VolcesAiGatewayTTS.model` | - | 常用 |  |
| `TTS.VolcesAiGatewayTTS.voice` | - | 常用 |  |
| `TTS.VolcesAiGatewayTTS.speed` | - | 更多设置 |  |
| `TTS.VolcesAiGatewayTTS.output_dir` | - | 更多设置 |  |

**`TTS.FishSpeech`**（19 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.FishSpeech.type` | - | 常用 |  |
| `TTS.FishSpeech.output_dir` | - | 更多设置 |  |
| `TTS.FishSpeech.response_format` | - | 常用 |  |
| `TTS.FishSpeech.reference_id` | - | 常用 |  |
| `TTS.FishSpeech.reference_audio` | - | 常用 |  |
| `TTS.FishSpeech.reference_text` | - | 常用 |  |
| `TTS.FishSpeech.normalize` | - | 更多设置 |  |
| `TTS.FishSpeech.max_new_tokens` | - | 更多设置 |  |
| `TTS.FishSpeech.chunk_length` | - | 更多设置 |  |
| `TTS.FishSpeech.top_p` | - | 更多设置 |  |
| `TTS.FishSpeech.repetition_penalty` | - | 更多设置 |  |
| `TTS.FishSpeech.temperature` | - | 更多设置 |  |
| `TTS.FishSpeech.streaming` | - | 更多设置 |  |
| `TTS.FishSpeech.use_memory_cache` | - | 更多设置 |  |
| `TTS.FishSpeech.seed` | - | 更多设置 |  |
| `TTS.FishSpeech.channels` | - | 常用 |  |
| `TTS.FishSpeech.rate` | - | 常用 |  |
| `TTS.FishSpeech.api_key` | - | 常用 |  |
| `TTS.FishSpeech.api_url` | - | 常用 |  |

**`TTS.GPT_SOVITS_V2`**（21 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.GPT_SOVITS_V2.type` | - | 常用 |  |
| `TTS.GPT_SOVITS_V2.url` | - | 常用 |  |
| `TTS.GPT_SOVITS_V2.output_dir` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.text_lang` | - | 常用 |  |
| `TTS.GPT_SOVITS_V2.ref_audio_path` | - | 常用 |  |
| `TTS.GPT_SOVITS_V2.prompt_text` | - | 常用 |  |
| `TTS.GPT_SOVITS_V2.prompt_lang` | - | 常用 |  |
| `TTS.GPT_SOVITS_V2.top_k` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.top_p` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.temperature` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.text_split_method` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.batch_size` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.batch_threshold` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.split_bucket` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.return_fragment` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.speed_factor` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.streaming_mode` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.seed` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.parallel_infer` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.repetition_penalty` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.aux_ref_audio_paths` | - | 常用 |  |

**`TTS.GPT_SOVITS_V3`**（15 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.GPT_SOVITS_V3.type` | - | 常用 |  |
| `TTS.GPT_SOVITS_V3.url` | - | 常用 |  |
| `TTS.GPT_SOVITS_V3.output_dir` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V3.text_language` | - | 常用 |  |
| `TTS.GPT_SOVITS_V3.refer_wav_path` | - | 常用 |  |
| `TTS.GPT_SOVITS_V3.prompt_language` | - | 常用 |  |
| `TTS.GPT_SOVITS_V3.prompt_text` | - | 常用 |  |
| `TTS.GPT_SOVITS_V3.top_k` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V3.top_p` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V3.temperature` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V3.cut_punc` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V3.speed` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V3.inp_refs` | - | 常用 |  |
| `TTS.GPT_SOVITS_V3.sample_steps` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V3.if_sr` | - | 更多设置 |  |

**`TTS.MinimaxTTSHTTPStream`**（6 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.MinimaxTTSHTTPStream.type` | - | 常用 |  |
| `TTS.MinimaxTTSHTTPStream.output_dir` | - | 更多设置 |  |
| `TTS.MinimaxTTSHTTPStream.group_id` | - | 常用 |  |
| `TTS.MinimaxTTSHTTPStream.api_key` | - | 常用 |  |
| `TTS.MinimaxTTSHTTPStream.model` | - | 常用 |  |
| `TTS.MinimaxTTSHTTPStream.voice_id` | - | 常用 |  |

**`TTS.AliyunTTS`**（7 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.AliyunTTS.type` | - | 常用 |  |
| `TTS.AliyunTTS.output_dir` | - | 更多设置 |  |
| `TTS.AliyunTTS.appkey` | - | 常用 |  |
| `TTS.AliyunTTS.token` | - | 常用 |  |
| `TTS.AliyunTTS.voice` | - | 常用 |  |
| `TTS.AliyunTTS.access_key_id` | - | 常用 |  |
| `TTS.AliyunTTS.access_key_secret` | - | 常用 |  |

**`TTS.AliyunStreamTTS`**（8 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.AliyunStreamTTS.type` | - | 常用 |  |
| `TTS.AliyunStreamTTS.output_dir` | - | 更多设置 |  |
| `TTS.AliyunStreamTTS.appkey` | - | 常用 |  |
| `TTS.AliyunStreamTTS.token` | - | 常用 |  |
| `TTS.AliyunStreamTTS.voice` | - | 常用 |  |
| `TTS.AliyunStreamTTS.access_key_id` | - | 常用 |  |
| `TTS.AliyunStreamTTS.access_key_secret` | - | 常用 |  |
| `TTS.AliyunStreamTTS.host` | - | 常用 |  |

**`TTS.TencentTTS`**（7 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.TencentTTS.type` | - | 常用 |  |
| `TTS.TencentTTS.output_dir` | - | 更多设置 |  |
| `TTS.TencentTTS.appid` | - | 常用 |  |
| `TTS.TencentTTS.secret_id` | - | 常用 |  |
| `TTS.TencentTTS.secret_key` | - | 常用 |  |
| `TTS.TencentTTS.region` | - | 常用 |  |
| `TTS.TencentTTS.voice` | - | 常用 |  |

**`TTS.TTS302AI`**（6 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.TTS302AI.type` | - | 常用 |  |
| `TTS.TTS302AI.api_url` | - | 常用 |  |
| `TTS.TTS302AI.authorization` | - | 常用 |  |
| `TTS.TTS302AI.voice` | - | 常用 |  |
| `TTS.TTS302AI.output_dir` | - | 更多设置 |  |
| `TTS.TTS302AI.access_token` | - | 常用 |  |

**`TTS.OpenAITTS`**（7 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.OpenAITTS.type` | - | 常用 |  |
| `TTS.OpenAITTS.api_key` | - | 常用 |  |
| `TTS.OpenAITTS.api_url` | - | 常用 |  |
| `TTS.OpenAITTS.model` | - | 常用 |  |
| `TTS.OpenAITTS.voice` | - | 常用 |  |
| `TTS.OpenAITTS.speed` | - | 更多设置 |  |
| `TTS.OpenAITTS.output_dir` | - | 更多设置 |  |

**`TTS.CustomTTS`**（14 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.CustomTTS.type` | - | 常用 |  |
| `TTS.CustomTTS.method` | - | 常用 |  |
| `TTS.CustomTTS.url` | - | 常用 |  |
| `TTS.CustomTTS.params.input` | - | 更多设置 |  |
| `TTS.CustomTTS.params.response_format` | - | 常用 |  |
| `TTS.CustomTTS.params.download_format` | - | 更多设置 |  |
| `TTS.CustomTTS.params.voice` | - | 常用 |  |
| `TTS.CustomTTS.params.lang_code` | - | 更多设置 |  |
| `TTS.CustomTTS.params.return_download_link` | - | 更多设置 |  |
| `TTS.CustomTTS.params.speed` | - | 更多设置 |  |
| `TTS.CustomTTS.params.stream` | - | 更多设置 |  |
| `TTS.CustomTTS.headers` | - | 更多设置 |  |
| `TTS.CustomTTS.format` | - | 常用 |  |
| `TTS.CustomTTS.output_dir` | - | 更多设置 |  |

**`TTS.PaddleSpeechTTS`**（7 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.PaddleSpeechTTS.type` | - | 常用 |  |
| `TTS.PaddleSpeechTTS.protocol` | - | 常用 |  |
| `TTS.PaddleSpeechTTS.url` | - | 常用 |  |
| `TTS.PaddleSpeechTTS.spk_id` | - | 常用 |  |
| `TTS.PaddleSpeechTTS.speed` | - | 更多设置 |  |
| `TTS.PaddleSpeechTTS.volume` | - | 更多设置 |  |
| `TTS.PaddleSpeechTTS.save_path` | - | 更多设置 |  |

**`TTS.IndexStreamTTS`**（5 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.IndexStreamTTS.type` | - | 常用 |  |
| `TTS.IndexStreamTTS.api_url` | - | 常用 |  |
| `TTS.IndexStreamTTS.audio_format` | - | 常用 |  |
| `TTS.IndexStreamTTS.voice` | - | 常用 |  |
| `TTS.IndexStreamTTS.output_dir` | - | 更多设置 |  |

**`TTS.AliBLTTS`**（5 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.AliBLTTS.type` | - | 常用 |  |
| `TTS.AliBLTTS.api_key` | - | 常用 |  |
| `TTS.AliBLTTS.model` | - | 常用 |  |
| `TTS.AliBLTTS.voice` | - | 常用 |  |
| `TTS.AliBLTTS.output_dir` | - | 更多设置 |  |

**`TTS.XunFeiTTS`**（7 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.XunFeiTTS.type` | - | 常用 |  |
| `TTS.XunFeiTTS.api_url` | - | 常用 |  |
| `TTS.XunFeiTTS.app_id` | - | 常用 |  |
| `TTS.XunFeiTTS.api_secret` | - | 常用 |  |
| `TTS.XunFeiTTS.api_key` | - | 常用 |  |
| `TTS.XunFeiTTS.voice` | - | 常用 |  |
| `TTS.XunFeiTTS.output_dir` | - | 更多设置 |  |

**`TTS.MlxTTS`**（5 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.MlxTTS.type` | - | 常用 |  |
| `TTS.MlxTTS.url` | - | 常用 |  |
| `TTS.MlxTTS.speed` | - | 更多设置 |  |
| `TTS.MlxTTS.output_dir` | - | 更多设置 |  |
| `TTS.MlxTTS.split_sentences` | - | 更多设置 |  |

**`TTS.MlxStreamTTS`**（6 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.MlxStreamTTS.type` | - | 常用 |  |
| `TTS.MlxStreamTTS.url` | - | 常用 |  |
| `TTS.MlxStreamTTS.speed` | - | 更多设置 |  |
| `TTS.MlxStreamTTS.output_dir` | - | 更多设置 |  |
| `TTS.MlxStreamTTS.split_sentences` | - | 更多设置 |  |
| `TTS.MlxStreamTTS.tts_timeout` | - | 更多设置 |  |

**`TTS.MlxWanwanStreamTTS`**（9 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.MlxWanwanStreamTTS.type` | - | 常用 |  |
| `TTS.MlxWanwanStreamTTS.url` | - | 常用 |  |
| `TTS.MlxWanwanStreamTTS.speed` | - | 更多设置 |  |
| `TTS.MlxWanwanStreamTTS.output_dir` | - | 更多设置 |  |
| `TTS.MlxWanwanStreamTTS.split_sentences` | - | 更多设置 |  |
| `TTS.MlxWanwanStreamTTS.tts_timeout` | - | 更多设置 |  |
| `TTS.MlxWanwanStreamTTS.voice` | - | 常用 |  |
| `TTS.MlxWanwanStreamTTS.ref_audio` | - | 常用 |  |
| `TTS.MlxWanwanStreamTTS.ref_text` | - | 常用 |  |

**`TTS.MlxMengwaStreamTTS`**（9 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.MlxMengwaStreamTTS.type` | - | 常用 |  |
| `TTS.MlxMengwaStreamTTS.url` | - | 常用 |  |
| `TTS.MlxMengwaStreamTTS.speed` | - | 更多设置 |  |
| `TTS.MlxMengwaStreamTTS.output_dir` | - | 更多设置 |  |
| `TTS.MlxMengwaStreamTTS.split_sentences` | - | 更多设置 |  |
| `TTS.MlxMengwaStreamTTS.tts_timeout` | - | 更多设置 |  |
| `TTS.MlxMengwaStreamTTS.voice` | - | 常用 |  |
| `TTS.MlxMengwaStreamTTS.ref_audio` | - | 常用 |  |
| `TTS.MlxMengwaStreamTTS.ref_text` | - | 常用 |  |

**`Memory.mem0ai`**（2 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `Memory.mem0ai.type` | - | 常用 |  |
| `Memory.mem0ai.api_key` | - | 常用 |  |

**`Memory.powermem`**（11 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `Memory.powermem.type` | - | 常用 |  |
| `Memory.powermem.enable_user_profile` | - | 更多设置 |  |
| `Memory.powermem.llm.provider` | - | 常用 |  |
| `Memory.powermem.llm.config.api_key` | - | 常用 |  |
| `Memory.powermem.llm.config.model` | - | 常用 |  |
| `Memory.powermem.embedder.provider` | - | 常用 |  |
| `Memory.powermem.embedder.config.api_key` | - | 常用 |  |
| `Memory.powermem.embedder.config.model` | - | 常用 |  |
| `Memory.powermem.embedder.config.openai_base_url` | - | 更多设置 |  |
| `Memory.powermem.vector_store.provider` | - | 常用 |  |
| `Memory.powermem.vector_store.config` | - | 更多设置 |  |

**`Memory.mem_local_short`**（2 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `Memory.mem_local_short.type` | - | 常用 |  |
| `Memory.mem_local_short.llm` | - | 常用 |  |

### 插件与工具（32）

意图编排与工具面：Intent 子树（意图即工具编排器）、plugins.*、外部 MCP、工具调用参数。

| 字段 | 类型 | 旧页归属 | 层 | 备注 |
|---|---|---|---|---|
| `tool_call_timeout` | int | 服务器/连接 | 更多设置 |  |
| `mcp_endpoint` | str | — | 更多设置 | 外部 MCP 接入点 |
| `selected_module.Intent` | str | 意图与插件 | 常用 |  |

**`plugins.get_weather`**（3 字段；旧归属：意图与插件·已启用）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `plugins.get_weather.api_host` | str | 常用 |  |
| `plugins.get_weather.api_key` | str | 常用 |  |
| `plugins.get_weather.default_location` | str | 常用 |  |

**`plugins.get_news_from_chinanews`**（4 字段；旧归属：意图与插件·未启用折叠）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `plugins.get_news_from_chinanews.default_rss_url` | str | 常用 |  |
| `plugins.get_news_from_chinanews.society_rss_url` | str | 常用 |  |
| `plugins.get_news_from_chinanews.world_rss_url` | str | 常用 |  |
| `plugins.get_news_from_chinanews.finance_rss_url` | str | 常用 |  |

**`plugins.get_news_from_newsnow`**（2 字段；旧归属：意图与插件·未启用折叠）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `plugins.get_news_from_newsnow.url` | str | 常用 |  |
| `plugins.get_news_from_newsnow.news_sources` | str | 常用 |  |

**`plugins.home_assistant`**（3 字段；旧归属：意图与插件·未启用折叠）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `plugins.home_assistant.devices[i]` | list | 常用 |  |
| `plugins.home_assistant.base_url` | str | 常用 |  |
| `plugins.home_assistant.api_key` | str | 常用 |  |

**`plugins.play_music`**（3 字段；旧归属：意图与插件·未启用折叠）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `plugins.play_music.music_dir` | str | 常用 |  |
| `plugins.play_music.music_ext[i]` | list | 更多设置 |  |
| `plugins.play_music.refresh_time` | int | 更多设置 |  |

**`plugins.search_from_ragflow`**（4 字段；旧归属：意图与插件·未启用折叠）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `plugins.search_from_ragflow.description` | str | 常用 |  |
| `plugins.search_from_ragflow.base_url` | str | 常用 |  |
| `plugins.search_from_ragflow.api_key` | str | 常用 |  |
| `plugins.search_from_ragflow.dataset_ids[i]` | list | 常用 |  |

**`plugins.web_search`**（4 字段；旧归属：意图与插件·未启用折叠）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `plugins.web_search.provider` | str | 常用 |  |
| `plugins.web_search.description` | str | 常用 |  |
| `plugins.web_search.max_results` | int | 更多设置 |  |
| `plugins.web_search.api_key` | str | 常用 |  |

**`Intent.function_call`**（2 字段；旧归属：意图与插件）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `Intent.function_call.type` | str | 常用 |  |
| `Intent.function_call.functions` | list | 常用 |  |

**`Intent.nointent`**（1 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `Intent.nointent.type` | - | 常用 |  |

**`Intent.intent_llm`**（3 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `Intent.intent_llm.type` | - | 常用 |  |
| `Intent.intent_llm.llm` | - | 常用 |  |
| `Intent.intent_llm.functions` | - | 常用 |  |

### 设备（17）

设备侧：下发给设备的连接载荷（provisioning）、设备认证、hello 协商、发往设备的节奏与时区。

| 字段 | 类型 | 旧页归属 | 层 | 备注 |
|---|---|---|---|---|
| `server.websocket` | str | 服务器/连接 | 常用 | provisioning 载荷 |
| `server.timezone_offset` | int | 服务器/连接 | 更多设置 |  |
| `server.auth.enabled` | bool | 认证与设备 | 常用 |  |
| `server.auth.allowed_devices[i]` | list | 认证与设备 | 常用 |  |
| `server.mqtt_gateway` | NoneType | 服务器/连接 | 常用 | provisioning 载荷 |
| `server.mqtt_signature_key` | NoneType | 服务器/连接 | 常用 | provisioning 载荷 |
| `server.udp_gateway` | NoneType | 服务器/连接 | 常用 | provisioning 载荷 |
| `server.websocket_backup` | str | 服务器/连接 | 常用 | provisioning 载荷 |
| `server.auth_key` | str | — | 更多设置 | 危险：改 = 全部设备 token 失效 |
| `tts_audio_send_delay` | int | 服务器/连接 | 更多设置 |  |
| `xiaozhi.type` | str | — | 更多设置 | 设备协商值，通常勿改 |
| `xiaozhi.version` | int | — | 更多设置 | 设备协商值，通常勿改 |
| `xiaozhi.transport` | str | — | 更多设置 | 设备协商值，通常勿改 |
| `xiaozhi.audio_params.format` | str | — | 更多设置 | 设备协商值，通常勿改 |
| `xiaozhi.audio_params.sample_rate` | int | 语音与音频 | 更多设置 |  |
| `xiaozhi.audio_params.channels` | int | 语音与音频 | 更多设置 |  |
| `xiaozhi.audio_params.frame_duration` | int | 语音与音频 | 更多设置 |  |

### 系统（13）

编排者自身：监听地址、会话/文件生命周期、传输保活、日志、跨用途路径（多属主事实）。

| 字段 | 类型 | 旧页归属 | 层 | 备注 |
|---|---|---|---|---|
| `server.ip` | str | 服务器/连接 | 更多设置 |  |
| `server.port` | int | 服务器/连接 | 更多设置 |  |
| `server.http_port` | int | 服务器/连接 | 更多设置 |  |
| `server.vision_explain` | str | 服务器/连接 | 更多设置 |  |
| `log.log_format` | str | — | 更多设置 |  |
| `log.log_format_file` | str | — | 更多设置 |  |
| `log.log_level` | str | — | 更多设置 |  |
| `log.log_dir` | str | — | 更多设置 | 路径（多属主→系统） |
| `log.log_file` | str | — | 更多设置 |  |
| `log.data_dir` | str | — | 更多设置 | 跨用途 data 目录（多属主→系统） |
| `delete_audio` | bool | 服务器/连接 | 更多设置 |  |
| `close_connection_no_voice_time` | int | 服务器/连接 | 更多设置 |  |
| `enable_websocket_ping` | bool | 服务器/连接 | 更多设置 |  |

## 四、四判据自检

1. **零字段丢失** ✅ — 程序对账：527 = 15+450+32+17+13，且旧归属 10 组分布（服务器/连接 16、角色与对话 11、模型引擎 36、意图与插件 3、已启用 3、未启用折叠 20、语音与音频 6、认证与设备 3、无归宿·非选中 416、真孤儿 13）与 #14 §2 逐组相等。
   口径沿用 #14：`list` 记 1 字段。页面凭空字段 `server.auth.expire_seconds` 不在配置树内，**不落位**——#17 已定渲染以配置树为据，树外键自然不显示（见 §五）。
2. **可盲测** ✅ — 十道题见 §五，侧栏出发全部 ≤2 次点击。
3. **一级 ≤7、深度 ≤3** ✅ — 一级 = 5 域（+页脚逃生口，非域）；深度 = 侧栏(1) → 域内卡片(2) → 卡内/域内「更多设置」折叠(3)。
4. **归属由规则唯一算出** ✅ — R1（多属主版）+ R2（两问版）+ 接入字段名字集（§六全文），本表即脚本按规则机械产出，可复算。

## 五、盲测十道题（侧栏出发，≤2 次点击）

| # | 任务 | 路径 | 点击 |
|---|---|---|---|
| 1 | 改唤醒词 | 侧栏「对话与角色」→ 常用层平铺 `wakeup_words` | 1 |
| 2 | 换 TTS 引擎 | 侧栏「引擎」→ 「当前生效」面板 TTS 下拉 | 1 |
| 3 | 看设备在不在线 | 侧栏「设备」→ 设备列表/固件管理（运行时面） | 1 |
| 4 | 调 TTS 超时 | 侧栏「引擎」→ 展开「引擎全局参数」→ `tts_timeout` | 2 |
| 5 | 改角色提示词 | 侧栏「对话与角色」→ `prompt` | 1 |
| 6 | 给设备配 Wi-Fi | 侧栏「设备」→ SmartConfig 配网入口 | 1 |
| 7 | 轮换设备认证密钥 | 侧栏「设备」→ 更多设置 → `server.auth_key`（危险标记） | 2 |
| 8 | 启用天气插件 | 侧栏「插件与工具」→ 插件启用清单（`functions`，常用层） | 1 |
| 9 | 声纹加一位说话人 | 侧栏「对话与角色」→ `voiceprint.speakers`（常用层） | 1 |
| 10 | 改日志级别 | 侧栏「系统」→ `log.log_level` | 1 |

（第 10 题：系统域常用层为空、折叠区为空时不渲染折叠，故 1 次点击即达。）

## 六、接入字段名字集（卡内分层规则的机械依据）

字段名（取路径最后一段，`[i]` 去除）命中下表 → 卡内常用；否则卡内更多设置。`type` 恒为常用（只读标识）。

`accent`, `access_key_id`, `access_key_secret`, `access_token`, `agent_id`, `api_host`, `api_key`, `api_secret`, `api_url`, `app_id`, `appid`, `appkey`, `audio_format`, `authorization`, `aux_ref_audio_paths`, `base_url`, `bot_id`, `channels`, `cluster`, `dataset_ids`, `default_location`, `default_rss_url`, `description`, `dev_pid`, `devices`, `domain`, `finance_rss_url`, `format`, `functions`, `group_id`, `host`, `inp_refs`, `is_ssl`, `language`, `llm`, `method`, `mode`, `model`, `model_dir`, `model_name`, `model_path`, `model_type`, `music_dir`, `news_sources`, `personal_access_token`, `port`, `prompt_lang`, `prompt_language`, `prompt_text`, `protocol`, `provider`, `rate`, `ref_audio`, `ref_audio_path`, `ref_text`, `refer_wav_path`, `reference_audio`, `reference_id`, `reference_text`, `region`, `resource_id`, `response_format`, `secret_id`, `secret_key`, `society_rss_url`, `speaker`, `spk_id`, `text_lang`, `text_language`, `token`, `type`, `url`, `user_id`, `voice`, `voice_id`, `world_rss_url`, `ws_url`

特记：`llm`（`Intent.intent_llm.llm`、`Memory.mem_local_short.llm`）与 `functions` 是**引用承载**（按名字引用引擎/插件），恒为常用。

## 七、给 #18 的移交注意

1. **计数口径**：沿用 #14（`list` 记 1），验收数字以 527 为准；`server.auth.expire_seconds` 是树外凭空字段，不计数、不落位、不渲染。
2. **意图双清单**：`Intent.function_call.functions` 与 `Intent.intent_llm.functions` 两份启用清单——方案要求意图两分支各自成卡（同在「插件与工具」），两份清单都可见，切换 `selected_module.Intent` 不再有清单消失问题。
3. **插件库**：未启用插件的参数不再折叠进 `<details>`，与引擎库同构（全部插件卡片 + 启用清单）。
4. **危险标记**：`server.auth_key`（改 = 全部设备 token 失效）在设备·更多设置内带危险视觉；危险操作的分级尺度仍是地图上的雾（另一件事）。
5. **设备协商值**：`xiaozhi.type/version/transport/audio_params.format` 标注「设备协商值，通常勿改」（hello 握手用设备上报值覆写）。
6. **「已配置/未配置」判定语义**：方案必须写明由服务端显式判定，不能靠正则猜掩码形态（#17 查明的两个显示 bug 的教训）。
7. **字段元信息**：渲染依据必须是配置树（#17 已实证静态清单会漏注入键），中文标签/说明/控件类型的单一事实源问题按地图 Not-yet-specified 处理，写进方案。

