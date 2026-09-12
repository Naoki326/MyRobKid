# 音乐状态的推送通道与提示注入：状态变更全集走通道，注入走占位符逐轮展开

音乐能播、能暂停之后，设备与模型**都不知道在放什么**（issue #6 的立论：音乐会话不是一等状态）。#7（收场）、#8（屏幕）已把这条状态呈现给用户的一半做完；本 ADR 记录另一半——**让模型知道**（issue #9）：设备把音乐会话的状态变更推给服务端，服务端在每次调用模型前把最新状态注入系统提示。

## Context

四件事在实现前并非显然：

1. **通道已存在、语义为空。** 设备已能主动发 MCP 消息（播放器完成回调之外还有通用入口），而服务端收到带 `method` 的消息时**只记一行日志就返回**（`mcp_handler.py`）。补语义不需要新连接或新协议——JSON-RPC 通知（无 `id`，不期待响应）落在那个 `elif "method"` 分支里即可。
2. **「通道粒度」与「历史粒度」会被想成一件事。** 通道决定服务端**知道什么**（必须准确，因而必须包含暂停与继续，否则答不出「暂停了吗」，位点估算还会在暂停期间虚涨）；历史决定模型**能回忆什么**（只写曲目级，防每轮对话刷屏）。把两者混为一谈会导致其中一边出错——本 ADR 明确分开。
3. **推送时机有「假成功」陷阱。** 设备既有工具在未验证执行结果时就返回 true（CONTEXT.md 的**假成功**）。若事件照抄那个时机，写进服务端状态的会是一个没出声的谎。判据必须是**播放器已验证的完成条件**。
4. **只写历史治不了「开口说话那次调用」。** 实测：曲目事件写成 system 角色后，**回答用的那次模型调用收到的 system 消息只有基础提示一条**，事件对它不可见；只有意图识别那次（自建历史文本、不按角色过滤）能看到。于是提示注入这条通道是**必需**的，缺了它「这歌谁唱的」答不出——看起来做了、实际一半失效。

## Decision

**1. 通道复用既有 MCP 消息通路，承载状态变更全集。**
设备侧 `music_session_event.{h,cc}` 把已成立的事实编成 JSON-RPC 通知（无 `id`），经 `Protocol::SendMcpMessage`（线上形状 `{"type":"mcp","payload":…}`）发；服务端 `mcp_handler.handle_mcp_message` 的 `elif "method"` 分支把 `method == "music.session"` 路由到 `conn.music_session.apply_event(params)`。**不引入新连接或协议**。

通道承载的状态：`playing` / `paused_conversation` / `paused_user` / `completed` / `interrupted` / `resume_failed` / `stopped` / `start_failed`（与设备侧 `MusicPlaybackStatus::state_name()`、`MusicEndingName()` 同名——服务端不发明第二套词汇）。事件名（`started|paused|resumed|completed|interrupted|resume_failed|stopped|start_failed`）只进日志与将来的历史写入；**`state` 是权威字段**。

边界：未知方法保持现有「只记一行日志」行为，不得报错；`params` 缺失/类型错/未知状态**安静忽略**（`apply_event` 返回 False）。关闭音乐功能时设备不发，服务端也什么都不注入——注入无害由这两端一起守着。

**2. 事件在「真的出声之后」才发。**
判据是**播放器已验证的完成条件**，不是工具返回值：

| 事件 | 发送点 | 为什么此处已出声 |
|---|---|---|
| `started` | `StartMusicNow()` 成功返回后 | `Start()` 已等过首帧解码 |
| `paused` | `PauseMusic()` 同步置位后 | 快照此刻已是 `paused_*` |
| `resumed` | `ResumeMusicNow()` 真开始续播后 | `Resume()` 已置位、解码器已复位；**deferred 的那条路不算出声**，不推假话 |
| `completed`/`interrupted`/`resume_failed`/`stopped` | `HandleMusicFinished()` 收场分类 | 那是播放器 worker 的结论 |
| `start_failed` | 同收场（起流失败的回调） | 从未出声，但服务端需要知道「现在没有音乐在播放」 |
| （`replaced` 不推） | — | 旧会话的收场归**新**会话（ADR-0014 决策 5） |

**3. 发送时机不需要额外的次序机制，但终态必须带上曲目。**
`Application::SendMcpMessage` 内部已 `Schedule()` 到主循环，且它与工具应答**共用同一条 schedule 队列、先进先出**——所以从任意任务调都安全，且在工具处理里「先推、再构造应答」就保证了服务端拿到状态早于/同于那次应答。**终态的曲目不由快照取**：worker 已退出、`GetPlaybackStatus()` 已报空闲且不带曲目，故 `FinishedResult` 扩展携带 `title/author/duration_s`（与 `ending`/位点同一条线程产出、不落共享成员），由 `PushMusicSessionEnding` 编入载荷——否则服务端只能把「刚才那首」覆盖成「未知曲目」。

**4. 服务端用占位符 + 逐轮展开注入，不复用 `dynamic_context`。**
`agent-base-prompt.txt` 新增 `<music_status>` 块；`Dialogue.get_llm_dialogue_with_memory` 每次构建送给模型的对话时用 `apply_prompt_placeholder` 替换。这条通道**每次都重新展开**，因此永远是最新状态、只有一块、**不累积**。`dynamic_context` 的提供者面向外部 HTTP 数据源，不适合设备内部状态，故不复用。

- 注入为空串 → 整块摘掉（关闭音乐功能、还没起播时模板里不留空壳）；模板里没有这个占位符 → no-op，不报错（用户自定义提示词）。
- 直播流（`form=live`）**不呈现位点与总量**——位点对它无意义，带上就是撒谎。服务端在 `_parse` 里主动丢掉设备可能误带的位点，两侧都挡。
- 注入是**只读的**：`chat()` 调 LLM 前现取一次快照，不产生额外一轮对话、不产生额外播报——「只读」在最外层没有可观测出口，故只在注入文本里写一条约束（「回答音乐相关问题时以此为准，不要主动播报音乐状态」），不派生子动作。

**5. 位点估算：在播推进、暂停/收场冻结、直播不报。**
`MusicSession.estimate_position()` 对在播状态加事件之后流逝的时间（clamp 到总长），对暂停与收场**冻结**在事件位点——暂停期间继续加时间就是那个「虚涨」的假位点，答「放到哪了」会越答越不对。

**6. 注入路径上有可观察锚点，注入是否生效可断言。**
```text
# 设备侧（状态变更真的发出去了没）
Music session: event=<…> state=<…> title='…' form=<live|finite> pos=<none|live|83.4s>
# 服务端事件入口（事件到了没、是不是重复）
Music session: applied=<yes|no> state=<…> title='…' form=… pos=…
# 服务端注入路径（状态真的进了送给模型的内容没）
Music inject: injected=<yes|no> state=<…> title='…' author='…' form=<live|finite> pos=<none|…>
```
`Music inject:` 是这条通道**唯一**能被外部观察到的「注入真的发生了」的证据：WS 协议不回显提示词，模型措辞不确定不能作回归判据。
`injected=` 字段是这条锚点的要害（review 修正）：它由服务端在**拼装完送给模型的那份提示之后**、按「注入文本是否真出现在系统提示里」算出。初版锚点打在取值处、只报 `state=…`，于是「状态读到了但占位符没展开」这种半失效会让验收缝保持绿色——即断言的对象是「读了快照」而不是 issue 要求的「内容真的进了模型」。`tools/latency_loop.py --music` 推一条通知后据它断言（不断言措辞）；`server/tests/test_music_injection_seam.py` 则直接把「送到 `get_llm_dialogue_with_memory` 的 system 提示」当断言对象。

## Consequences

- **历史写入（曲目级事件进对话历史，暂停/继续不写）不在本票**（issue #10）。本票只补通道语义与注入；`MusicSession` 已经留下 `event` 字段与幂等记账，历史那条路从它取料即可，不必改线协议。
- **暂停与继续既推送又不写历史**：这是「通道粒度 ≠ 历史粒度」的直接体现。实现时不得因为「不写历史」而顺手「不推送」——那会让「暂停了吗」答不出、位点虚涨。
- **注入的诚实度与既有管线遥测一致**：它证明的是「音乐状态进了送给模型的那份 system 提示」，**不覆盖**「模型据此答对了」——后者由模型输出决定，不确定，因此不作回归判据。
- **真机回环（缝一 + 缝二）需要插 USB / 起服务端**，本 ADR 的实现只保证宿主/服务端可测的那半；真机步骤：
  1. 起服务端并让日志落盘（`Music inject:` 可见）；
  2. `server/.venv/bin/python tools/latency_loop.py 这歌谁唱的 --music started --server-log <服务端日志>` → 期望 `注入已生效: state=playing title='晴天' …`（退出码 0）；
  3. `--music paused` 后同一句 → 期望 `state=paused_user`；`--music-live` → 期望 `pos=none` 且不因直播怀疑而误报。
- `server/tests/test_music_injection_seam.py`（12 项）与 `firmware/scripts/tests/test_music_session_event.py`（9 项）钉住本 ADR 的载荷形状与注入缝；`tools/tests/test_latency_music_push.py`（20 项）钉住假设备侧的协议与断言判别力。
- `PROJECT_VER` 2.4.28 → 2.4.29（固件侧新增 `music_session_event.{h,cc}` 与 `FinishedResult` 的曲目字段）；服务端新增 `core/utils/music_session.py`、`<music_status>` 占位与 `Music inject:` 锚点。

## 后续修订（issue #10）

上面 Consequences 的第一条写的「历史写入不在本票」已被 issue #10 完成。本 ADR 的决策 1（通道粒度）与决策 4（注入）不变；新增的是第二个出口：

- **历史出口**：`MusicSession.history_entry()` 把当前会话的**曲目级事件**渲染成一条 system 历史条目（「开始播放《晴天》」「《晴天》已播完」「《晴天》播放中断」「《晴天》续播失败」），由 `mcp_handler._apply_music_session_event` 在 `apply_event` 返回 True 时写进 `conn.dialogue`。分类与措辞是纯函数（`format_history_entry` + `TRACK_LEVEL_EVENTS`），单测钉住。
- **两条出口不合并**：注入（`prompt()`）每次都重新展开、含暂停与继续；历史（`history_entry()`）只写曲目级事件。前者答「现在在放什么」，后者答「刚才放过什么」。实测表已说明为什么不能只做一条：写在历史里的 system 事件对「开口说话那次调用」不可见，只有意图识别那次能看到；反过来，把暂停/继续也写进历史会每轮产生成对条目、淹没真实对话。
- **为什么不把历史合进通道粒度**：通道承载状态变更全集（含暂停/继续），因为服务端必须知道当前是否暂停（否则答不出「暂停了吗」、位点会在暂停期间虚涨）。历史是另一个问题：它给模型的是**先后顺序**，粒度必须粗。两者的差别是「服务端知道什么」与「模型能回忆什么」。
- **集合按 issue 字面**：`started` / `completed` / `interrupted` / `resume_failed`（「换歌」= 新的 `started`）。`paused` / `resumed` 明确排除；`stopped` 与 `start_failed` 也不写——issue 正文的列举里没有它们，`start_failed` 从未出声、本就无「曲目」可言，`stopped` 是边界（ADR-0014 已把用户按停定为一种收场），若要写入需调度方裁决。
- **新的可观察锚点**：`Music history: written=<yes|no> event=… title='…' author='…' [skipped=duplicate|not_track_level|no_dialogue] [entry='…']`。它存在的理由是「暂停没写历史」必须**可断言**——历史在服务端进程内，外部脚本看不见；若只能靠「日志里没看到条目」判，那是缺席而不是证据，而缺席区分不出「没写」与「没到」。
- **缝**：`server/tests/test_music_history_seam.py`（20 项，真 `Dialogue` 与真锚点）与 `tools/tests/test_latency_history_seam.py`（6 项，走真实 WebSocket + 假设备客户端的真推送函数），另在 `test_music_session.py` 补 10 项纯函数（31→41）、`test_latency_music_push.py` 补 12 项假设备侧断言判别力（20→32）。测试随 `--assert-history` 暴露给真机验收。

## 取证链

| 断言 | 观察点 | 判据 |
|---|---|---|
| 事件经既有通路推送 | 服务端 `Music session: applied=yes` + 设备 `Music session: event=…` | 两条锚点同一次变更，无新连接/协议 |
| 开始播放后注入生效 | `Music inject: injected=yes state=playing title='晴天'` | 送给模型的 system 提示含 `<music_status>` 与曲目（seam 测试直接取该提示） |
| 换歌答新曲目 | 第二次 `started` 之后的注入文本 | 含新曲目、不含旧曲目 |
| 暂停/继续都推 | `paused` → `Music inject: state=paused_user`；`resumed` → `state=playing` | 两侧状态可区分 |
| 直播流不报位点 | `form=live` + `pos=none` + 注入文本无 `m:ss` | 服务端与固件两侧都丢掉位点 |
| 幂等 | 重复推送的 `Music session: applied=no` | 状态不被推进、注入块仍只有一块 |
| 关闭音乐功能无害 | 连接无 `music_session` / 事件从未到达 | 无 `<music_status>` 块、无异常 |
