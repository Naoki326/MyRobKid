# 服务端配置页：字段盘点与归属判定规则

> 研究票据：[#14 字段盘点与归属规则](https://github.com/Naoki326/MyRobKid/issues/14)（上级地图 #13）。
> 本文只做**盘点与规则拟定**，不做字段级落位裁决，也不改任何代码/配置/HTML。
>
> **证据基线**：`server/config.yaml`（vendored 上游模板，1176 行）、`server/data/.config.yaml`（本机用户配置，156 行）、
> 运行时生效配置 `curl -s http://127.0.0.1:8003/xiaozhi/config/api/full`（2026-09-12 抓取，落盘 `/tmp/full_config.json`）、
> 页面 `server/config/config_page.html`（813 行）。
> 除非另行标注，正文中的「`config_page.html:NNN`」都是该文件的真实行号。

---

## 一、结论摘要

1. **字段总量 527 个叶子**。计数约定：`list` 一律记作 **1 个字段**（页面用 `listFld`/`engFields` 也是按一个列表控件渲染），
   空 `object` 占 1 个位置；`list[dict]` 展开元素键。其中
   **引擎子树 448 个（85.0%）**，引擎之外的零散字段只有 **79 个**。
2. **当前页面只渲染得动 98 个字段**（`config_page.html:519` 起的 `buildGroupBody()` 六个分支 + `config_page.html:267` `buildOta()`）。
   剩下的 **429 个不是「藏起来了」，而是根本没有渲染路径**——它们只在 `advanced` 组的只读 JSON 里可见
   （`config_page.html:663` `buildAdvanced()`，渲染点 `config_page.html:665`）。其中 416 个属于「未被 `selected_module` 选中的引擎/意图」，
   13 个是真正的孤儿（`log.*` 6 个、`xiaozhi.type/version/transport`、`xiaozhi.audio_params.format`、`mcp_endpoint`、
   `server.auth_key`、`voiceprint.speakers`）。
3. **当前页面实际按「YAML 文件的物理位置」分组**，不是按组件功能。`buildGroupBody()` 的 `server` 组把
   `server.*`、`close_connection_no_voice_time`、`tts_timeout`、`tool_call_timeout`、`enable_websocket_ping`、
   `delete_audio`、`tts_audio_send_delay` 打进同一张卡——这 6 个顶层键在 YAML 里恰好挨着 `server:` 段落，
   于是「相邻」被误当成了「同类」（`server/config.yaml:9-82`）。
4. **推荐的判定规则**（详见 §三「规则 R」）：
   > **R1 求属主**——字段是「某个可替换组件的实现细节」还是「编排者自己的策略」；
   > **R2 定分层**——`常用 = 换掉它要多写代码（声明式选择/被其他字段引用）`，`更多设置 = 换掉它不用改代码（纯参数/地址/密钥/阈值）`。
5. **分歧点最有价值**：LuCI 按**管理职责域**切一级（Status/System/Services/Network），Frigate 按**作用域与宿主**切
   （global/camera/integration/system），上游智控台按**YAML 命名空间前缀**切（source 里字面写着 `paramCode.startsWith('server.')`）。
   三种候选在 `xiaozhi.audio_params.*`、`voiceprint.speakers`、`plugins.*`、`Intent.functions` 四处**结论不同**，
   见 §三「候选与分歧」。本票推荐 R，但在 §三列出了 R 与三者的逐条对撞。

---

## 二、字段全量清单

### 2.1 计数总览

| 范围 | 叶子数 | 已渲染 | 未渲染 |
|---|---|---|---|
| 引擎子树（`VAD/ASR/LLM/VLLM/TTS/Memory/Intent`） | 448 | 32 | 416 |
| 引擎之外零散字段 | 79 | 66 | 13 |
| **合计** | **527** | **98** | **429** |

> 另有 1 个**页面凭空字段** `server.auth.expire_seconds`（模板与生效配置里都没有，见 §4.3），不在这 527 之内。

按引擎拆分：

| 引擎族 | 引擎数 | 叶子字段 | 当前选中 | 已渲染字段 |
|---|---|---|---|---|
| `VAD` | 1 | 5 | `SileroVAD` | 5 |
| `ASR` | 17 | 109 | `FunASR` | 4 |
| `LLM` | 16 | 72 | `ThirkingLLM` | 7 |
| `VLLM` | 4 | 16 | `ThirkingVLLM` | 4 |
| `TTS` | 26 | 224 | `MlxKafeiStreamTTS` | 9 |
| `Memory` | 4 | 16 | `nomem` | 1 |
| `Intent` | 3 | 6 | `function_call` | 2 |
| **小计** | **71** | **448** | | **32** |

按「现页归属」计数：

| 现页归属 | 叶子数 | 说明 |
|---|---|---|
| 服务器/连接 | 16 | `buildGroupBody('server')`，`config_page.html:521` |
| 角色与对话 | 11 | `buildGroupBody('role')`，`config_page.html:547` |
| 模型引擎 | 36 | `buildGroupBody('models')`，`config_page.html:567`（仅选中引擎 + 7 个 `selected_module` 下拉） |
| 意图与插件 | 3 | `buildGroupBody('intent')`，`config_page.html:599`（仅选中 `Intent` 分支） |
| 意图与插件·未启用折叠 | 20 | 同组下 `<details>📂 未启用插件的参数`，`config_page.html:617` |
| 语音与音频 | 6 | `buildGroupBody('speech')`，`config_page.html:620` |
| 认证与设备 | 3 | `buildGroupBody('device')`，`config_page.html:635` |
| **无归宿（非选中引擎/意图）** | **416** | 只在「全部配置」只读 JSON 可见 |
| **无归宿（真孤儿）** | **13** | 同上 |
| **合计** | **527** | |

### 2.2 A 段：引擎之外的字段（79 个叶子）

> 「现页归属」列若为「—」即 `无归宿`——仅出现在 `config_page.html:665` 的只读 JSON。
>
> 本表按叶子路径逐条列出（`list` 也写成 `[i]` 以便与 `listFld` 的实参对应），
> 因此表内 `allowed_devices[i]` / `wakeup_words[i]` / `exit_commands[i]` / `test_sentences[i]` / `speakers[i]` / `functions[i]`
> **六个 `list` 在总数里各只算 1 个字段**。

| 字段路径 | 类型 | 现页归属 |
|---|---|---|
| `server.ip` | `str` | 服务器/连接 |
| `server.port` | `int` | 服务器/连接 |
| `server.http_port` | `int` | 服务器/连接 |
| `server.websocket` | `str` | 服务器/连接 |
| `server.vision_explain` | `str` | 服务器/连接 |
| `server.timezone_offset` | `int` | 服务器/连接 |
| `server.auth.enabled` | `bool` | 认证与设备 |
| `server.auth.allowed_devices[i]` | `list` | 认证与设备 |
| `server.mqtt_gateway` | `NoneType` | 服务器/连接 |
| `server.mqtt_signature_key` | `NoneType` | 服务器/连接 |
| `server.udp_gateway` | `NoneType` | 服务器/连接 |
| `server.websocket_backup` | `str` | 服务器/连接 |
| `server.auth_key` | `str` | — |
| `log.log_format` | `str` | — |
| `log.log_format_file` | `str` | — |
| `log.log_level` | `str` | — |
| `log.log_dir` | `str` | — |
| `log.log_file` | `str` | — |
| `log.data_dir` | `str` | — |
| `delete_audio` | `bool` | 服务器/连接 |
| `close_connection_no_voice_time` | `int` | 服务器/连接 |
| `tts_timeout` | `int` | 服务器/连接 |
| `tool_call_timeout` | `int` | 服务器/连接 |
| `enable_wakeup_words_response_cache` | `bool` | 角色与对话 |
| `enable_greeting` | `bool` | 角色与对话 |
| `enable_stop_tts_notify` | `bool` | 角色与对话 |
| `stop_tts_notify_voice` | `str` | 角色与对话 |
| `enable_websocket_ping` | `bool` | 服务器/连接 |
| `tts_audio_send_delay` | `int` | 服务器/连接 |
| `exit_commands[i]` | `list` | 角色与对话 |
| `xiaozhi.type` | `str` | — |
| `xiaozhi.version` | `int` | — |
| `xiaozhi.transport` | `str` | — |
| `xiaozhi.audio_params.format` | `str` | — |
| `xiaozhi.audio_params.sample_rate` | `int` | 语音与音频 |
| `xiaozhi.audio_params.channels` | `int` | 语音与音频 |
| `xiaozhi.audio_params.frame_duration` | `int` | 语音与音频 |
| `module_test.test_sentences[i]` | `list` | 语音与音频 |
| `wakeup_words[i]` | `list` | 角色与对话 |
| `mcp_endpoint` | `str` | — |
| `context_providers[i]` | `list` | 认证与设备 |
| `voiceprint.url` | `NoneType` | 语音与音频 |
| `voiceprint.speakers[i]` | `list` | — |
| `voiceprint.similarity_threshold` | `float` | 语音与音频 |
| `prompt` | `str` | 角色与对话 |
| `prompt_template` | `str` | 角色与对话 |
| `system_error_response` | `str` | 角色与对话 |
| `end_prompt.enable` | `bool` | 角色与对话 |
| `end_prompt.prompt` | `str` | 角色与对话 |
| `selected_module.VAD` | `str` | 模型引擎 |
| `selected_module.ASR` | `str` | 模型引擎 |
| `selected_module.LLM` | `str` | 模型引擎 |
| `selected_module.VLLM` | `str` | 模型引擎 |
| `selected_module.TTS` | `str` | 模型引擎 |
| `selected_module.Memory` | `str` | 模型引擎 |
| `selected_module.Intent` | `str` | 意图与插件 |
| `plugins.get_weather.api_host` | `str` | 意图与插件·已启用 |
| `plugins.get_weather.api_key` | `str` | 意图与插件·已启用 |
| `plugins.get_weather.default_location` | `str` | 意图与插件·已启用 |
| `plugins.get_news_from_chinanews.default_rss_url` | `str` | 意图与插件·未启用折叠 |
| `plugins.get_news_from_chinanews.society_rss_url` | `str` | 意图与插件·未启用折叠 |
| `plugins.get_news_from_chinanews.world_rss_url` | `str` | 意图与插件·未启用折叠 |
| `plugins.get_news_from_chinanews.finance_rss_url` | `str` | 意图与插件·未启用折叠 |
| `plugins.get_news_from_newsnow.url` | `str` | 意图与插件·未启用折叠 |
| `plugins.get_news_from_newsnow.news_sources` | `str` | 意图与插件·未启用折叠 |
| `plugins.home_assistant.devices[i]` | `list` | 意图与插件·未启用折叠 |
| `plugins.home_assistant.base_url` | `str` | 意图与插件·未启用折叠 |
| `plugins.home_assistant.api_key` | `str` | 意图与插件·未启用折叠 |
| `plugins.play_music.music_dir` | `str` | 意图与插件·未启用折叠 |
| `plugins.play_music.music_ext[i]` | `list` | 意图与插件·未启用折叠 |
| `plugins.play_music.refresh_time` | `int` | 意图与插件·未启用折叠 |
| `plugins.search_from_ragflow.description` | `str` | 意图与插件·未启用折叠 |
| `plugins.search_from_ragflow.base_url` | `str` | 意图与插件·未启用折叠 |
| `plugins.search_from_ragflow.api_key` | `str` | 意图与插件·未启用折叠 |
| `plugins.search_from_ragflow.dataset_ids[i]` | `list` | 意图与插件·未启用折叠 |
| `plugins.web_search.provider` | `str` | 意图与插件·未启用折叠 |
| `plugins.web_search.description` | `str` | 意图与插件·未启用折叠 |
| `plugins.web_search.max_results` | `int` | 意图与插件·未启用折叠 |
| `plugins.web_search.api_key` | `str` | 意图与插件·未启用折叠 |

**注**：`context_providers[i]` 是 `list[dict]`，页面的 `engFields(S.context_providers?.[0]||{}, 'context_providers.0')`
（`config_page.html:657`）**只渲染第 0 个元素**，其两个子键 `url` 与 `headers.Authorization` 在页面上可见；
第 1 个及以后的源不可见（生效配置当前只有 1 个源，所以暂时看不出问题）。
本表把整个列表记作 1 个字段，与「`list` = 1 控件」的约定一致。

**注**：`server.auth.expire_seconds` 在页面可编辑（`config_page.html:653`），但**模板与生效配置里都没有这个键**——
它的值是代码里的默认 30 天（`server/core/auth.py:22`），页面渲染时会显示为空。这是「页面凭空造字段」的唯一一例，
盘点时应登记为**页面侧虚拟字段**，不算配置里的叶子。

模板/生效配置的差集（`server/config.yaml` 特有 0 个，生效配置特有的键）：
- `server.websocket_backup`、`server.auth_key` 只在 `server/data/.config.yaml`（:8、:4）里，模板无；
- 五个 `Mlx*TTS` 引擎、`ThirkingLLM`、`ThirkingVLLM` 只在本机配置里，模板无。

### 2.3 B 段：引擎子树（448 个叶子）

每个引擎下的字段，未选中者一律标 `—`（无归宿）。选中者用 `← 选中` 标出。

#### VAD（1 引擎 / 5 叶子） —— 当前选中 `SileroVAD`

**`VAD.SileroVAD`**（5 字段） ← **选中**

| 字段路径 | 类型 | 现页归属 |
|---|---|---|
| `VAD.SileroVAD.type` | `str` | 模型引擎 |
| `VAD.SileroVAD.threshold` | `float` | 模型引擎 |
| `VAD.SileroVAD.threshold_low` | `float` | 模型引擎 |
| `VAD.SileroVAD.model_dir` | `str` | 模型引擎 |
| `VAD.SileroVAD.min_silence_duration_ms` | `int` | 模型引擎 |

#### ASR（17 引擎 / 109 叶子） —— 当前选中 `FunASR`

**`ASR.FunASR`**（4 字段） ← **选中**

| 字段路径 | 类型 | 现页归属 |
|---|---|---|
| `ASR.FunASR.type` | `str` | 模型引擎 |
| `ASR.FunASR.model_dir` | `str` | 模型引擎 |
| `ASR.FunASR.output_dir` | `str` | 模型引擎 |
| `ASR.FunASR.language` | `str` | 模型引擎 |

**其余 16 个引擎全部无归宿（非选中），共 105 字段**：

| 引擎 / 分支 | 字段数 | 字段名（含 `type`） |
|---|---|---|
| `ASR.FunASRServer` | 6 | `type`, `host`, `port`, `is_ssl`, `api_key`, `output_dir` |
| `ASR.SherpaASR` | 4 | `type`, `model_dir`, `output_dir`, `model_type` |
| `ASR.SherpaParaformerASR` | 4 | `type`, `model_dir`, `output_dir`, `model_type` |
| `ASR.DoubaoASR` | 7 | `type`, `appid`, `access_token`, `cluster`, `boosting_table_name`, `correct_table_name`, `output_dir` |
| `ASR.DoubaoStreamASR` | 9 | `type`, `appid`, `access_token`, `resource_id`, `boosting_table_name`, `correct_table_name`, `enable_multilingual`, `end_window_size`, `output_dir` |
| `ASR.DoubaoStreamASRV2` | 9 | `type`, `appid`, `access_token`, `resource_id`, `boosting_table_name`, `correct_table_name`, `enable_multilingual`, `end_window_size`, `output_dir` |
| `ASR.TencentASR` | 5 | `type`, `appid`, `secret_id`, `secret_key`, `output_dir` |
| `ASR.AliyunASR` | 6 | `type`, `appkey`, `token`, `access_key_id`, `access_key_secret`, `output_dir` |
| `ASR.AliyunStreamASR` | 8 | `type`, `appkey`, `token`, `access_key_id`, `access_key_secret`, `host`, `max_sentence_silence`, `output_dir` |
| `ASR.BaiduASR` | 6 | `type`, `app_id`, `api_key`, `secret_key`, `dev_pid`, `output_dir` |
| `ASR.OpenaiASR` | 5 | `type`, `api_key`, `base_url`, `model_name`, `output_dir` |
| `ASR.GroqASR` | 5 | `type`, `api_key`, `base_url`, `model_name`, `output_dir` |
| `ASR.VoskASR` | 3 | `type`, `model_path`, `output_dir` |
| `ASR.Qwen3ASRFlash` | 8 | `type`, `api_key`, `base_url`, `model_name`, `output_dir`, `enable_lid`, `enable_itn`, `context` |
| `ASR.XunfeiStreamASR` | 8 | `type`, `app_id`, `api_key`, `api_secret`, `domain`, `language`, `accent`, `output_dir` |
| `ASR.AliyunBLStreamASR` | 12 | `type`, `api_key`, `model`, `format`, `sample_rate`, `disfluency_removal_enabled`, `semantic_punctuation_enabled`, `max_sentence_silence`, `multi_threshold_mode_enabled`, `punctuation_prediction_enabled`, `inverse_text_normalization_enabled`, `output_dir` |

#### LLM（16 引擎 / 72 叶子） —— 当前选中 `ThirkingLLM`

**`LLM.ThirkingLLM`**（7 字段） ← **选中**

| 字段路径 | 类型 | 现页归属 |
|---|---|---|
| `LLM.ThirkingLLM.type` | `str` | 模型引擎 |
| `LLM.ThirkingLLM.model_name` | `str` | 模型引擎 |
| `LLM.ThirkingLLM.url` | `str` | 模型引擎 |
| `LLM.ThirkingLLM.api_key` | `str` | 模型引擎 |
| `LLM.ThirkingLLM.max_tokens` | `int` | 模型引擎 |
| `LLM.ThirkingLLM.temperature` | `float` | 模型引擎 |
| `LLM.ThirkingLLM.reasoning_effort` | `str` | 模型引擎 |

**其余 15 个引擎全部无归宿（非选中），共 65 字段**：

| 引擎 / 分支 | 字段数 | 字段名（含 `type`） |
|---|---|---|
| `LLM.AliLLM` | 8 | `type`, `base_url`, `model_name`, `api_key`, `temperature`, `max_tokens`, `top_p`, `frequency_penalty` |
| `LLM.AliAppLLM` | 6 | `type`, `base_url`, `app_id`, `api_key`, `is_no_prompt`, `ali_memory_id` |
| `LLM.DoubaoLLM` | 4 | `type`, `base_url`, `model_name`, `api_key` |
| `LLM.DeepSeekLLM` | 4 | `type`, `model_name`, `url`, `api_key` |
| `LLM.ChatGLMLLM` | 4 | `type`, `model_name`, `url`, `api_key` |
| `LLM.OllamaLLM` | 3 | `type`, `model_name`, `base_url` |
| `LLM.DifyLLM` | 4 | `type`, `base_url`, `api_key`, `mode` |
| `LLM.GeminiLLM` | 5 | `type`, `api_key`, `model_name`, `http_proxy`, `https_proxy` |
| `LLM.CozeLLM` | 4 | `type`, `bot_id`, `user_id`, `personal_access_token` |
| `LLM.VolcesAiGatewayLLM` | 4 | `type`, `base_url`, `model_name`, `api_key` |
| `LLM.LMStudioLLM` | 4 | `type`, `model_name`, `url`, `api_key` |
| `LLM.HomeAssistant` | 4 | `type`, `base_url`, `agent_id`, `api_key` |
| `LLM.FastgptLLM` | 5 | `type`, `base_url`, `api_key`, `variables.k`, `variables.k2` |
| `LLM.XinferenceLLM` | 3 | `type`, `model_name`, `base_url` |
| `LLM.XinferenceSmallLLM` | 3 | `type`, `model_name`, `base_url` |

#### VLLM（4 引擎 / 16 叶子） —— 当前选中 `ThirkingVLLM`

**`VLLM.ThirkingVLLM`**（4 字段） ← **选中**

| 字段路径 | 类型 | 现页归属 |
|---|---|---|
| `VLLM.ThirkingVLLM.type` | `str` | 模型引擎 |
| `VLLM.ThirkingVLLM.model_name` | `str` | 模型引擎 |
| `VLLM.ThirkingVLLM.url` | `str` | 模型引擎 |
| `VLLM.ThirkingVLLM.api_key` | `str` | 模型引擎 |

**其余 3 个引擎全部无归宿（非选中），共 12 字段**：

| 引擎 / 分支 | 字段数 | 字段名（含 `type`） |
|---|---|---|
| `VLLM.ChatGLMVLLM` | 4 | `type`, `model_name`, `url`, `api_key` |
| `VLLM.QwenVLVLLM` | 4 | `type`, `model_name`, `url`, `api_key` |
| `VLLM.XunfeiSparkLLM` | 4 | `type`, `base_url`, `model_name`, `api_key` |

#### TTS（26 引擎 / 224 叶子） —— 当前选中 `MlxKafeiStreamTTS`

**`TTS.MlxKafeiStreamTTS`**（9 字段） ← **选中**

| 字段路径 | 类型 | 现页归属 |
|---|---|---|
| `TTS.MlxKafeiStreamTTS.type` | `str` | 模型引擎 |
| `TTS.MlxKafeiStreamTTS.url` | `str` | 模型引擎 |
| `TTS.MlxKafeiStreamTTS.speed` | `float` | 模型引擎 |
| `TTS.MlxKafeiStreamTTS.output_dir` | `str` | 模型引擎 |
| `TTS.MlxKafeiStreamTTS.split_sentences` | `bool` | 模型引擎 |
| `TTS.MlxKafeiStreamTTS.tts_timeout` | `int` | 模型引擎 |
| `TTS.MlxKafeiStreamTTS.voice` | `str` | 模型引擎 |
| `TTS.MlxKafeiStreamTTS.ref_audio` | `str` | 模型引擎 |
| `TTS.MlxKafeiStreamTTS.ref_text` | `str` | 模型引擎 |

**其余 25 个引擎全部无归宿（非选中），共 215 字段**：

| 引擎 / 分支 | 字段数 | 字段名（含 `type`） |
|---|---|---|
| `TTS.EdgeTTS` | 3 | `type`, `voice`, `output_dir` |
| `TTS.DoubaoTTS` | 11 | `type`, `api_url`, `voice`, `output_dir`, `authorization`, `appid`, `access_token`, `cluster`, `speed_ratio`, `volume_ratio`, `pitch_ratio` |
| `TTS.HuoshanDoubleStreamTTS` | 10 | `type`, `ws_url`, `appid`, `access_token`, `resource_id`, `speaker`, `enable_ws_reuse`, `audio_params.speech_rate`, `audio_params.loudness_rate`, `additions.post_process.pitch` |
| `TTS.HuoshanDoubleStreamTTSV2` | 10 | `type`, `ws_url`, `appid`, `access_token`, `resource_id`, `speaker`, `enable_ws_reuse`, `audio_params.speech_rate`, `audio_params.loudness_rate`, `additions.post_process.pitch` |
| `TTS.CosyVoiceSiliconflow` | 6 | `type`, `model`, `voice`, `output_dir`, `access_token`, `response_format` |
| `TTS.CozeCnTTS` | 5 | `type`, `voice`, `output_dir`, `access_token`, `response_format` |
| `TTS.VolcesAiGatewayTTS` | 7 | `type`, `api_key`, `api_url`, `model`, `voice`, `speed`, `output_dir` |
| `TTS.FishSpeech` | 19 | `type`, `output_dir`, `response_format`, `reference_id`, `reference_audio`, `reference_text`, `normalize`, `max_new_tokens`, `chunk_length`, `top_p`, `repetition_penalty`, `temperature`, `streaming`, `use_memory_cache`, `seed`, `channels`, `rate`, `api_key`, `api_url` |
| `TTS.GPT_SOVITS_V2` | 21 | `type`, `url`, `output_dir`, `text_lang`, `ref_audio_path`, `prompt_text`, `prompt_lang`, `top_k`, `top_p`, `temperature`, `text_split_method`, `batch_size`, `batch_threshold`, `split_bucket`, `return_fragment`, `speed_factor`, `streaming_mode`, `seed`, `parallel_infer`, `repetition_penalty`, `aux_ref_audio_paths` |
| `TTS.GPT_SOVITS_V3` | 15 | `type`, `url`, `output_dir`, `text_language`, `refer_wav_path`, `prompt_language`, `prompt_text`, `top_k`, `top_p`, `temperature`, `cut_punc`, `speed`, `inp_refs`, `sample_steps`, `if_sr` |
| `TTS.MinimaxTTSHTTPStream` | 6 | `type`, `output_dir`, `group_id`, `api_key`, `model`, `voice_id` |
| `TTS.AliyunTTS` | 7 | `type`, `output_dir`, `appkey`, `token`, `voice`, `access_key_id`, `access_key_secret` |
| `TTS.AliyunStreamTTS` | 8 | `type`, `output_dir`, `appkey`, `token`, `voice`, `access_key_id`, `access_key_secret`, `host` |
| `TTS.TencentTTS` | 7 | `type`, `output_dir`, `appid`, `secret_id`, `secret_key`, `region`, `voice` |
| `TTS.TTS302AI` | 6 | `type`, `api_url`, `authorization`, `voice`, `output_dir`, `access_token` |
| `TTS.OpenAITTS` | 7 | `type`, `api_key`, `api_url`, `model`, `voice`, `speed`, `output_dir` |
| `TTS.CustomTTS` | 14 | `type`, `method`, `url`, `params.input`, `params.response_format`, `params.download_format`, `params.voice`, `params.lang_code`, `params.return_download_link`, `params.speed`, `params.stream`, `headers`, `format`, `output_dir` |
| `TTS.PaddleSpeechTTS` | 7 | `type`, `protocol`, `url`, `spk_id`, `speed`, `volume`, `save_path` |
| `TTS.IndexStreamTTS` | 5 | `type`, `api_url`, `audio_format`, `voice`, `output_dir` |
| `TTS.AliBLTTS` | 5 | `type`, `api_key`, `model`, `voice`, `output_dir` |
| `TTS.XunFeiTTS` | 7 | `type`, `api_url`, `app_id`, `api_secret`, `api_key`, `voice`, `output_dir` |
| `TTS.MlxTTS` | 5 | `type`, `url`, `speed`, `output_dir`, `split_sentences` |
| `TTS.MlxStreamTTS` | 6 | `type`, `url`, `speed`, `output_dir`, `split_sentences`, `tts_timeout` |
| `TTS.MlxWanwanStreamTTS` | 9 | `type`, `url`, `speed`, `output_dir`, `split_sentences`, `tts_timeout`, `voice`, `ref_audio`, `ref_text` |
| `TTS.MlxMengwaStreamTTS` | 9 | `type`, `url`, `speed`, `output_dir`, `split_sentences`, `tts_timeout`, `voice`, `ref_audio`, `ref_text` |

#### Memory（4 引擎 / 16 叶子） —— 当前选中 `nomem`

**`Memory.nomem`**（1 字段） ← **选中**

| 字段路径 | 类型 | 现页归属 |
|---|---|---|
| `Memory.nomem.type` | `str` | 模型引擎 |

**其余 3 个引擎全部无归宿（非选中），共 15 字段**：

| 引擎 / 分支 | 字段数 | 字段名（含 `type`） |
|---|---|---|
| `Memory.mem0ai` | 2 | `type`, `api_key` |
| `Memory.powermem` | 11 | `type`, `enable_user_profile`, `llm.provider`, `llm.config.api_key`, `llm.config.model`, `embedder.provider`, `embedder.config.api_key`, `embedder.config.model`, `embedder.config.openai_base_url`, `vector_store.provider`, `vector_store.config` |
| `Memory.mem_local_short` | 2 | `type`, `llm` |

#### Intent（3 引擎 / 6 叶子） —— 当前选中 `function_call`

**`Intent.function_call`**（2 字段） ← **选中**

| 字段路径 | 类型 | 现页归属 |
|---|---|---|
| `Intent.function_call.type` | `str` | 意图与插件 |
| `Intent.function_call.functions` | `list` | 意图与插件 |

**其余 2 个分支无归宿（非选中），共 4 字段**：

| 引擎 / 分支 | 字段数 | 字段名（含 `type`） |
|---|---|---|
| `Intent.nointent` | 1 | `type` |
| `Intent.intent_llm` | 3 | `type`, `llm`, `functions` |

---

## 三、归属判定规则

### 3.1 推荐的规则 R（两步，可机械套用）

> **R1 — 求属主（定一级域）。** 问一句：「把这个字段写死成常量，谁会受损？」
>
> - 若受损的是**某一个可替换组件的实现**（换个引擎/插件/算法就不再适用）→ 域 = 该组件所在的域。
> - 若受损的是**编排者的策略**（谁都可以用，与选哪个组件无关）→ 域按编排者职责落到 `系统` / `设备` / `高级`。
>
> **R2 — 定分层（常用 / 更多设置）。** 问一句：「改这个字段，要不要改代码或重启才能被承认？」
>
> - `常用` = 声明式选择，或**被其他字段引用的名字**。典型：`selected_module.*`、`prompt`、`wakeup_words`、
>   `server.websocket`、`server.auth.enabled`、插件启用开关。
> - `更多设置` = 纯参数 / 地址 / 密钥 / 阈值 / 调优旋钮 / 调试开关。特点：**换掉它不需要动任何其他配置**。
>   典型：`threshold`、`speed`、`temperature`、`api_key`、`base_url`、`output_dir`、`reasoning_effort`、
>   `log.*`、`enable_websocket_ping`、`tts_audio_send_delay`。

**为什么把 R2 押在「是否被引用」而不是「用户懂不懂」**：被字段之间引用关系决定的东西，改错了会**静默失效**
（页面上选了一个引擎名，但同名配置块不存在 → 引擎静默回退/报错）；而纯参数改错了只影响体感，能试出来。
这条轴也因此**可被盲测**——把字段扔进一个没看过页面的人面前，他只需要查「有没有别的字段引用了这个名字」。

### 3.2 候选方案（先例）

| 方案 | 一级域切法 | 分层切法 | 出处 |
|---|---|---|---|
| **R（本票推荐）** | 属主：组件实现细节 vs 编排者策略 | 是否被引用/声明式 | 综合下面三者 |
| **S（上游智控台）** | **YAML 命名空间前缀** | 无分层（全部平铺 + 搜索框） | `manager-web/src/views/ParamsManagement.vue` 的 `getTopGroup()` 字面写着 `paramCode.startsWith('server.')` → `server`，`'plugins.'` → `plugins`，`'log.'` → `log`，`'session_state.'` → `session_state`，其余 → `general`；i18n 把五类译作 全部/服务端/插件/日志/会话/通用（`i18n/zh_CN.js` `paramManagement.group.*`） |
| **L（OpenWrt LuCI）** | **管理职责域**：`admin/status`、`admin/system`、`admin/services`、`admin/network`、`admin/vpn` | 同一 map 内第二个 tab `Advanced Settings` | `modules/luci-base/root/usr/share/luci/menu.d/luci-base.json`（顶层全是 `firstchild` 容器，靠 `order` 排序）；`luci-app-firewall/.../view/firewall/zones.js:112-115` 定义 `general/advanced/conntrack/extra` 四个 tab，`:232` 起把 `device`、`subnet`、`masq6`、`family`、`masq_src/dest`、`log` 全塞进 `advanced` |
| **F（Frigate）** | **作用域 × 宿主**：`general` / `globalConfig` / `cameras` / `enrichments` / `system` / `users` / `notifications` / `frigateplus` / `maintenance` | 同一表单内 `advancedFields` 默认折叠 | `web/src/pages/Settings.tsx:307-455` 的 `settingsGroups`；`web/src/components/config-form/ConfigForm.tsx:198` 注释 `/** Fields marked as advanced (collapsed by default) */`；`lib/config-schema/transformer.ts:545-551` 落到 `ui:options.advanced = true` |

**分层先例的共性**：Frigate 与 LuCI 都把「**调优旋钮 + 原始/低层输入 + 环境耦合项**」放进 advanced，
把「**启用开关 + 主模式 + 主阈值 + 必填凭据**」留在默认层。证据：Frigate `motion.ts` 的
`advancedFields = [lightning_threshold, skip_motion_threshold, delta_alpha, frame_alpha, frame_height, mqtt_off_delay]`
而 `threshold`、`contour_area` 留在默认；`auth.ts` 把 `cookie_name`、`session_length`、`hash_iterations`、`trusted_proxies`
标 advanced，只留 `enabled` 和 `native_oauth_url`；LuCI `zones.js` 把 `subnet`、`masq_src/dest`、`log_limit` 放 advanced，
把 `input/output/forward` 策略留在 general。这与 R2 的「是否被引用」**高度重合**——纯参数正是那些没人引用的字段。

### 3.3 候选之间的分歧点（最有价值的部分）

| 纠缠字段 | 按 R（属主） | 按 S（YAML 前缀） | 按 L（职责域） | 按 F（作用域×宿主） | 分歧根因 |
|---|---|---|---|---|---|
| `xiaozhi.audio_params.*`（4 个） | **设备**（固件 hello 协商的格式，服务端只是回声） | `general`（顶层键不是 `server.`/`plugins.`/`log.`/`session_state.`） | 应进 `admin/network` 或干脆不可见（LuCI 里这类是驱动默认值） | `globalConfig`（唯一一份全局默认） | 「设备协商参数」在 LuCI 世界观里根本不是可编辑项；在 Frigate 世界观里是全局默认；只有 R 认为它属于设备这一侧 |
| `voiceprint.speakers` | **对话与角色**（说话人身份是角色知识，`server/core/utils/dialogue.py:126-131` 把它拼成 `<speakers_info>` 注入提示词） | `general`（顶层键） | `admin/services`（声纹是一个服务） | `enrichments`（Frigate 把所有 AI 增强放这里） | L/F 按「是个什么服务」，R 按「这个数据影响谁的行为」 |
| `plugins.*`（23 个，含未启用的 20 个） | **插件与工具**（无论启没启用） | `plugins`（前缀直接命中） | 每个插件是 `admin/services/<app>` 一个独立菜单项 | `enrichments` / `integrations` | 分歧在**未启用插件**：R/L/F 都说「仍然属于插件域、只是不生效」；S 因为只有前缀信息，看不见启用状态，**做不到** |
| `Intent.function_call.functions` | **插件与工具**（它是一张「启用清单」，指向插件名） | `general`（顶层键 `Intent` 不匹配任何前缀） | `admin/services` | `enrichments` | S 把整个 `Intent` 树判成「通用」，而它实际是插件域的入口开关——**这是 S 最明显的误判** |

**另一处硬分歧：危险操作放哪。** LuCI 把 `reboot`（order 90）放 `admin/system` 末尾，
`flash`（刷写固件，order 70）也放 `admin/system`；Frigate 把 `maintenance` 单列成组。
本票不裁决（另一票），但登记：R 的 R1 会把「重启服务」「上传固件」「广播 SmartConfig」
都判为**编排者策略 → `系统`**，与 LuCI 的 `admin/system` 落点一致。

---

## 四、现存分组轴批判

### 4.1 页面的八个组与它们真正的轴

`GROUPS`（`config_page.html:170-179`）：

| id | label | 内容来自 | 实际轴 |
|---|---|---|---|
| `server` | 服务器 / 连接 | `buildGroupBody('server')` | **YAML 里 `server:` 段落 + 紧邻的顶层键** |
| `role` | 角色与对话 | `buildGroupBody('role')` | **角色相关的顶层键**（`prompt`、`wakeup_words`…） |
| `models` | 模型引擎 | `buildGroupBody('models')` | **`selected_module` + 6 个引擎子树**（只渲染选中的） |
| `intent` | 意图与插件 | `buildGroupBody('intent')` | **`Intent` + `plugins`** |
| `speech` | 语音与音频 | `buildGroupBody('speech')` | **`xiaozhi.audio_params` + `voiceprint` + `module_test`** |
| `device` | 认证与设备 | `buildGroupBody('device')` | **SmartConfig UI + `server.auth` + `context_providers`** |
| `ota` | 设备与固件 | `buildOta()`（`config_page.html:267`） | **不是配置**：`/api/devices`、`/api/firmware` 运行时数据 |
| `advanced` | 全部配置 | `buildAdvanced()`（`config_page.html:663`） | **只读 JSON 兜底** |

**判定：`server` 组按「YAML 相邻性」分组，`role`/`intent`/`speech` 按「上游作者写 YAML 时的心智分段」分组，`models` 才是唯一按组件分组的。**
证据：`buildGroupBody('server')` 拉的 16 个 `fld()` 里，只有 10 个以 `server.` 开头；
另外 6 个（`close_connection_no_voice_time`、`tts_timeout`、`tool_call_timeout`、`enable_websocket_ping`、
`delete_audio`、`tts_audio_send_delay`）是**裸顶层键**，它们进这一组的原因仅仅是模板里它们紧挨着 `server:` 段落写
（`server/config.yaml`，`server:` 块在 :9，`log:` 在 :44，7 个裸键在 :59-82）。
`speech` 组同理：`xiaozhi.audio_params.*`（顶层 `xiaozhi:` 在模板 :88）、`voiceprint`（:188）、
`module_test`（:100）三个互不相干的顶层键被硬凑成一组，只因名字里都有「语音/音频」的意思。

### 4.2 与规则 R 冲突的具体字段（old group → new domain）

**A. `server` 组 16 个字段中，10 个应当搬走**（`server.ip/port/http_port/websocket/vision_explain/websocket_backup` 这 6 个留下）：**

| 字段 | 现组 | R 判定的域 | R 的层 | 理由 |
|---|---|---|---|---|
| `tts_timeout` | 服务器/连接 | **引擎** | 更多设置 | 唯一消费点是 TTS 初始化：`server/core/utils/modules_initialize.py:104` `tts_config.setdefault("tts_timeout", ...)`，以及每设备注入 `server/core/connection.py:816`。它是 TTS 组件的实现细节，不是服务器参数 |
| `tool_call_timeout` | 服务器/连接 | **插件与工具** | 更多设置 | 唯一消费点是工具调用：`server/core/handle/intentHandler.py:170`、`connection.py:1359`。属于 function call 子系统 |
| `enable_websocket_ping` | 服务器/连接 | **系统**（或 `设备`？见下） | 更多设置 | 消费点 `server/core/handle/textHandler/pingMessageHandler.py:27`——传输层保活，与「哪个 TTS/LLM」无关，也不是监听参数 |
| `tts_audio_send_delay` | 服务器/连接 | **设备** | 更多设置 | 消费点 `server/core/handle/sendAudioHandle.py:130`，它影响的是**发往设备的音频包节奏**，是设备侧播放平滑的补偿 |
| `delete_audio` | 服务器/连接 | **系统** | 更多设置 | 消费点分散在 `modules_initialize.py:113,128`（建 TTS/ASR 时传入）+ `connection.py:815`。是文件生命周期策略，跨组件 |
| `close_connection_no_voice_time` | 服务器/连接 | **系统** | 常用 | 消费点 `receiveAudioHandle.py:114` + `connection.py:186`。它决定会话何时被回收——改为常量会让「空闲设备挂多久」不可调，属编排策略 |
| `server.mqtt_gateway` / `server.mqtt_signature_key` / `server.udp_gateway` | 服务器/连接 | **设备** | 更多设置 | 三者都是**向设备下发连接方式**的网关闭关（`ota_handler.py:236` 注释 `existing mqtt/websocket logic`），与 `server.websocket` 平级，而非与 `server.port` 平级 |
| `server.timezone_offset` | 服务器/连接 | **设备** | 常用 | 消费点唯一：`ota_handler.py:228` `"timezone_offset": ... * 60` 下发给设备。服务端自己不按它算时间 |

**B. 跨组误置（本票点名的两处）：**

| 纠缠 | 现组 | 问题 | R 的解法 |
|---|---|---|---|
| `tts_timeout` / `tool_call_timeout` / `enable_websocket_ping` / `tts_audio_send_delay` | 全在「服务器/连接」 | 它们各属**四个不同域**（引擎 / 插件与工具 / 系统 / 设备），只是 YAML 里挨着 `server:` | 按 R1 分别归 引擎（更多设置）、插件与工具（更多设置）、系统（更多设置）、设备（更多设置） |
| SmartConfig 配网 + `server.auth.*` + `context_providers` | 全在「认证与设备」 | 三者关系：SmartConfig 是**给设备配 Wi-Fi**（`server/core/api/config_handler.py:463`）、`server.auth` 是**设备连服务器的凭证**（`server/core/auth.py:22`、`server/core/api/ota_handler.py:54`）、`context_providers` 是**给 LLM 注入动态提示词**（`server/core/utils/context_provider.py:17`）——第三项和「设备」毫无关系 | SmartConfig → 设备（常用）；`server.auth.*` → 设备（`enabled`/`allowed_devices` 常用，`expire_seconds` 更多设置）；`context_providers` → **对话与角色**（它注入提示词） |

**C. 13 个真孤儿（`无归宿`）按 R 的落点：**

| 字段 | R 判定的域 | 层 | 依据 |
|---|---|---|---|
| `log.log_level` | 高级 | 更多设置 | `server/config/logger.py:81` |
| `log.log_format` / `log.log_format_file` | 高级 | 更多设置 | `server/config/logger.py:93,103` |
| `log.log_dir` | 高级 | 更多设置 | `server/config/logger.py:82`、`server/config/config_loader.py:114-115`（建目录） |
| `log.log_file` | 高级 | 更多设置 | `server/config/logger.py:83` |
| `log.data_dir` | 高级 | 更多设置 | `server/config/logger.py:84`（与 `log_dir` 并列，但语义是「数据目录」不是「日志目录」——**R 在此露出破绽**，见下） |
| `server.auth_key` | 设备 | 更多设置（**危险**） | 它是签发 token 的密钥：`server/app.py:50-62` 优先级 `server.auth_key` > `manager-api.secret` > 随机生成；消费点 `server/core/websocket_server.py:70`、`server/core/api/ota_handler.py:54`、`server/core/api/vision_handler.py:24`、`server/core/providers/tools/device_mcp/mcp_handler.py:244`。**改它 = 所有设备 token 失效**，属危险操作 |
| `mcp_endpoint` | 插件与工具 | 更多设置 | `server/core/providers/tools/unified_tool_handler.py:87` 读 `config.get("mcp_endpoint")`，为空/含「你的」则不启用（`mcp_endpoint_handler.py:16`）。它是外部 MCP 接入点，属工具域 |
| `voiceprint.speakers` | 对话与角色 | 常用 | `server/core/utils/modules_initialize.py:141` 要求 `url` 与 `speakers` 同时存在才启用；`server/core/utils/dialogue.py:126-131` 把说话人拼进提示词 |
| `xiaozhi.type` / `xiaozhi.version` / `xiaozhi.transport` | 设备 | 更多设置 | 整块 `xiaozhi` 作为 `welcome_msg` 原样回给设备（`server/core/connection.py:246`）。三者是协议声明，通常无需改 |
| `xiaozhi.audio_params.format` | 设备 | 更多设置 | 被设备 hello 覆盖：`server/core/handle/helloHandle.py:46-49` 用设备上报的 `format` 覆写 `welcome_msg["audio_params"]`。**它其实近乎只读** |

**R 在此处的破绽（登记为待裁决）**：`log.data_dir` 按 R1 会被判成「日志组件的实现细节」→ `log.*` 一族 → 高级，
但它的语义是 data 目录（数据库、固件库所在），不是日志。说明 R1 的「属主」问法对**跨用途的路径类字段**会失灵。
建议的补丁：R1 之外加一条 **R0 例外——「路径/目录类字段一律进 `系统`」**，因为路径是部署环境的事实，不是任何组件的行为。

### 4.3 页面结构性缺陷（不是字段问题，但影响「2 次点击可达」）

1. **`Intent.intent_llm.functions` 与 `Intent.function_call.functions` 是两份独立清单**，
   页面只渲染当前选中分支的那一份（`config_page.html:600` `const it = S.Intent?.[S.selected_module?.Intent]||{}`）。
   切换 `selected_module.Intent` 时，另一份清单**在新页面上完全消失**，用户会以为配置丢了。
2. **未启用插件的参数被折叠进 `<details>`**（`config_page.html:617`），而非独立的「插件库」页面。这与「引擎库」的决策方向不一致。
3. `server.auth.expire_seconds` 是页面凭空造出来的字段（模板无、生效配置无），
   保存后会真的写进 `.config.yaml` 并生效（`server/core/auth.py:22` 接受它）——属于**未登记的可变字段**。

---

## 五、参考来源

### 仓库内（file:line）

- `server/config.yaml`（模板，1176 行）：`server:` L19、`log:` L42、6 个裸顶层键 L57-71、`xiaozhi:` L86、
  `module_test:` L97、`wakeup_words:` L102、`mcp_endpoint:` L118、`context_providers:` L123、`plugins:` L131、
  `voiceprint:` L222、`prompt` L247、`selected_module` L275、引擎块 L294-1045。
- `server/data/.config.yaml`（本机用户配置，156 行）：`server.auth_key` L4、`websocket_backup` L8、
  `tts_timeout: 60` L98、5 个 Mlx 引擎 L56-95。
- `server/config/config_page.html`：`GROUPS` L170-179、`buildGroupBody` L519、`buildOta` L267、
  `buildAdvanced` L663、`fld/sel/listFld` L437-517、`engFields` L497、SmartConfig L651-660、`FIELD_META` L198。
- `server/config.yaml.example`、`server/config_from_api.yaml`（差集核对用）。
- 消费点：`server/app.py:50-62,79,107`；`server/core/auth.py:22,63`；`server/core/websocket_server.py:70-72`；
  `server/core/api/ota_handler.py:54,127-137,228,236,295`；`server/core/api/vision_handler.py:24,164`；
  `server/core/api/camera_handler.py:138`；`server/core/http_server.py:44-45,78-87,163-174`；
  `server/core/utils/util.py:522-537`（`get_vision_url`）；`server/core/utils/modules_initialize.py:104,113,128,141-148`；
  `server/core/utils/context_provider.py:17-22`；`server/core/connection.py:178,186,246-250,815-816,1359`；
  `server/core/handle/helloHandle.py:36,44-49,76,92`；`server/core/handle/sendAudioHandle.py:130,292-295`；
  `server/core/handle/receiveAudioHandle.py:114`；`server/core/handle/intentHandler.py:170`；
  `server/core/handle/textHandler/pingMessageHandler.py:27`；
  `server/core/handle/textHandler/listenMessageHandler.py:97-99`；
  `server/core/providers/tools/unified_tool_handler.py:35-92`；`server/core/providers/tools/mcp_endpoint/mcp_endpoint_handler.py:14-29`；
  `server/config/logger.py:58-103`；`server/config/config_loader.py:78,114-115`。

### 上游 xiaozhi-esp32-server（`xinnan-tech/xiaozhi-esp32-server`, branch `main`）

- `main/manager-web/src/views/ParamsManagement.vue:388-393` — **分组函数是纯前缀匹配**：
  `getTopGroup(paramCode)` `if (paramCode.startsWith('server.')) return 'server'` … `if (paramCode.startsWith('log.')) return 'log'`
  … `return 'general'`；一级分类五个 + 全部 = `paramManagement.group.{all,server,plugins,log,session,general}`；
  二级子分类 `getSubGroup()` 对 `server`/`plugins` 取 `parts[1]`，其余归 `_all`；
  已知二级名 `server.auth / server.connection / server.registry / server.resilience / server.metrics / server.tracing`。
- `main/manager-web/src/views/roleConfig.vue` — 角色配置页：`models` 数组（`:539-547`）把
  `vad / asr / llm / slm / vllm / intent / memory / tts` 八个下拉全部塞进一个 `el-form`，
  **按「角色会用到哪些组件」横切，而不是按配置文件的键**；插件启用清单用 `FunctionDialog` 弹窗（`:458-461` `@update-functions`）。
- `main/manager-web/src/i18n/zh_CN.js` — `paramManagement.group.*` = 全部/服务端/插件/日志/会话/通用；
  `paramManagement.groupHint` = 「按命名空间聚合展示」（**上游自己承认这就是命名空间轴**）。
- `main/manager-web/src/router/index.js` — 智控台一级路由：`/role-config`、`/voice-print`、`/device-management`、
  `/user-management`、`/model-config`、`/params-management`、`/knowledge-base-management`、
  `/server-side-management`、`/ota-management`、`/voice-resource-management`、`/voice-clone-management`、
  `/dict-management` 等。

### OpenWrt LuCI（`openwrt/luci`, branch `master`）

- `modules/luci-base/root/usr/share/luci/menu.d/luci-base.json` — 所有顶层节点都是
  `"action":{"type":"firstchild","recurse":true}` 容器，靠 `order` 排序，靠 `depends`（`acl`/`fs`/`uci`）条件显示。
- `modules/luci-mod-system/root/usr/share/luci/menu.d/luci-mod-system.json` — `admin/system/system`(order 1)、
  `admin/system/admin`(2)→`password`(1)/`pwpolicy`(2)…、`reboot`(90)。
- `modules/luci-mod-network/root/usr/share/luci/menu.d/luci-mod-network.json` — Interfaces(10)、Wireless(15)、
  Routing(30)、DHCP(40)、DNS(45)、Diagnostics(50)、Firewall(60, alias→`firewall/zones`)。
- `applications/luci-app-firewall/root/usr/share/luci/menu.d/luci-app-firewall.json` — `admin/network/firewall` →
  `firewall/zones`，含 `General Settings`(10)/`Port Forwards`(20)/`Traffic Rules`(30)。
- `applications/luci-app-firewall/htdocs/luci-static/resources/view/firewall/zones.js:112-115` —
  `s.tab('general'|'advanced'|'conntrack'|'extra')`；`:232` 起 `advanced` 收纳
  `device`/`subnet`/`masq6`/`family`/`masq_src`/`masq_dest`/`log`/`log_limit`。
- `applications/luci-app-firewall/.../view/firewall/rules.js:181-183,204-522` — 同一模式，`advanced` 收纳
  `direction`/`device`/`family`/`icmp_type`/`ipset`/`helper`/`log`/`log_limit`/`extra`（9 项），`general` 留
  `name`/`proto`/`src`/`dest`/`src_port`/`dest_port`/`target`/`set_helper`（8 项），`timed` 另列 7 项时间限制。
- `modules/luci-mod-network/htdocs/luci-static/resources/view/network/interfaces.js:518-524` —
  `general`/`advanced`/`physical`/`brport`/`bridgevlan`/`firewall`/`dhcp` 七 tab。
- `modules/luci-mod-network/.../view/network/wireless.js:1004-1005,1034-1063,1073-1077` — `advanced` 收纳
  `cell_density`/`distance`/`frag`/`rts`/`noscan`/`beacon_int`/`rxldpc`/`ldpc`/`isolate`。
- `openwrt.org/docs/guide-user/luci/luci.essentials`（已核原文）**不含菜单结构说明**；菜单结构的权威来源是
  上面的 menu.d JSON。Wiki 另有 `docs/techref/luci`。

### Frigate（`blakeblackshear/frigate`, branch `dev`）

- `web/src/pages/Settings.tsx:307-455` — `settingsGroups` 九组：`general` / `globalConfig` / `cameras` /
  `enrichments` / `system` / `users` / `notifications` / `frigateplus` / `maintenance`。
- `web/src/components/config-form/ConfigForm.tsx:198` —
  `/** Fields marked as advanced (collapsed by default) */ advancedFields?: string[];`
- `web/src/lib/config-schema/transformer.ts:16-17,461-553` — `advancedFields` 支持 `*` 通配路径，
  最终落成 `ui:options.advanced = true`（`:545-551`）。
- `web/src/components/config-form/section-configs/`：
  `genai.ts`（`*.base_url`,`*.provider_options`,`*.runtime_options`）、
  `record.ts`（`expire_interval`,`preview`,`export`）、
  `snapshots.ts`（`height`,`quality`）、
  `ffmpeg.ts`（`path`,`global_args`,`gpu`；global 另加 `input_args`,`output_args`）、
  `motion.ts`（`lightning_threshold`,`skip_motion_threshold`,`delta_alpha`,`frame_alpha`,`frame_height`,`mqtt_off_delay`）、
  `auth.ts`（`cookie_name`,`cookie_secure`,`session_length`,`refresh_time`,`failed_login_rate_limit`,`trusted_proxies`,`hash_iterations`,`roles`）、
  `mqtt.ts`（`stats_interval`,`qos`,`tls_ca_certs`,`tls_client_cert`,`tls_client_key`,`tls_insecure`）、
  `ui.ts`（`advancedFields: []`）、`database.ts`/`tls.ts`（`[]`）。
- `web/src/components/config-form/section-configs/types.ts` — `MessageSeverity`、`FieldConditionalMessage`、
  `SectionConfigOverrides` 的类型定义（说明「条件消息」是另一条合法轴：按条件提示，而非按位置藏字段）。

### Home Assistant（`home-assistant/frontend`, branch `dev`；`home-assistant/core`, `dev`）

- `src/panels/config/config-sections.ts:55` — `configSections` 共 20 个 group key：`dashboard`、
  `dashboard_external_settings`、`dashboard_2`、`connectivity`、`dashboard_3`、`backup`、`devices`、`automations`、
  `tags`、`voice_assistants`、`tools`、`energy`、`network_discovery`、`integration_credentials`、
  `integration_mqtt`、`lovelace`、`persons`、`areas`、`general`、`about`。
- `src/panels/config/dashboard/ha-config-dashboard.ts:194-199` — 只有 `dashboard` / `dashboard_2` / `dashboard_3`
  被拼成真正的落地页（`_pages()`）；其余 group key 是 quick-bar / 子导航的聚合容器。
- `src/common/config/can_show_page.ts`（全文）—
  `canShowPage = (isCore(page) || isLoadedIntegration(hass, page)) && (!page.filter || page.filter(hass))`。
  **当前可见性轴 = `adminOnly` + `component`（集成是否加载）+ `filter`；没有任何 advanced 门控**。
- `config-sections.ts` 中 `advancedOnly` 命中数 = **0**（grep 验证）。
- HA Advanced Mode 的退役轨迹（`gh api search/commits`）：`7bea54851`（2026-05-27）`Remove advanced mode completely (#52212)`、
  `9f4d35bc0`（2026-05-14）`Remove advanced mode navigation gating (#52045)`、
  `dbe46d3b3`（2026-03-19）`Remove advanced mode usages for apps area`。
- `home-assistant/core/homeassistant/data_entry_flow.py:657-666` —
  `show_advanced_options` 已 `@deprecated_function(..., breaks_in_ha_version="2027.6")`，且注释写明
  「During the deprecation period return True」，实现体是 `return True`。
- `developers.home-assistant.io/docs/config_entries_index/` 原文：
  > "Config entries are configuration data that are persistently stored by Home Assistant. A config entry is created by a user via the UI."
  > "Once created, config entries can be removed by the user. Optionally, config entries can be changed by the user via a
  > reconfigure step or options flow handler, also defined by the integration."
- 参考（同页）：`developers.home-assistant.io/docs/config_entries_options_flow_handler/` 原文
  > "An integration that is configured via a config entry can expose options to the user to allow tweaking behavior of the integration…
  > Components that want to support config entry options will need to define an Options Flow Handler."

---

## 附：本清单的复现方法

```bash
# 生效配置（服务端需在运行）
curl -s http://127.0.0.1:8003/xiaozhi/config/api/full > /tmp/full_config.json
# 叶子遍历（list 记 1 字段；list[dict] 展开元素键；空 dict 记 1 字段）
python - <<'PY'
import json
api=json.load(open('/tmp/full_config.json'))['config']
def leaves(o,p=''):
    if isinstance(o,dict):
        if not o: return [(p,'object')]
        r=[]
        for k,v in o.items(): r+=leaves(v,(p+'.'+k) if p else k)
        return r
    if isinstance(o,list): return [(p+'[i]','list')]
    return [(p,type(o).__name__)]
print(len(leaves(api)))
PY
```
