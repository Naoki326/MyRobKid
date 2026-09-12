# 音乐暂停/续播的恢复语义：反压暂停、两段式恢复、计时器驱动归还

ADR-0009 把「恢复语义」列为四层里样本数为 0 的两层之一（另一个是嵌套归还），故意不抽象，等音乐暂停/续播（issue #4）做出第一个样本。本 ADR 记录这个样本实际长成什么样：哪些接口被这一版证据定死了，哪些格子仍然留白。

## Decision

**1. 暂停 = 停止从流里取数据，纯靠 TCP 反压。**
worker 置位后不 Read、不解码、不推帧，但**不退出、不关连接**。worker 一停读，`http_client` 的 8KB 关卡（`OnTcpData` 的 `MAX_BODY_CHUNKS_SIZE`）就把它自己的 TCP 回调线程堵住，接收窗口随即关闭，上游（Mac 侧 ffmpeg）自己停在写阻塞上——实测设备慢读时 ffmpeg 全程存活、CPU≈0、下载 0KB/s。**不新增音频管线开关**：新开关意味着又多一个「谁在控制扬声器」的隐式状态，而反压是既有链路本来就有的行为。

- 暂停判定必须放在 HTTP 填充循环**之外**（放里面＝暂停期间仍把 `in_buf` 填满），且填充循环内也要能立刻停（同一次填充中刚置位的暂停不能读完这一轮）。
- `ring`（~0.8s）与 `in_buf`（≤64KB）暂停时**故意不丢**：那是快路径「零间隙」的来源。只有走重启路径才丢——跨连接的字节已经 stale。

**2. 恢复两段式，判定靠短超时试读。**
① 先试原连接：把 HTTP 超时临时压到 2.5s 读一次，一个字节都没到即判死；到了就把这批字节接进压缩缓冲，原连接继续用（零间隙）。② 判死则按位点重起流：起点 = 绝对位点回退 **2s 安全余量**（宁可重复一小段，不要跳词），clamp ≥0。

- `-1` 是「超时 / 连接硬错误」的折叠值（干净 EOF 是 `0`），所以探针必须自己开分支，**不能**复用「`size < 0` 即 decode_error」那条路；探完必须把超时还原成 8s——实例级超时留成 2.5s 会把后续一次真实卡顿误判成死连接。
- **重启由 worker 自己做**：ring / in_buf / rate converter 全归 worker 所有，三者必须同时换代，别的任务不许伸手进来。故 `StreamOnce()` 是可重入的一次流会话，返回 `kRestart` 让 `WorkerTask` 循环再来一次。
- 地址重拼是**替换**语义：`RemoveMusicStart` 再 `AppendMusicStart`。`AppendMusicStart` 是追加，直接重拼会让地址里出现两个 `ss=`（谁生效取决于上游解析顺序）。这条单独有宿主测试（`firmware/scripts/tests/test_music_url.py`）。
- 直播流位点无意义：重起流传 `start=0` = 重连到现场（ADR-0009「位点必须丢弃，而非拒绝」）。
- 两条都失败：提示音 + 回可交互状态，不留半死暂停态。**续播失败与自然播完必须可分**（`resume_failed` 与 `success` 是两个 bool），否则断线会被听成「歌放完了」。

**3. 自动续播由会话层的静默计时器驱动，不由归还事件驱动。**
`Application` 挂现成的 1Hz tick，另起 `pause_quiet_ticks_`（**不与 `clock_ticks_` 共用**——后者同时在驱动「每 10 秒打堆统计」，共用会被无关抖动清零）。只在「会话性暂停存在 + 设备在聆听 + 本轮还没说话」时递增，到 5s 触发续播。**不依赖「设备自然回待机」**：那条路是服务端 120s 无语音超时，中途还会触发 `end_prompt` 让机器人说一句告别语。

**4. 两种暂停语义分开记，且用户暂停不可降级。**
`paused_ + pause_kind_` 同锁读写（`PauseKind::kUser` 优先：用户说「暂停」之后又来一次唤醒，音乐会话性让位不得把它降回「可自动续」）。**种类只有播放器持有**，会话层不缓存副本：`Application` 每次按需现读（`IsMusicPauseConversational() = IsPaused() && GetPauseState() == kConversation`），缓存一份抄本迟早与事实分叉（照抄调用方入参就会把用户暂停误记成会话性暂停，用户随便聊一句音乐自己就回来了）。`CancelPauseAutoResume()` 因此只重置计时器，没有种类可清。

**5. 一个 bool 装不下「播放器是否在忙」，拆成三个谓词。**
`IsBusy()` = worker 存活（对象被占用、不可复用的底层原语）；`IsPlaying()` = worker 存活**且未暂停**（真在出声）；`IsPaused()` / `GetPauseState()`。五处落点统一：省电判定（`OnAudioChannelClosed`：暂停也占着连接与 PERFORMANCE 档，判成空闲会把 Wi-Fi 打回省电档、撕裂恢复后的音频流）、延迟起播分支（`StartMusic`：状态门只看对话状态，暂停态不该被误判成要延迟）、停止路径（`StopMusic`：能从暂停态停止，`IsBusy()` 在暂停时仍为 true）、按钮打断（`HandleToggleChatEvent` 与唤醒词同一路径，启动同一函数 `PauseMusic`）、以及 `HandleStateChangedEvent` 的 idle 分支（`EnableWakeWordDetection(!IsMusicPlaying())`：在出声才让位，暂停态照常可用唤醒词）。

本票只收敛成单一谓词，**不建框架**（ADR-0009 已把「抽通用仲裁者/引用计数」列为不在范围）。

**6. 会话性暂停的收尾在会话层做。**
暂停的 `HandleMusicFinished` 永远走不到（worker 不退出），所以资源归还（唤醒词、省电档）不能指望 finished 回调：暂停时由 `PauseMusic` 自己恢复唤醒词，续播时再关。

## 已定的接口（可恢复占用者）

ADR-0009 说「接口先对，实体后补」。这个样本把「可恢复占用者」的前半句定死了：

```cpp
enum class PauseKind { kConversation, kUser };  // 暂停语义只有两种，别拿四态状态当入参
bool Pause(PauseKind kind);                     // 非阻塞；返回 false = 没有活着的会话
bool Resume();                                  // 非阻塞；两段式由 worker 完成
void CancelPause();                             // 撤「该不该恢复」的登记，不碰 worker
```

`MusicPlayer::GetPauseState()` 返回 `PauseKind`（仅 `IsPaused()` 为真时有意义）；上报用的四态字符串由 `GetPlaybackStatus()` 做 `PauseKind → MusicPlaybackStatus::State` 的映射，口径不变。

会话层的续播结果也是三态（`Application::ResumeOutcome`）：`kResumed`（已出声）/ `kDeferred`（说话途中登记，答完即续——**不是**已继续）/ `kNothing`。工具返回值据此照实回 `"resumed"` / `"resume_pending"` / `"nothing_to_resume"`，不许把「已排队」说成「已继续」。

**仍然留白**（本票没有证据的部分，别照抄）：嵌套归还（会话性暂停期间通知拿走扬声器，通知结束该不该恢复音乐）、释放链与计时器的优先级。恢复的驱动源已证实是计时器而非释放链。

## 取证链

| 断言 | 观察点 | 判据 |
|---|---|---|
| 暂停期间不再取数 | `pipe:` 行 `read=0B/2s`、`pushed=0`、位点冻结 | 反压生效且位点未被推帧推进 |
| 两种暂停可区分 | `pipe:` 行尾 `PAUSED_CONV` / `PAUSED_USER`；`Music pause: kind=… pos=…` | 两种语义各有独立标记 |
| 走的是哪一段恢复 | `Music resume: mode=continue at=73.4s` / `mode=restart at=71.4s from=73.4s margin=2s` | 快慢路径各自的接缝位点可核（continue 的 at= 是冻结位点；restart 的 at= 是请求的重起位点、from= 才是暂停时的位点） |
| 自动续播发生了 | `Music auto-resume: quiet=5s` | 计时器真的到点（而非靠服务端超时） |
| 用户暂停绝不自动续 | 带 `PAUSED_USER` 的暂停跨度内是否出现 `Music auto-resume:` | 出现即失败 |

`tools/serial_telemetry.py --assert` 据此扩展：暂停跨度内的位点必须冻结、续播接缝必须在安全余量内（重启式按锚点自报的 `margin` 判、回退不超过 margin+0.5s 且不前进超过 0.5s；continue 必须落在冻结位点上 ±0.5s）、**推进率与挂钟偏差按净播放时长折算**（否则一段含暂停的正常抓取必然失败）。`--expect-restart` / `--expect-auto-resume` 用于要求本次抓取覆盖某条分支。

**锚点行与 pipe 行的精度故意不同**：`pipe:` 周期行（2 秒一拍）继续用整秒 `pos=73s`——周期采样不该伪造精度；三条锚点行（`Music pause: pos=`、`Music resume: mode=continue at=`、`mode=restart at=/from=`）改用一位小数（`73.4s`），因为 spec 的验收缝是「恢复后位点从原处继续（±0.5s）」，整秒量化够不着。两者共用同一个位点折算（`PositionSeconds` / `PositionSecondsF`），不会分叉。

**换歌（替换语义）的回调不能被旧 worker 偷走**：完成回调在**会话开始时**就被 worker 快照，会话结束时由它交还给 `WorkerEntry`，在 `worker_running_` 清零之后触发。若改读共享成员，旧 worker 会在 `Start()` 装了新回调之后取走新回调、用旧会话的结果调它——新会话的收尾（唤醒词与省电档归还）就永远不会执行。

## Consequences

- 暂停期间那条 TCP 连接一直挂着（上游 nginx `proxy_read_timeout` 一小时、CDN 侧也可能回收）。这正是恢复必须分层的原因，也是**位点必须由设备自己记账**的原因——不能指望连接还在。
- 暂停很久（超过上游超时）后仍能续上，走的就是慢路径；快路径只覆盖「上一条连接还热着」的短暂停。
- 位点**记账**仍是整秒量化，但锚点行带一位小数、接缝容差因此收到 ±0.5s；这是「不跳词」而不是「精确到毫秒」的验收。重启式续播的回退由锚点自报的 `margin` 放行（回退 ≤ margin+0.5s）。
- 播放自然结束与续播失败的分岔（`resume_failed`）在本票只做到「可辨提示音 + 回可交互状态」；屏幕说明与推给服务端属可见化那张 spec（issue #12）。

## 后续修订（issue #7）

上面的决策 2 末段写的「**续播失败与自然播完必须可分**（`resume_failed` 与 `success` 是两个 bool）」已被 ADR-0014 取代：两个 bool 装不下第三种收场（用户主动停止），它俩都与「链路中断」共用同一种 Bool 组合。现在 `FinishedResult` 带的是收场分类（`MusicEnding`）+ 收场位点，两个 bool 已删除。决策 1–6 的其余部分（反压暂停、两段式恢复、计时器驱动、两态暂停、谓词分家、会话层收尾）不变。
