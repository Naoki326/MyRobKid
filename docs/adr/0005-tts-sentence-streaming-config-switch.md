# TTS 逐句合成与整段合成并存，配置切换

对话回复的 TTS 从整段合成切为**逐句合成**（`server/data/.config.yaml` 中 `TTS.MlxStreamTTS.split_sentences: true`），仍用本机 MLX TTS 服务（launchd `com.yuanbao.mlxtts`）克隆的官方娃娃音。整段条目 `MlxTTS`（`split_sentences: false`）保留，两条目经 `selected_module.TTS` 一行切换，见 ADR-0004。

## Context（取舍）

- 用户要求复刻官方云"很流畅"的听感：早出声、边合成边下发。官方是 chunk 级双向流式；本地 MLX 服务只有整段 `/tts` 端点且单线程，不对它做流式/并发改造（ADR-0004 约束），故采用服务端逐句流水线：LLM 流式输出 → 首句按逗号快切 → 逐句合成 → opus 帧按 60ms 节奏下发，合成与播放并行。
- 实测（反馈回路，同问"给我讲一个睡前小故事"）：整段首包 30.9s → 逐句 7.6s；短问 7.1s。MLX 合成 RTF 0.84~1.21（语速 0.85），长句富余、连续短句句间可能露 0.5s 级 gap。
- 长回复轮首包 18.1s 的主因是 LLM 网关（newapi.thirking.top）流速仅 ~14 tok/s，非 TTS 环节；提速的下一刀在 LLM，不在 MLX。

## Consequences

- 首包不再随回复长度增长；代价是句间韵律独立合成（不如整段连贯），真机听感待验，不佳可切回 `MlxTTS`。
- 切句残余的孤立标点段（如"）"）会在 `base.to_tts_stream` 入口被过滤，避免对共享 MLX 服务无效重试（防雪崩，见 ADR-0004）。
- 逐句期间 MLX 服务被串行占用时间更长（边播边合成），hermes 其他消费方排队感知会更明显。
