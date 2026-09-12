# 屏幕出口：曲目在状态转移之后写，结束态交还所有权

音乐在设备的**空闲态**下播放（ADR-0008 的音乐模式），而空闲态会把消息区清空、状态栏写成「待机」——于是放着歌、屏幕写着待机（issue #6 的立论：音乐会话不是一等状态，只活在播放器与一条 TCP 连接里）。#7 已经解决了**收场**的屏幕输出（`播放结束` / `播放中断` / `已停止`），本 ADR 记录剩下那半：**播放中**与**结束态之后**的消息区呈现（issue #8）。

## Context

三件事在实现前并非显然：

1. **写屏时机与「谁会清屏」耦合。** 设备从说话态回到空闲态时会 `ClearChatMessages()`，而音乐恰恰在这条路径上起播（`tts stop` → `LaunchPendingMusic()` → `SetDeviceState(idle)` → `music_start` 任务 → 第一帧 → `Start()` 返回）。如果曲目在 `Start()` 返回后直接写屏，它会被**同一轮或下一轮**主循环里那条 idle 分支的清屏吃掉——屏幕上什么都没有，正是要修的症状本身。
2. **清屏的路径不止一条。** `HandleStateChangedEvent` 的 idle 分支、`OnAudioChannelClosed`、`Alert`/`DismissAlert`、起播失败回落……多条路径都会清或覆写消息区；而这些都是应用层动作，与音乐会话在时间上无关。
3. **「音乐期间不再显示待机」与「不改状态栏」是两件事。** 状态栏归状态机（`待机`/`聆听中`/`说话中`），消息区归这条会话。母 spec（#6）明确本次**不动状态栏**，只动消息区。

## Decision

**1. 曲目文本经 `Schedule()` 排出，永远晚于触发它的那次状态转移。**
`StartMusicNow()`（起播，含换歌与「说完话再播」）与 `ResumeMusicNow()`（续播）在成功返回前只**登记**一次写屏，真正落屏在主循环的下一个 SCHEDULE 拍。主循环 `Run()` 同一轮先处理 `MAIN_EVENT_STATE_CHANGED`、后处理 `MAIN_EVENT_SCHEDULE`，因此曲目必然晚于它自己触发的那次 `-> idle` 的 `ClearChatMessages()`。这与 ADR-0014 决策 4（`HandleMusicFinished` 的出声与写屏）是同一个机制、同一个理由——只是那次要躲的是 Listening 的 `ResetDecoder()`，这次要躲的是 idle 的清屏。

**2. 消息区有归属权：`music_screen_owns_content_`。**
idle 分支不再无条件 `ClearChatMessages()`——音乐会话仍在（`IsMusicBusy()`：在播或两种暂停）且消息区归音乐所有时，它**重画**而不是清掉，且重画的内容**现取自 `GetPlaybackStatus()` 快照**而不是缓存文本。于是：

- 「放着歌屏幕写着待机」在两种到达路径下都成立：正常次序（清屏在前、写曲目在后）与「先写后清」（tts stop 与 `OnAudioChannelClosed` 各回一次 idle）；
- 换歌后重画出来的是新曲目，与「换歌立刻更新」同一事实源；
- 暂停期间（`IsBusy()` 仍为真）屏幕保住曲目，用户看到的不是「待机」。

**归属权只由「一次真的写下了内容的写屏」建立，也只由两种情形交还**：收场写屏（`end-state`，写一次但 `owns=off`）与清空写屏（`skip`：起播失败、无文案的收场、`WriteMusicNowPlaying` 拿到空闲快照时的兜底）。换歌本身**不写 `skip`**（旧会话的收场归新会话，ADR-0014 决策 5）：旧曲目由新会话的 `now-playing` 原地替换，中间不留空窗。交还之后 idle 的清屏照旧生效——结束态留在屏幕上直到不该留的那一刻，然后正常消失，不会「自己复活」。

**3. 四条「别人的清屏/覆写」路径改为重画。**
这些路径都是应用层动作，与音乐会话在时间上无关，却都会把曲目从屏幕上抹掉：

| 路径 | 本来要做什么 | 音乐在忙时的处置 |
|---|---|---|
| `HandleStateChangedEvent` 的 idle 分支 | `ClearChatMessages()`（说话态 → 空闲态）| 重画（音乐恰恰从这条路径起播） |
| `OnAudioChannelClosed`（服务端 120s 无语音超时）| 清消息区 + 回 idle | **不清**（音乐本身没结束，与 `SetPowerSaveLevel` 同一 `IsMusicBusy()` 判据） |
| `StopNotification` | 擦掉通知文案、回 idle | 重画（通知放完了，音乐接着占着扬声器） |
| `DismissAlert`（一连上服务端就撤告警）| 清消息区 | 重画（那时 `tts/stop` 可能已经把音乐接回来了） |

同一个取舍：**曲目是用户唯一能看见的「它在放」凭据**（工具与日志他都看不见），不该因为无关的界面动作消失。

**4. 四类写屏走同一个 helper，形态与遥测只有一个出口。**
`ShowMusicScreen(action, text, owns, facts?)`：写消息区、置归属权、递增写屏序号、打锚点行。四个动作为 `now-playing`（起播/续播）、`repaint`（有东西要覆写/清空消息区而音乐还握着它 → 重画，决策 3 的四条路径）、`end-state`（收场文案，`#7` 的三种）、`skip`（无文案的清空 + 交还所有权）。#7 的结束态写屏改为经这个 helper——语义不变（仍是「写一次、位置在 `SetDeviceState` 之后」）。

决策 3 的四条路径经 `RepaintOrClearMusicScreen(clear_fn)` 收口：它们只贡献自己的**清屏动作**（回调），归属权判断与交还由 helper 统一做。为什么清屏动作不一起收进来——**清屏 API 因显示变体而异**：LCD 的 `SetChatMessage("", "")` 在气泡变体里会留残影，`ClearChatMessages()` 才是对的；而 OLED/Emote 根本没重写 `ClearChatMessages()`（基类是空实现），只有 `SetChatMessage("", "")` 有效。硬统一会在这两类屏上弄出残留或静默失效。

> 修订（review 发现）：初版称「归属权交还与遥测锚点这两件事从此只有一处实现」，但四条路径实际是**三处各写了一份** `music_screen_owns_content_ = false;`、**一处（`StopNotification`）漏了这句**——后者会永久劫持消息区，是 review 揪出来的真 bug。现四条路径已改走该 helper，「只有一处实现」才成立。

**5. 文案拼装是纯逻辑，单独一个宿主可测的文件。**
`audio/music_screen.{h,cc}` 不引任何 ESP 头，只吃四个字段（`title` / `author` / `duration_s` / `live`）→ 一行文本。`main/audio/music_ending.cc` 已经证明这条路可行（ADR-0014 的「为什么把纯逻辑单独拆一个文件」）：

- 总量显示与否只看**内容形态**（`live`，来自 `MusicPlaybackStatus::seekable` 取反），不看时长是否存在——上游没把 `duration` 剥干净的直播流上，时长是个不该信的数字；
- `0` 与负数都是「不知道放多久」（`0:00` 会被读成「这就完了」）；
- 缺哪块不出哪块：作者为空不留一个悬空的「 · 」。

**6. Lang 只加一个 key：`MUSIC_NOW_PLAYING`。**
前缀（zh「正在播放：」/ en「Now playing: 」）来自 Lang，逻辑只做拼接——与 `音乐结束/中断/已停止` 同一套路，`gen_lang.py` 生成，不手改 `lang_config.h`。

**7. 遥测锚点是「屏幕被设成了什么」的唯一证据源。**
```text
Music screen: action=<now-playing|repaint|end-state|skip> seq=N owns=<on|off>
  idle_gen=N device=<state> title='…' author='…' form=<live|finite> duration=Ns
  total=<m:ss|none> text='…'
```
`device=` 是**写屏那一刻**的设备状态，`text=` 是真正写下去的那串字符——二者同行，所以「曲目在状态转移之后设置」是可断言的：`now-playing` 行的 `device=` 必须是 `idle`（音乐在空闲态下播），且它晚于 `State: … -> idle` 那一行。`seq`/`idle_gen` 是两条单调计数器，用于让「谁先谁后」不依赖日志时间戳。

## Consequences

- **状态栏仍显示 `待命`**（母 spec 的明确取舍）。用户看到的是：状态栏「待命」+ 消息区「正在播放：晴天 · 周杰伦 · 4:29」。观感突兀的话，把状态文本一并改成播放态是**独立的小改动**（加一个 `MUSIC_PLAYING` key + 收场时还原 `STANDBY`），不属于本票，也不影响本结构。
- **暂停态呈现（暂停标识、位点、两种暂停文案）不在本票**（issue #11）。扩展点已经摆好：`MusicScreenFacts` 加 `state` / `position_s` 字段、`BuildMusicNowPlaying` 加分支即可；调用点（`WriteMusicNowPlaying`）与所有权机制都不用动。本票**不预置**这些字段——没有证据的开关只会养出「写了但没人读」的死代码。
- **`end-state` 写屏的 `text=` 是 Lang 字符串**（`播放结束`），`title`/`author` 为空——「不残留旧曲目」在锚点里就是「标题字段空 + 文本里没有旧标题」两个方向的核对。
- **`Music screen:` 是应用侧锚点，不是播放器锚点**：它说的是「屏幕被设成了什么」，至于像素有没有真的画出来，与既有管线遥测的诚实度一致——**不覆盖**。
- **`screen_updates_on_song_change` 在只播一首时报「不适用」**：一次抓取里只有一条起流锚点时无从比较，不做必然失败的红线（与 `--expect-ending` 的取舍一致）。
- **`--expect-screen`**：默认「抓到屏幕锚点才断言」（旧固件与只验位点的抓取不受影响），点名后没抓到就是失败。
- `PROJECT_VER` 2.4.27 → 2.4.28；新增 `MUSIC_NOW_PLAYING`（en-US / zh-CN）。

## 后续修订（issue #11）：暂停态——位点与两种暂停

#8 留下的扩展位（`MusicScreenFacts` 的 `state` / `position_s`）在这里填上，调用点与所有权机制未动——扩展点确实没波及调用点。

**1. 新增 `MusicScreenState` 三态（播放 / 会话性暂停 / 用户暂停），无 kIdle。**
四态快照里 kIdle 是「没有会话可写」——那是清屏（`skip`），不是这里的事。让空闲**无法被传进来**，拼装里就不必再防一次。两个暂停态的文案差别**只有前缀**（Lang 提供，正文相同：曲目 · 作者 · 位点），所以一个枚举撑起两件事：`BuildMusicNowPlaying` 决定「显示不显示位点」，调用方按它挑前缀——两边不会各判一次。

**2. 位点与总量在屏幕上是互斥的两态。**
播放态显示总量（用户关心「还有多久」），暂停态显示已播位点（时间停住了，「放到哪」是唯一能回答「卡住还是暂停」的数字）。互斥是**按态定**的：暂停且位点未知时**什么都不显示**，不退回总量——退回会让暂停态与播放态看着一样，那种“差不多”的呈现正是要消掉的。判定落在 `MusicScreenShowsPosition`（暂停 + 有限内容 + 位点已知）与既有的 `MusicScreenShowsTotal`。

**3. 两种暂停文案必须不同，前缀只从快照挑。**
`已暂停（说完自动继续）：` / `已暂停（说“继续”恢复）：`（en：`Paused (resumes after you finish): ` / `Paused (say "resume" to continue): `）。选这一组而不是「说话中暂停」之类的短句，是因为它直接说出**用户该做什么**——会话性暂停该等、用户暂停该说「继续」，这正是工单的立论。

前缀**现取快照**（`WriteMusicNowPlaying` 里的 `switch (status.state)`），不从 `PauseMusic(kind)` 的入参挑。两条真实路径会把入参判错（`Pause()` 返回 true 但种类不一定是入参那个）：

| 路径 | 入参 | 播放器实际持有 | 从快照取 | 从入参取（错） |
|---|---|---|---|---|
| 会话性暂停期间用户改口说「暂停」（升级） | user | user | 「说继续恢复」✓ | 「说完自动继续」✗——用户等下去，音乐永不回来 |
| 用户暂停期间再来一次唤醒（降级被拒） | conversation | user | 「说继续恢复」✓ | 「说完自动继续」✗——屏幕在说谎 |

同一取舍已在 `PauseMusic`/`UpdatePauseAutoResume` 的自动续播判定上用过（ADR-0013 决策 4）。写屏也走 `ScheduleMusicScreen("paused")`——暂停发生在任意任务（按钮、MCP 工具、唤醒词）上，而写屏必须落在主循环、晚于唤醒触发的那次 `-> idle` 清屏（与决策 1 同一理由）。

**4. 遥测扩展而不新增类：`Music screen:` 锚点加 `state=` 与 `pos=`。**
```text
Music screen: action=<now-playing|paused|repaint|end-state|skip> seq=N owns=<on|off>
  idle_gen=N device=<state> title='…' author='…' form=<live|finite> duration=Ns
  total=<m:ss|none> state=<playing|paused_conversation|paused_user>
  pos=<Ns|live|none> text='…'
```

为什么扩现有锚点而不是新开一条：`state=` 与 `text=` 必须**同行**才构成「两种暂停可区分」的判据（两条不同的 `state` 行不得有同一个 `text`），拆到两条行就又要靠时间戳对时。`pos=` 走 `WritePositionField`（与 `pipe:`/`pause`/`resume`/收场四处同一口径）；`total=` 留 `FormatMusicClock`（人读的 `1:12`）——屏幕上给人看的与锚点里给脚本核的故意不同，但两处的判定同源（两个 Shows* 谓词）。

`tools/serial_telemetry.py` 新增四条断言，各抓一类真错：

| 断言 | 抓住什么错 |
|---|---|
| `pause_screen_distinguishes_kinds` | 两种暂停被写成同一句（忘了按 state 挑前缀）——只断言「文本含曲目」的测试看不见 |
| `paused_screen_shows_position` | 暂停不显示位点（正向）与播放反而显示位点（反向） |
| `live_screen_has_no_position` | 直播流显示了数字位点——工单点名要抓的真错（与 #8 的 `live_screen_has_no_total` 分开，两样都要各自可断言） |
| `paused_position_matches_truth` | 屏幕位点与暂停锚点（设备自己的记账）不一致（±2s）——抓住「拿总量冒充位点」或「拿陈旧快照写屏」 |

它们各有一条测试把真错写出来验证判别力（`tools/tests/test_screen_telemetry.py` 的 `PausedScreenAssertions`）：写成同一句、缺位点、播放带位点、直播带位点、屏幕位点与锚点分叉。

**5. 「前缀只从快照挑」用源码级结构不变量锁住。**
普通行为测试看不见它——错只在「某种暂停下又发生一次事件」时出现（抓取里最不常覆盖的组合）。所以判据放在源码层：三个 Lang 前缀的赋值必须在同一个 `switch (status.state)` 内（`firmware/scripts/tests/test_music_screen.py` 的 `MusicScreenPausedStructureTest`）。已验证判别力：把前缀改成从 `kind` 入参取 → 立即失败。

**6. `FormatMusicClock` 的 0 与负数是两回事。**
负数（含 `-1`，调用方拿来表示未知）给空串；`0` 是合法位点（刚开始就暂停）——显示 `0:00`。位点与总量共用这一个格式函数，口径不会分叉。

### 取证链（issue #11）

| 断言 | 观察点 | 判据 |
|---|---|---|
| 暂停时显示暂停标识与已播位点 | `action=paused` 且 `state=paused_*` 的行 | `pos=` 是数字、`text=` 含 `m:ss` |
| 两种暂停文案可区分 | 两条 `state=` 不同的写屏 | 两个 `text=` 不相等 |
| 恢复播放后回到播放中显示 | 续播后的写屏行 | `state=playing` 且 `pos=none`、`total=` 是时钟 |
| 直播流不显示位点与总量 | `form=live` 的写屏行 | `pos=live` 或 `none`、`total=none`、`text=` 无时钟 |
| 显示的位点与真实位点一致 | `pos=` 与最近一条 `Music pause: pos=` | 差 ≤ 2s（同源同值） |

### 待人工验收（需插 USB + 真机）

```
python3 tools/serial_telemetry.py 60 --assert --expect-screen
```

1. **放一首 → 说话唤醒** → 屏幕变「已暂停（说完自动继续）：曲目 · 作者 · 1:12」，不再报总量；答完不说话，数秒后自动续 → 屏幕回到「正在播放：…」
2. **说「暂停」** → 屏幕变「已暂停（说“继续”恢复）：…」；随便聊一句 → 音乐不自动响、屏幕不变
3. **说「继续」** → 屏幕回到播放中
4. **电台（直播）** → 暂停与播放两态都**不**出现任何时钟；加 `--live` 跑一次
5. 两条暂停文案**肉眼读数不同**（这正是“可区分”的最终判据）

## 取证链

| 断言 | 观察点 | 判据 |
|---|---|---|
| 播放中显示曲目与作者 | `Music screen: action=now-playing … text='…'` | 文本含 `title=` 与 `author=`（同一条行自报） |
| 换歌立刻更新 | 每条 `Music stream started` 之后的**第一条**曲目写屏 | 它的 `title=` 等于那条锚点的曲目 |
| 曲目在状态转移之后设置 | `now-playing` 行的 `device=` + `seq` 与 `State: … -> idle` 的先后 | 写屏在 `-> idle` 之后，且写屏之后不得紧跟着 `-> idle`（那次的清屏会吃掉它） |
| 直播流不显示总量 | `form=live` + `total=none` + 文本无时钟 | 三处互相印证（`form` 由 `seekable` 取反而来） |
| 结束态不留陈旧曲目 | 每条带屏幕文案的 `Music feedback` 之后 | 有一次 `action=end-state` 且其 `title=` 为空、在它之后不再出现任何会话的曲目名 |
| 曲目活过了清屏 | `action=repaint` 锚点及其 `text=` | 走的是重画分支（`owns=on` + `IsMusicBusy()`），而不是清空 |
