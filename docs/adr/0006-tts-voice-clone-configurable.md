# TTS 音色克隆配置化（湾湾小何本地克隆）

MyRobKid 服务端的 MLX TTS provider 支持 `ref_audio` / `ref_text` 条目字段，把克隆音色做成配置切换：`MlxWanwanStreamTTS`（湾湾小何，逐句流式）与 `MlxStreamTTS`（元宝娃娃音）等条目经 `selected_module.TTS` 一行切换。新增 `tools/transcribe_ref.py`：音频转 24kHz mono WAV + SenseVoice 转写，一键生成 `ref_text`。

## Context（取舍）

- 用户要本地湾湾小何（火山 `zh_female_wanwanxiaohe_moon_bigtts`）。查证：开源的是 TTS 引擎（Qwen3-TTS/Seed-TTS 等），该音色是火山商业资产，无公开权重；GitHub/HuggingFace 亦无样本。本地 MLX 是克隆式引擎，给参考音频即复刻（元宝、花生先例），故路径为「样本 → 克隆」。
- MLX 服务 HTTP 接口本就透传 `ref_audio`/`ref_text`（`generate_tts` 支持），服务端零改动，仅 provider 补传参。
- 样本来源（用户侧，任一）：剪映 App 文本朗读（剪映同款）导出、豆包 App 湾湾小何音色朗读录音、火山控制台音色页试听。要求 10~15s 干净单人语音，无 BGM。
- 实际样本：B 站小智演示视频（BV1AiQ1YqEyn）提取——fsmn-vad 分段 + SenseVoice 转写 + 基频甄别（男声 ~100Hz / 小何 ~350Hz）切出纯小何段，拼接 16.1s，响度归一，文本人工校对；参考成品 `~/.hermes/models/voice_profiles/wanwan_ref.wav`。克隆效果受参考音频（外放录音）局限，与原音色有差距，可后续用更干净的样本替换同路径文件即可。

## Consequences

- `ref_audio` 必须写绝对路径：MLX 服务端 `os.path.exists()` 不展开 `~`。
- 服务端存在**静默回退**：`ref_audio` 不存在时回退元宝音色，`ref_text` 缺省回退元宝文本（与音频不匹配会劣化克隆效果）。对策：切换 `selected_module.TTS` 前确认两字段就绪（provider 启动时对缺失文件打 warning，但不阻断）。
- 样本未到位前 `MlxWanwanStreamTTS` 条目仅备位，`selected_module.TTS` 维持 `MlxStreamTTS`。
- 教训（0828 音质事故）：参考音频提取必须保持高采样率直出 24kHz。首版参考误用转写用的 16kHz 文件，8kHz 以上高频全失，克隆合成发闷（8-12k 能量 0.04%）；从 B 站 44.1kHz 原始音轨重提后恢复至 1.98%（追平元宝标杆 2.03%）。`tools/transcribe_ref.py` 已固定输出 24kHz，勿再手抄 16k 中间产物。
