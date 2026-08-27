# TTS 整段合成，复用本机 MLX TTS 服务

对话回复采用**整段合成**（`server/data/.config.yaml` 中 `TTS.MlxTTS.split_sentences: false`）：LLM 输出完毕后一次性把整段文本交给本机 MLX TTS 服务（launchd `com.yuanbao.mlxtts`，127.0.0.1:9753，Qwen3-TTS 克隆音色），合成完再下发。配套 `tts_timeout` 提至 60s、TTS 失败重试从 5 次降为 2 次。

## Context（取舍）

- 逐句合成首包更快（~5s vs 整段 10~20s），但句间韵律断裂、听感"一顿一顿"；用户明确选择听感连贯、接受首包变慢。
- MLX TTS 服务是共享设施（hermes 生态多消费方），单线程实现：客户端超时重试会在其队列中堆积、引发雪崩（Broken pipe + 排队到小时级），故必须收紧重试并放宽超时。
- 不为对话链路对该服务做流式/并发改造——它不属于本项目管辖。

## Consequences

- 首包延迟随回复长度增长；若未来要提速，方向是"首句快速 + 剩余整段"的混合切分，而不是动 MLX 服务本身。
