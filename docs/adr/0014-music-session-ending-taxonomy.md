# 音乐会话收场分类：原因驱动提示音与屏幕，用户主动停止永不报警

「播完了」与「断了」在音频流上都表现为流结束，设备却必须把它们说清：前者用户想再点一首，后者用户要检查网络。issue #7 之前 `MusicPlayer::FinishedResult` 只带两个 bool（`success` / `resume_failed`），于是**用户按停止**与**链路中断**折叠成同一种组合（`success=false` 且 `resume_failed=false`）——没有原因可判，就没有反馈可给。本 ADR 记录这次把「取消」升级为「带原因的取消」、并据此定下音/屏/遥测契约的取舍。

## Decision

**1. 取消携带原因，取消不再是 bool。**
`std::atomic<bool> cancelled_` → `std::atomic<CancelCause> cancel_cause_{kNone}`，取值 `kNone / kUserStop / kReplace / kStartAbort`（换歌、停止指令、起流自我拆掉三条路径）。**单一事实源**：不再有「两个标记互相矛盾」的窗口（例如 `cancelled_` 说停了、`replaced_` 说没换歌）。`Cancelled()` 由 `cause != kNone` 派生。写入用**先到者胜**的无锁 CAS——不取 `mutex_`：取消会从已持该锁的路径（`Start()` 的换歌分支）上调用，取锁会自锁。

**2. 收场由五个事实位推导，顺序固定。**
`DeriveMusicEnding(played, drained, cancelled, replaced, attempted_restart)`，优先级 `replaced > start_failed > completed > stopped > resume_failed > interrupted`：

| 优先级 | 条件 | 结局 | 理由 |
|---|---|---|---|
| 1 | `replaced` | `kReplaced` | 旧会话收场归新会话，报什么都会打扰用户 |
| 2 | `!played` | `kStartFailed` | 没出过声就没有「中断/停止/续播失败」可言 |
| 3 | `drained` | `kCompleted` | 播空与按键的赛跑里，真放完了就别说断了 |
| 4 | `cancelled` | `kStopped` | 用户按停后恰好又重连失败，那也是用户停的 |
| 5 | `attempted_restart` | `kResumeFailed` | 走过重连仍失败 ≠ 静默断流 |
| 6 | 其余 | `kInterrupted` | 出过声、没播空、没人取消 |

`played` 取自 `first_frame_decoded_`（**本次会话**至少解出一帧，`start_mutex_` 保护）；`drained` 取自 `StreamEnd::kDrained`；`cancelled`/`replaced` 取自取消原因；`attempted_restart` 为 worker 循环里是否走过重连。五个事实位在 `WorkerTask` 返回前一次读齐——那时 `worker_running_` 仍为真，下一次 `Start()` 还没覆盖会话状态。

**3. 提示音与屏幕文案按结局分派，三种收场各有其形。**
自然播完 → `OGG_SUCCESS` + 「播放结束 / Playback finished」；链路中断与续播失败 → `OGG_EXCLAMATION` + 「播放中断 / Playback interrupted」；用户主动停止 → **无声** + 「已停止 / Stopped」；换歌与起流失败 → 无声无屏（屏幕归新会话 / 起播那一刻已报过错）。复用既有音效资源（`success.ogg` / `exclamation.ogg`），不新增。

**4. 屏幕文案与提示音都在状态转换之后落地。**
进 `kDeviceStateListening` 时两件事会吃掉先写的内容：`idle` 分支会 `ClearChatMessages()`（抹屏幕），Realtime 聆听模式下 `StartListeningAudio()` 会 `EnableVoiceProcessing(true)` → `ResetDecoder()`（清 decode 队列，连刚入队的提示音一起）。因此**发声与写屏都放在 `Schedule()` 的 lambda 里、`SetDeviceState` 之后**——主循环同一轮先处理 `STATE_CHANGED` 再处理 `SCHEDULE`；AutoStop 模式下转态本身还有 `IsPlaybackIdle()` 闸门，提示音先播完才开聆听，正是想要的顺序。

**5. 换歌的「跳过」是显式分支，不只靠忙碌判据。**
`ending == kReplaced || IsMusicBusy()` → 打 `skipped=new_session` 后立即返回，不碰省电档、唤醒词与屏幕。（原实现仅靠 `!IsMusicBusy()`；有了 `kReplaced` 之后判断由原因给出，忙碌判据保留用于兜住旧 worker 的陈旧回调。）

**6. 收场原因进遥测，声音分支不靠耳朵验。**
播放器（`MusicPlayer`）：`Music ended: reason=<name> played=0|1 pos=<x.y>s|live url=…`（真故障走 `ESP_LOGE`）；应用（`Application`）：`Music feedback: reason=<name> pos=… sound=<success|alert|none> screen=<ended|interrupted|stopped|none> wake_word=<on|off> interactive=<scheduled|already|wake_word_only|none>`，换歌为 `Music feedback: reason=… pos=… skipped=new_session`。结局名（`completed/interrupted/resume_failed/stopped/replaced/start_failed`）是遥测契约，两两不同、非空、无空格。

`tools/serial_telemetry.py` 据此新增 `--expect-ending <reason>`（可重复/逗号分隔）与九条断言：`ending_reason_recorded`（原因在已知六种内）、`ending_played_flag_consistent`（`played=0` 只能是 `start_failed`）、`ending_has_feedback`、`ending_feedback_matches_reason`（逐条比对照表）、`ending_cues_are_distinguishable`（两种音必须不同且都不静音）、`user_stop_never_warns`（硬不变量）、`pause_is_not_an_ending`（暂停跨度内不得出现收场锚点）、`playback_returns_interactive`、`ending_expected_seen`（`--expect-ending` 时）。**这些断言只在抓到收场锚点时出现**——issue #3/#4 那套位点核验不受影响；反过来，一次根本没出声的抓取（起流失败：没有起流锚点也没有位点）会走 `position_checks_not_applicable` 提前收尾，不会用五条必然失败把真结论淹掉。

## 为什么把纯逻辑单独拆一个文件

`music_ending.{h,cc}` 一个头文件都不引（连 `<cstddef>` 都不用），不含 ESP 头。收益是这条最容易错、又最只能靠耳朵发现的映射（音效分派）变成**宿主编译可测**的纯函数：`firmware/scripts/tests/test_music_ending.py` 把六个结局的推导、优先级边角（`replaced` 压过一切、`drained` 压过 `cancelled`、`cancelled` 压过 `attempted_restart`）、名字唯一性、以及「用户停永不报警」逐条钉住。Lang 字符串与屏幕输出留在 `application.cc`（依赖设备资源），不往这个文件里拉。

## Consequences

- **`FinishedResult` 换形态**：`{MusicEnding ending; double position_s; bool live;}`，`success`/`resume_failed` 两个 bool 与访问器删除。ADR-0013 决策 2 末段关于「两个 bool」的表述已被本 ADR 取代（该处已加修订说明）。
- **行为变化（需真机确认）**：所有非跳过收场现在走同一条公共路径（撤自动续播 → 归还省电 → 恢复唤醒词 → 出声/写屏 → 回可交互）。与旧实现相比，**唯一的真实差异是提示音与屏幕文案的落地时机**：旧实现在 `resume_failed` 分支里直接 `PlaySound` 后 `return`，新实现把出声与写屏放进 `Schedule` 回调、在 `SetDeviceState` 之后执行。旧实现的 `return` 并未跳过省电归还与唤醒词恢复——那两行在 `if (resume_failed)` **之前**，两条路径都走得到（核对 `37b10a9:firmware/main/application.cc:1455-1495`），它只是跳过了一段功能相同的重复 `Schedule`。改动理由是进 Listening 的路上 `ResetDecoder()` 可能吃掉刚入队的提示音（Realtime 聆听模式下 `StartListeningAudio → EnableVoiceProcessing(true)`），延后落地才留得住音。
- **不做会话 id 校验**：曾考虑给每次会话编号、回调带 id、应用侧只认得当前 id。放弃理由：`kReplaced` 已把换歌收场显式分类，加上 `IsMusicBusy()` 兜底，陈旧回调想造成错误副作用必须先同时骗过这两个判据；引入 id 会给每个回调加一层与收益不成比例的状态。
- **按钮打断仍是暂停、不是收场**：`PauseMusic(PauseKind::kConversation)` 路径不产生 `Music ended`——中断瞬间没有 「播放中断」音，用户要听见的是他自己的操作被照办。`pause_is_not_an_ending` 断言把这条钉在遥测里。
- **留白**：收场原因没有推给服务端（那属可见化 spec，issue #12）；`Player 名曲/作者`显示、暂停位点显示属 issue #8/#11，本票未动。
- `PROJECT_VER` 2.4.26 → 2.4.27；`lang_config.h` 重新生成（三个新 key）。
