"""音乐会话（issue #9）：服务端持有的「当前音乐状态」，三处出口共用的唯一事实源。

设备经**既有 MCP 消息通路**把音乐会话的状态变更推给服务端（JSON-RPC 通知，无
``id``）。服务端据此维护当前会话，并在**每次调用模型前**把最新状态注入系统提示
（``<music_status>`` 占位符逐轮展开，沿用 ``<memory>`` 的先例）。

为什么单独一个文件：这条链路里最贵的错误是「状态记错」——把暂停记成在播，位点
估算就会在暂停期间虚涨；把换歌记成同一首，模型答的就是上一首。而它本身是**纯
逻辑**（事件 → 状态 → 一行注入文本），不依赖连接、日志、LLM 或任何服务端组件。
于是它放在这里，用 ``unittest`` 钉住（``server/tests/test_music_session.py``），
不必起服务端、不必接设备。

线协议（设备 → 服务端，复用既有 MCP 消息通路，不引入新连接或协议）::

    {"jsonrpc": "2.0", "method": "music.session", "params": {
        "event": "started|paused|resumed|completed|interrupted|resume_failed|stopped|start_failed",
        "state": "playing|paused_conversation|paused_user|completed|interrupted|resume_failed|stopped|start_failed",
        "title": "晴天", "author": "周杰伦", "form": "finite|live",
        "position_s": 83.4, "duration_s": 269}}

- 无 ``id`` = 通知，服务端不回响应（与既有的请求/响应消息区分开）。
- ``state`` 是权威字段（服务端据此维护会话），``event`` 只进日志与后续的历史写入。
- 直播流（``form=live``）**不带** ``position_s`` / ``duration_s``：位点对它无意义，
  带上就是撒谎；服务端也不对它做任何位点估算。
- 幂等：状态向量（event/state/曲目/作者/形态/总长）相同的事件视为重复，整条忽略。
"""

import math
import re
import threading
import time
from typing import Any, Dict, Optional

#: 设备推送音乐会话状态变更用的 JSON-RPC 方法名（无 id 的通知）。
MUSIC_SESSION_METHOD = "music.session"

STATE_PLAYING = "playing"
STATE_PAUSED_CONVERSATION = "paused_conversation"
STATE_PAUSED_USER = "paused_user"
# 收场四态与设备侧收场分类同名（ADR-0014）——设备报的就是那六个名字，
# 服务端不再发明第二套词汇（``replaced`` 不推：旧会话的收场归新会话）。
STATE_COMPLETED = "completed"
STATE_INTERRUPTED = "interrupted"
STATE_RESUME_FAILED = "resume_failed"
STATE_STOPPED = "stopped"
STATE_START_FAILED = "start_failed"

PLAYING_STATES = frozenset({STATE_PLAYING})
PAUSED_STATES = frozenset({STATE_PAUSED_CONVERSATION, STATE_PAUSED_USER})
TERMINAL_STATES = frozenset(
    {
        STATE_COMPLETED,
        STATE_INTERRUPTED,
        STATE_RESUME_FAILED,
        STATE_STOPPED,
        STATE_START_FAILED,
    }
)
KNOWN_STATES = PLAYING_STATES | PAUSED_STATES | TERMINAL_STATES

FORM_FINITE = "finite"
FORM_LIVE = "live"

#: 事件 → 状态 的兜底映射：设备两个字段都给；缺 state 时不猜反方向。
_EVENT_DEFAULT_STATE = {
    "started": STATE_PLAYING,
    "resumed": STATE_PLAYING,
    "paused": STATE_PAUSED_CONVERSATION,
    STATE_COMPLETED: STATE_COMPLETED,
    STATE_INTERRUPTED: STATE_INTERRUPTED,
    STATE_RESUME_FAILED: STATE_RESUME_FAILED,
    STATE_STOPPED: STATE_STOPPED,
    STATE_START_FAILED: STATE_START_FAILED,
}

#: 系统提示模板里的音乐状态块（与 ``<memory>`` 同一先例：占位符 + 每轮展开）。
PROMPT_BLOCK_RE = re.compile(r"<music_status>.*?</music_status>", re.DOTALL)

#: 注入文本末尾的约束：这条通道是只读的，不触发任何动作、不主动播报。
_INJECTION_RULE = "回答音乐相关问题时以此为准，不要主动播报音乐状态。"


def format_clock(seconds: float) -> str:
    """位点/总量的可读口径 ``m:ss``（≥1 小时 ``h:mm:ss``）。负值取 0。"""
    total = int(seconds) if seconds and seconds > 0 else 0
    if total >= 3600:
        return "%d:%02d:%02d" % (total // 3600, (total % 3600) // 60, total % 60)
    return "%d:%02d" % (total // 60, total % 60)


def _coerce_text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return ""


def _coerce_number(value: Any) -> Optional[float]:
    """只接受有限实数：NaN / inf / 布尔 / 字符串数字一律当没给。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


class MusicPrompt:
    """一次注入所需的全部事实（渲染文本 + 进遥测的字段）。"""

    __slots__ = (
        "text",
        "event",
        "state",
        "title",
        "author",
        "form",
        "live",
        "position_s",
        "duration_s",
    )

    def __init__(self, text, event, state, title, author, form, live, position_s, duration_s):
        self.text = text
        self.event = event
        self.state = state
        self.title = title
        self.author = author
        self.form = form
        self.live = live
        self.position_s = position_s
        self.duration_s = duration_s


class MusicSession:
    """当前音乐会话：串行地吃事件，随时给出一行注入文本。

    线程安全：事件在 asyncio 任务里到达（``handle_mcp_message``），注入在回答
    模型的那条线程上发生（``chat``）——两边不是同一条线程，所以加锁。
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._key = None  # 幂等判据（不含位点与时间）
        self._event = ""
        self._state = ""
        self._title = ""
        self._author = ""
        self._form = FORM_FINITE
        self._live = False
        self._position_s = None
        self._duration_s = 0
        self._updated_at = 0.0
        self._applied = 0
        self._duplicates = 0

    # ── 事件入口 ────────────────────────────────────────────────

    @property
    def has_state(self) -> bool:
        with self._lock:
            return self._key is not None

    @property
    def applied_events(self) -> int:
        with self._lock:
            return self._applied

    @property
    def duplicate_events(self) -> int:
        with self._lock:
            return self._duplicates

    def apply_event(self, params: Dict[str, Any], now: Optional[float] = None) -> bool:
        """吃一条状态变更。返回 True = 状态真的变了（重复事件返回 False）。"""
        parsed = self._parse(params)
        if parsed is None:
            return False
        key, event, state, title, author, form, live, position, duration = parsed
        with self._lock:
            if key == self._key:
                # 幂等（验收缝之一）：同一事件重发（重连、重试）不产生重复状态，
                # 也不推进 updated_at/位点基准——状态向量完全不变。
                self._duplicates += 1
                return False
            self._key = key
            self._event = event
            self._state = state
            self._title = title
            self._author = author
            self._form = form
            self._live = live
            self._position_s = position
            self._duration_s = duration
            self._updated_at = self._clock() if now is None else now
            self._applied += 1
        return True

    @staticmethod
    def _parse(params) -> Optional[tuple]:
        if not isinstance(params, dict):
            return None
        state = _coerce_text(params.get("state"))
        event = _coerce_text(params.get("event"))
        if state not in KNOWN_STATES:
            # 缺 state 时按事件兜底；两个都不认识 —— 忽略整条（不猜、不报错）。
            state = _EVENT_DEFAULT_STATE.get(event, "")
        if state not in KNOWN_STATES:
            return None
        if not event:
            event = state
        title = _coerce_text(params.get("title"))
        author = _coerce_text(params.get("author"))
        form = FORM_LIVE if _coerce_text(params.get("form")).lower() == FORM_LIVE else FORM_FINITE
        live = form == FORM_LIVE
        # 直播流位点无意义：设备不会带，服务端也主动丢——两者都挡住才不会
        # 出现「电台答出一个位点」这种假话。
        position = None if live else _coerce_number(params.get("position_s"))
        duration = 0 if live else int(_coerce_number(params.get("duration_s")) or 0)
        key = (event, state, title, author, form, duration)
        return key, event, state, title, author, form, live, position, duration

    # ── 出口 ────────────────────────────────────────────────────

    def estimate_position(self, now: Optional[float] = None) -> Optional[float]:
        """当前位点估算（秒）。

        在播：事件位点 + 事件之后流逝的时间（clamp 到总量）。
        暂停/收场：**冻结**在事件位点——暂停期间继续加时间就是那个「虚涨」的
        假位点，答「放到哪了」会越答越不对。
        直播流或位点未知：None（不报位点）。
        """
        with self._lock:
            if self._live or self._position_s is None:
                return None
            if self._state not in PLAYING_STATES:
                return self._position_s
            elapsed = max(0.0, (self._clock() if now is None else now) - self._updated_at)
            position = self._position_s + elapsed
            if self._duration_s > 0:
                position = min(position, float(self._duration_s))
            return position

    def prompt(self, now: Optional[float] = None) -> Optional[MusicPrompt]:
        """给「回答用的那次模型调用」的注入内容。从未收到事件 → None。"""
        with self._lock:
            if self._key is None:
                return None
            event = self._event
            state = self._state
            title = self._title
            author = self._author
            form = self._form
            live = self._live
            duration = self._duration_s
        position = self.estimate_position(now)
        return MusicPrompt(
            text=_render_prompt(state, title, author, live, position, duration),
            event=event,
            state=state,
            title=title,
            author=author,
            form=form,
            live=live,
            position_s=position,
            duration_s=duration,
        )


def _render_prompt(state, title, author, live, position, duration) -> str:
    """状态 + 曲目 → 一行自然语言（模型读的是它，不是字段表）。"""
    track = title
    if author:
        track = "%s — %s" % (title, author) if title else author
    track = track or "未知曲目"
    clock = format_clock(position) if position is not None else ""
    total = format_clock(duration) if duration and duration > 0 else ""

    if state == STATE_PLAYING:
        if live:
            body = "现在正在播放：%s（直播流，没有位点）" % track
        elif clock and total:
            body = "现在正在播放：%s（已播放约 %s，总长 %s）" % (track, clock, total)
        elif clock:
            body = "现在正在播放：%s（已播放约 %s）" % (track, clock)
        elif total:
            # 位点未知但总长已知（设备没报位点）——总长照报，「不知道放到哪」
            # 不等于「不知道这首歌多长」。
            body = "现在正在播放：%s（总长 %s）" % (track, total)
        else:
            body = "现在正在播放：%s" % track
    elif state == STATE_PAUSED_USER:
        where = "，停在 %s" % clock if clock else ""
        body = "音乐已暂停：%s（用户暂停%s，需用户明确说“继续”才会续播）" % (track, where)
    elif state == STATE_PAUSED_CONVERSATION:
        where = "，停在 %s" % clock if clock else ""
        body = "音乐暂时让位：%s（会话性暂停%s，这次对话答完会自动续播）" % (track, where)
    elif state == STATE_START_FAILED:
        body = "音乐起播失败（尝试播放：%s），现在没有音乐在播放" % track
    elif state == STATE_COMPLETED:
        body = "音乐已播放结束（刚才放的是：%s），现在没有音乐在播放" % track
    elif state == STATE_INTERRUPTED:
        body = "音乐播放中断（中断时在放：%s），现在没有音乐在播放" % track
    elif state == STATE_RESUME_FAILED:
        body = "音乐续播失败（刚才放的是：%s），现在没有音乐在播放" % track
    elif state == STATE_STOPPED:
        body = "音乐已被停止（刚才放的是：%s），现在没有音乐在播放" % track
    else:  # 未知状态不该走到这里（_parse 已挡住）；兜底成「不在放」。
        body = "现在没有音乐在播放"
    return "%s。%s" % (body, _INJECTION_RULE)


def apply_prompt_placeholder(system_prompt: str, music_status: Optional[str]) -> str:
    """把模板里的 ``<music_status>`` 块整块换成注入内容。

    - ``music_status`` 为 None：模板原样返回（日志等非注入路径）。
    - 空串：整块摘掉——关闭/未使用音乐功能时**不注入**，模板里也不留一个空壳。
    - 非空：包在标签里注入（每次调用前重新展开，永远是最新状态、只有一块、不累积）。

    模板里没有这个占位符时（用户自定义提示词），以上全部是 no-op——不报错。
    """
    if music_status is None:
        return system_prompt
    if not PROMPT_BLOCK_RE.search(system_prompt):
        return system_prompt
    if not music_status:
        return PROMPT_BLOCK_RE.sub("", system_prompt)
    block = "<music_status>\n%s\n</music_status>" % music_status
    return PROMPT_BLOCK_RE.sub(lambda _match: block, system_prompt, count=1)
