# 音乐位点口径：按已推入播放队列的 PCM 记账，起点为绝对值

ADR-0009 把「恢复到哪」列为恢复语义的第二个问题（位点须减掉 ring 预缓冲）。在实现位点记账（issue #3）时必须把「在哪测量」定死，否则暂停/续播（issue #4）一落地就会跳词或重复。

## Decision

1. **测量点在 `TryPushPcmToPlaybackQueue` 成功处**，按「已推入播放队列的 PCM 量」折算位点，**不用已读字节**：读侧含 ring 预缓冲（32 帧 ≈ 0.8s，为吸收网络抖动而存在），把预读算进「已播」，续播必然重复约 1 秒。
2. **位点报绝对值**：`起点（本条流的 ss=）+ 已推帧折算秒`。用户说「从 1 分钟放」，起播后立刻问「放到哪了」，答案应是 ≈60s 而非 ≈0s；续播按绝对位点向代理请求新流。
3. **直播流不记账**：`MusicContentMeta::live` 为真时不累计样本、状态上报标 `seekable:false` 不带位点——直播的「恢复」是重连到现场，不是定位。
4. **残余误差显式登记**：播放队列本身最多 2 帧（`MAX_PLAYBACK_TASKS_IN_QUEUE = 2`，约 50ms），低于「数秒」容差一个量级，不为其再加输出侧计量钩子——为 50ms 去动 TTS/通知共用的音频输出路径不值。
5. **状态上报四态互斥**：`idle / playing / paused_conversation / paused_user`，后两态由暂停票落用；**会话结束即回 idle 并丢弃位点**——空闲或暂停态报出一个还在走的陈旧位点，比没有位点更糟。
6. **状态上报只写一处**：设备状态 JSON 的组装统一在 `WifiBoard::GetDeviceStatusJson`，删除 minicam 板卡的整份重复实现（那份还漏了电量与芯片温度；音乐字段因此只加一次）。
7. **EOF 尾巴不死等**：流结束时长可能残留标签/垃圾帧，解码器既不消费也不产出。此时按正常播完收尾（`success = true`），否则会话永远挂在「在播」——唤醒词不恢复、位点停在旧值。判据是「EOF + ring 空 + 本轮零消费零产出零推帧」连续 10 轮（~100ms），先让队列里最后两帧播完再判。

## 取证链（为何不是别的测量点）

| 候选测量点 | 误差 | 否决理由 |
|---|---|---|
| 已读字节 | 预读 + in_buf 压缩缓冲，秒级偏高且随码率/网络抖动 | 读是为了播，不是播了 |
| 解码完成处 | 与测量点 1 差一个 ring（0.8s） | ring 里的帧随时可能因取消被丢弃 |
| 已推入播放队列（**选定**） | ≤ 2 帧播放队列残余（~50ms） | 正是「听到的内容」的前沿 |
| I2S 实际渲染 | 理论最准 | 需在 TTS/通知/音乐共用的输出任务里加钩子，波及面不成比例 |

## Consequences

- 位点在 `MusicPlayer` 内部记账（`pushed_samples_`，worker 每推成一帧原子累加），快照经 `MusicPlayer::GetPlaybackStatus()` → `Application::GetMusicStatus()` → `WifiBoard::GetDeviceStatusJson()` 单线向上，状态上报与 `pipe:` 遥测同源同值。
- `pipe:` 遥测行增加 `pos=<绝对秒>s`（直播 `pos=live`）；起流另打锚点行 `Music stream started: title=… duration=… form=… start=…`。`tools/serial_telemetry.py --assert` 据此断言：位点增速不超墙钟（预读入账即超速）、位点不低于起点（相对/绝对口径）、live 不出数字位点、起流锚点必须抓到。
- 四态字符串口径固定为 `idle / playing / paused_conversation / paused_user`（后两态由 #4 落用；报告的是这两态**冻结时**的位点，不随时间推进）。
- 暂停（issue #4）冻结位点即可正确续播；「恢复回退 1–2s 安全余量」（ADR-0009）叠加在绝对位点之上。
- `MusicPlaybackStatus` 定义在 `music_player.h`（与记账同处），不另立会话单例：实测状态只由播放器持有，再造一个单例只会是第二份真相。
