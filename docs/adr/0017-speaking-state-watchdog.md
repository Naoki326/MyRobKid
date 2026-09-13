# `speaking` 态的兜底退出：判据是「放完了没人喊停」，不是「待太久」

设备进入 `speaking` 后**只在收到服务端的 `tts stop` 时才退出**（`application.cc` 的 `stop` 分支）。那条消息没到，设备就停在 `speaking`——不再聆听、唤醒词只认 `idle`，用户侧表现为「它说完了但没反应」。实测有一轮服务端在 `17:51:56` 就发了 `tts stop`，设备到 `17:52:43` 才退出，中间卡了 **47 秒**（期间内存平稳、主循环活着、无 `Channel timeout`）。本 ADR 记录兜底这条路径的决策（issue #33）。

## Context

三件事在实现前并非显然：

1. **这是一个独立缺陷，不是 #32 的修复。** #32 现象是「永久卡死、只有重启恢复」，本条是「47 秒后自恢复」——两者未必同因。但无论 `tts stop` 为何迟到（网络写阻塞、丢包、服务端卡顿），**一个「等对端发结束信号才能退出」的状态在对端不发时就会停住**。即使 #32 的真凶修好，这条路径本身仍然脆弱。
2. **「时长」当判据必然误伤长回答。** 正常一轮 TTS 3–20 秒，但讲故事那种长回答实测有连续 20+ 秒的 TTS。任何「`speaking` 超过 N 秒就退」的判据都要在「盖过长回答」与「卡死恢复要快」之间二选一，而两者不可兼得——这是判据本身选错了。
3. **`IsPlaybackIdle()` 正是缺的那个信号。** 它已在 `MAIN_EVENT_PLAYBACK_DRAINED` 分支里用过，回答的是「播放队列是否已空」。用「**放完了却还在 `speaking`**」替代「`speaking` 持续太久」，长回答天然豁免（只要还出声就永不计数），只有真放空了却没人喊停才触发。

## Decision

**1. 判据是「TTS 已放完却仍在 `speaking`」，不是「`speaking` 持续太久」。**
两个事实现取，任一不成立即清零计数：设备在 `kDeviceStateSpeaking`，且 `TtsPlaybackDrained()`（`AudioService::IsPlaybackIdle()` **且** `tts_buffer_` 为空）。后半句是为整段预缓冲模式（`CONFIG_TTS_PLAYBACK_FULL_PREBUFFER`）留的：那种模式下音频先攒在 `tts_buffer_`、不推播放队列，只判 `IsPlaybackIdle()` 会把「还在缓冲一条长回答」误判成「已放完」，10 秒就误打断。默认的流式模式（本仓实际构建的那档、也正是 #33 复现的那档）下缓冲恒空，二者等价。

**2. 判据抽成纯逻辑模块，与设备资源解耦。**
`main/audio/speaking_watchdog.{h,cc}` 只吃两个 bool、吐一个动作（`kNone` / `kDrainedUnstopped`），一个 ESP 头文件都不引，故可用宿主 C++ 编译器测（`firmware/scripts/tests/test_speaking_watchdog.py`）——判错了就是两种真机故障（判早了打断长回答、判晚了永久沉默），值得在没有设备的情况下钉住。真正的状态转移、阈值常量与日志归 `application.cc`。

**3. 复用现成的 1Hz `CLOCK_TICK`，不另起定时器。**
`UpdatePauseAutoResume()`（同一条 tick 路径）已经是「用 tick 做看门狗」的现成范例，本票照抄。阈值 `kSpeakingWatchdogTicks = 10`：判据本身已排除「还在出声」，所以这里只需要盖过服务端逐句流式合成时最长的句间间隙（实测 <2s）——留 5 倍余量，又不至于让用户在卡死后再等 47 秒。

**4. 触发恰好一次，且退出路径与 `tts stop` 正常到达**完全**同一条。**
`Tick()` 在数满阈值的那一拍返回 `kDrainedUnstopped` 并把计数清零，之后要重新数满才会再触发——不会每拍刷一条日志。退出经抽出的 `ResolveSpeakingExit()`：延迟的音乐先起播、答话途中登记的续播现在接上、手动模式下回待机，否则进聆听。`tts stop` 那条路与看门狗共用这一个收口（并在其中 `Reset()` 计数），免得两条路各自抄一份「去哪」。

**5. 触发时串口有一条与正常 stop 可分的日志。**
`Speaking watchdog: drained 10s without tts stop - leaving speaking (issue #33 fallback)`（`ESP_LOGW`）。行内的秒数是**阈值**而非实测间隔（计数到点即清零，读不到实测值）；判读时不看「有没有退出 `speaking`」（两条路都退），而看有没有这一行，并核它与上一行 `State: … -> speaking` 的挂钟差≈阈值。

**6. 迟到的真 `tts stop` 是安全的空操作。**
看门狗已把状态转走（`listening` / `idle` 等），`tts stop` 分支的 `if (GetDeviceState() == kDeviceStateSpeaking)` 不成立，不会二次转态；此后再到的音频包也因状态已不是 `speaking` 而在 `OnIncomingAudio` 里被丢弃——与既有的 `AbortSpeaking` 同一口径。

## 待确认的前提（不当成结论）

按 evidence-rules §4，本票**没有**确认「为什么那 47 秒里 `tts stop` 没有生效」。可观测的事实只有：服务端在 `17:51:56` 发了 `tts stop`、设备到 `17:52:43` 才退出、期间内存平稳且主循环活着。**业界领先假设**是「设备没收到那条消息 / 它被退迟处理」，「丢包 / 写阻塞 / 服务端卡顿」各是对该假设的未验证细化。本条不依赖那个假设成立：无论原因为何，一个大音「只要对端不发结束信号就会永久停住」的退出条件本身就不鞉固——它才是本票要修的实质，#32 追的是「为何不发/不到」。

## 不假装的缺口

- **整段预缓冲模式丢 `tts stop` 仍无兜底。** 那种模式下音频攒在 `tts_buffer_`、只在 `tts stop` 时 `FlushTtsBuffer()`；stop 丢了缓冲没人冲、音频永不播，看门狗的判据（缓冲为空）也永远不成立。本票复现与验收都在默认的流式模式下——这条缺口是**明说的**，不假装它被覆盖。
- **流式模式下，服务端任何一次 >10s 的停顿都会被当成「放完了没人喊停」。** 每包只有 60ms，所以解码/播放队列的入水完全取决于服务端持续送包；一旦送包停顿（长 LLM 输出的句间思考、网络抖动的长尾），队列就是真的空了。现有代码里 `MAIN_EVENT_PLAYBACK_DRAINED` 与 `pending_listening_start_` 这套机制本身就证明「队列在回答中途会短暂排空」——它们处理的是亚秒级的排空，而 10s 阈值只能盖过 10s 以内的停顿。这是「兜底必须有个阈值」的固有代价：阈值越小恢复越快、越容易被真停顿误触；10s 是在 5 倍子、句间间隙余量与「别让用户再等 47 秒」之间的折中。若真机上出现提前退出的情况，优先的修法是**加深判据**（如要求「距最后一包到达已 N 秒」），而不是单纯拉大阈值。
- **与 #32 的关系**：本条是**兜底**，不是 #32 的修复。真凶（`tts stop` 为何迟到）仍由 #32 追。

## 取证链

| 断言 | 观察点 | 判据 |
|---|---|---|
| 放完却无人喊停会退出 | `Speaking watchdog: drained 10s without tts stop - leaving speaking` + 紧随的 `State: speaking -> …` | 锚点行出现在无 `tts stop` 的间隔里 |
| 长回答不被误打断 | 一轮连续 20+ 秒 TTS 的 `speaking` 跨度内**不出现**该锚点行 | 判据是 TTS 已放完（还出声就不计数），不是时长 |
| 与正常 stop 可区分 | 同一抓取里「有锚点行」与「有 `tts stop`」互斥 | 锚点行只在 stop 缺席时出现 |
| 阈值可核 | 锚点行与上一行 `State: … -> speaking` 的挂钟差 ≈ `drained 10s` | 行内秒数是阈值，可与挂钟差互核 |

契约测试（纯逻辑，不需真机）：`firmware/scripts/tests/test_speaking_watchdog.py`——连续 N 拍才触发、长回答（队列非空）永不触发、句间间隙不累积、触发恰好一次、离开 `speaking` 一拍即清零、阈值 1 与 `Reset()`。

## Consequences

- 设备对「对端不发结束信号」不再无限期停住：任何一次 `tts stop` 丢失最多让用户多等 10 秒，而不是永久哑掉。
- 代价是一条与真 stop 并行的**第二条**退出路径，两者必须在同一处收口（`ResolveSpeakingExit()`）——这是本 ADR 决策 4 的硬约束，日后改动这条路径要同时顾及两者。
- 阈值与判据的语义分居两处：数值（`kSpeakingWatchdogTicks`）在 `application.h`，判据逻辑在 `speaking_watchdog.cc`。改判据只动后者，改手感只动前者。
