#!/usr/bin/env python3
"""抓取机器人串口日志，实时提取 pipe: 管线遥测；--assert 时对位点做断言。

用法:
  server/.venv/bin/python tools/serial_telemetry.py [秒数，默认 120]
  server/.venv/bin/python tools/serial_telemetry.py 25 --assert [--start N] [--live]
      [--min-lines K] [--offset-tol S] [--expect-restart] [--expect-auto-resume]
      [--expect-ending REASON[,REASON…]] [--expect-screen]

插上 USB 后自动探测 /dev/cu.usbmodem*，全量日志存 /tmp/serial_full.log。

断言模式（issue #3 验收缝）——抓取前先开始，再触发播放（起流锚点必须被抓到）：
  - position_monotonic          位点单调不减（同一会话内不回退；重启式续播的
                                ±2s 受控回退由「Music resume: mode=restart」
                                标记放行）
  - position_not_below_start    位点下限 ≥ 起点（带 ss 起流时是绝对位点）
  - position_tracks_wall_clock  位点不领先挂钟（上四分位偏差 ≤ 容差）——ring 预读
                                （约 0.8s）若被计入位点会恒定领先约 +0.8s
  - position_advance_rate       位点总推进 / 净播放时长 ≥ 0.7（暂停不开销）
  - live_records_no_position    直播流只有 pos=live 标记，不出现数字位点

暂停/续播（issue #4）——暂停期间位点必须冻结，续播必须接在原处：
  - pause_freezes_position      暂停跨度内位点不再增长
  - pause_kind_recorded         暂停样本带暂停种类（PAUSED_CONV / PAUSED_USER）
  - resume_continues_position   续播接缝：continue 接在冻结位点 ±0.5s（spec 的
                                验收缝）、restart 回退不超过锚点自报 margin+0.5s
                                且不前进超过 0.5s
  - user_pause_never_auto_resumes  用户暂停的跨度内不得出现 Music auto-resume:（说
                                「暂停」后随便聊一句，音乐不自动响）
  - 可选 --expect-restart       本次抓取要覆盖「原连接不可用」分支
  - 可选 --expect-auto-resume   本次抓取要覆盖「静默数秒自动续播」

收场反馈（issue #7）——三种收场必须可区分，且用户主动停止不报故障音：
  - ending_reason_recorded          `Music ended: reason=…` 是已知的六种之一
  - ending_played_flag_consistent   played=0 的收场只能是 start_failed
  - ending_feedback_matches_reason  每条收场都配上对的提示音与屏幕文案
  - ending_cues_are_distinguishable 自然播完与链路中断的音必须不同、都不静音
  - warning_screens_are_distinguishable  共用告警音的收场（链路中断 / 续播失败）
                                    屏幕文案必须不同（音一样，屏再一样就分不开）
  - user_stop_never_warns           用户主动停止/换歌一律无声（硬不变量）
  - pause_is_not_an_ending          暂停跨度里不该出现收场锚点（按钮打断只是暂停）
  - playback_returns_interactive    收场后唤醒词恢复 / 回到 Listening
  - 可选 --expect-ending REASON     本次抓取要覆盖指定收场（可逗号分隔/重复）

屏幕出口（issue #8）——消息区被设成了什么、什么时刻设的：
  - screen_shows_track              播放中的文本含曲目与作者
  - screen_updates_on_song_change   换歌后屏幕立刻是新曲目（只播一首时报不适用）
  - screen_set_after_state_change   曲目文本晚于 `State: … -> idle`（idle 分支的
                                    清屏在前）——「不被清屏吃掉」的串口佐证
  - live_screen_has_no_total        直播流 form/total 都是 live/none，文本无时钟
  - ended_screen_has_no_stale_track 收场后给出结束态，不残留旧曲目
  - 可选 --expect-screen         本次抓取要验证屏幕出口（没抓到锚点即失败）

暂停态屏幕（issue #11）——位点与两种暂停可区分：
  - pause_screen_distinguishes_kinds  会话性暂停与用户暂停的文本不同（用户据此
                                    决定该等还是该说继续）；只抓到一种时报不适用
  - paused_screen_shows_position      暂停写屏带数字位点与时钟，播放写屏不带
                                    （位点与总量在屏幕上是互斥的两态）
  - live_screen_has_no_position       直播写屏的 pos 一律 live，无数字位点
  - paused_position_matches_truth     屏幕上的位点与暂停锚点（`Music pause: pos=`）
                                    一致（±2s）——「位点与真实位点一致」的串口佐证

锚点行：`Music screen: action=<now-playing|paused|repaint|end-state|skip> seq=N
  owns=<on|off> idle_gen=N device=<state> title='…' author='…' form=<live|finite>
  duration=Ns total=<m:ss|none> state=<playing|paused_conversation|paused_user>
  pos=<Ns|live|none> text='…'`——设备真正写给消息区的那串字符与当时的事实同一条
行里对齐；`State: … -> idle` 行（DeviceStateMachine）用来定先后。

一个抓取窗口里可能连播多首（每首一条起流锚点行），断言**按会话分段**跑：位点
单调、起点下限、挂钟偏差都只在同一会话内比较，换歌不误报。起点取自锚点行
（`start=Ns`，设备自己的口径）——CLI `--start` 只在没有锚点行时兜底；两者都在
且不一致会单独报一条 `cli_matches_anchor`，免得断言在参数漏传时静默放行。

位点精度：`pipe:` 周期行是**整秒**（`pos=73s`，2 秒一拍的采样不该伪造精度）；
三条锚点行（`Music pause: pos=`、`Music resume: mode=continue at=`、
`mode=restart at=/from=`）是**一位小数**（`73.4s`）——spec 的「恢复后位点从原处
继续（±0.5s）」用整秒量化够不着。margin= 仍是整秒。
"""
import glob
import re
import statistics
import sys
import time

PIPE_RE = re.compile(
    r"pipe:\s+ring=(\d+)/(\d+)\s+in_buf=(\d+)B\s+read=(\d+)B/2s"
    r"\s+pushed=(\d+)\s+fail=(\d+)(?:\s+pos=(?P<pos>\d+s|live))?"
    r"(?P<flags>.*)$")
SESSION_RE = re.compile(
    r"Music stream started: title='(?P<title>.*?)' author='(?P<author>.*?)' "
    r"duration=(?P<duration>\d+)s form=(?P<form>\w+) start=(?P<start>\d+)s")
# 锚点行的位点带**一位小数**（`73.4s`）：spec 的「恢复后位点从原处继续（±0.5s）」
# 用整秒量化够不着，故 pause/resume 三条锚点行改用一位小数；`pipe:` 周期行继续
# 整秒（2 秒一拍的采样不该伪造精度）。两条正则因此分开写，别互相串精度。
_NUM_POS = r"(?P<pos>\d+(?:\.\d+)?s|live)"
# 暂停锚点：设备进暂停时打一次，带冻结位点与两种语义中的哪一种。
PAUSE_RE = re.compile(r"Music pause:\s+kind=(?P<kind>user|conversation)\s+pos=" + _NUM_POS)
# 续播锚点：两段式恢复走了哪一段（continue = 原连接接着读，restart = 按位点重起流）。
# continue 的 at= 是接缝处的位点（暂停时冻结的那个）；restart 的 at= 是**请求的
# 重起位点**、from= 才是暂停时的位点，margin= 是安全余量秒数——用来核对「宁可
# 重复一小段，不跳词」。
RESUME_RE = re.compile(
    r"Music resume:\s+mode=(?P<mode>continue|restart)\s+at=" + _NUM_POS +
    r"(?:\s+from=(?P<from>\d+(?:\.\d+)?s|live))?(?:\s+margin=(?P<margin>\d+)s)?")
# 自动续播：静默计时到阈值（issue #4 的会话性暂停由它驱动）。
AUTO_RESUME_RE = re.compile(r"Music auto-resume:\s+quiet=(?P<quiet>\d+)s")
# 收场锚点（issue #7）：播放器在 worker 退出前打，reason 是五个事实位推出来的结局，
# played= 是「本次会话真出过声」的自我声明（用户客观上听见了才有「中断」可言）。
ENDING_RE = re.compile(
    r"Music ended:\s+reason=(?P<reason>\w+)\s+played=(?P<played>[01])\s+pos=" +
    _NUM_POS + r"(?:\s+url=(?P<url>.*))?")
# 反馈锚点（issue #7）：应用侧报告这次收场给了用户什么——sound= 提示音、
# screen= 屏幕文案、wake_word=/interactive= 证明播放结束后设备回到了可交互态。
# 两个正则分开写：换歌的跳过型反馈没声没屏（skipped=new_session），用同一个
# 正则装两套字段会让「少了字段」和「字段为空」分不开。
FEEDBACK_RE = re.compile(
    r"Music feedback:\s+reason=(?P<reason>\w+)\s+pos=" + _NUM_POS +
    r"\s+sound=(?P<sound>\w+)\s+screen=(?P<screen>\w+)"
    r"\s+wake_word=(?P<wake_word>\w+)\s+interactive=(?P<interactive>\w+)")
FEEDBACK_SKIP_RE = re.compile(
    r"Music feedback:\s+reason=(?P<reason>\w+)\s+pos=" + _NUM_POS +
    r"\s+skipped=(?P<skipped>\w+)")
# 屏幕出口锚点（issue #8，issue #11 扩展 state=/pos=）：应用侧每次把消息区设成
# 什么、当时设备在哪个状态、呈现的是哪种态（playing / paused_conversation /
# paused_user）、这块区域归不归音乐所有，都在这条行里对齐。「曲目在状态转移
# 之后设置」与「两种暂停文案可区分」因此都在串口上可验，不靠人看屏幕。
#   action=now-playing   起播/续播成功后写曲目与作者（含暂停恢复）
#   action=paused        暂停（issue #11）：两种暂停的文本必须可区分
#   action=repaint      有东西要覆写/清空消息区，而音乐还握着它 → 重画曲目
#                       （idle 分支、收尾清屏、通知接管、告警撤销四条路）
#   action=end-state     收场反馈（播放结束/中断/已停止），不保留旧曲目
#   action=skip          起播没成功（换歌失败/起播任务起不来）：清空并交还所有权
#                       ——换歌本身不写 skip（旧会话的收场归新会话，见 ADR-0014
#                       决策 5），新曲目由新会话的 now-playing 接着写上
# state= 是**写屏那一刻呈现的态**（与 device= 那次状态不同一回事）、pos= 是锚点
# 口径的位点（`72s` / `live` / `none`）。
# text= 是**最后**一个字段：曲目里带单引号（`Don't Stop`）时，前面的非贪婪
# 匹配仍能对得上。
SCREEN_RE = re.compile(
    r"Music screen:\s+action=(?P<action>[\w-]+)\s+seq=(?P<seq>\d+)\s+"
    r"owns=(?P<owns>\w+)\s+idle_gen=(?P<idle_gen>\d+)\s+device=(?P<device>\w+)\s+"
    r"title='(?P<title>.*?)'\s+author='(?P<author>.*?)'\s+form=(?P<form>\w+)\s+"
    r"duration=(?P<duration>\d+)s\s+total=(?P<total>[\w:]+)\s+"
    r"state=(?P<state>\w+)\s+pos=(?P<pos>[\w.]+)\s+"
    r"text='(?P<text>.*)'\s*$")
# 设备状态转移行（DeviceStateMachine，TAG=StateMachine）：屏幕文本必须晚于它那
# 一次转移——idle 分支就在那条路径上重画/清空消息区。
STATE_RE = re.compile(r"\bState:\s+(?P<old>\w+)\s+->\s+(?P<new>\w+)")
# 文本里有没有时钟形状的总量（`4:29` / `1:01:01`）。直播流上出现一个就是撒谎——
# 短语里恰好带冒号数字（`13:30 开始`）的误判率极低，而漏判的代价是一条假绿。
_CLOCK_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")

# 收场原因 → (提示音, 屏幕文案) 的**验收对照表**（issue #7 的契约）。固件那边同
# 一张表分开实现（music_ending.cc 给提示音，application.cc 给文案）；这里独立写
# 一份，是为了让「固件只改了注释、忘了改行为」也能被逮住。
# 自然播完给 success、链路中断给 alert——两者必须不同（issue #7 的核心）；
# 用户主动停止与换歌一律 none（绝不报故障音）。
ENDING_FEEDBACK = {
    "completed": ("success", "ended"),
    "interrupted": ("alert", "interrupted"),
    # 续播失败与链路中断**共用同一个告警音**（用户听得出「出事了」，分不出是
    # 哪一种），屏幕才是把它们分开的地方（issue #12）：`resume_failed` 是「再点
    # 一次」，`interrupted` 是「查网络/上游」。两行屏幕文案必须不同。
    "resume_failed": ("alert", "resume_failed"),
    "stopped": ("none", "stopped"),
    "replaced": ("none", "none"),
    "start_failed": ("none", "none"),
}
# 没出过声（played=0）就没有「中断/续播失败」可言，一律 start_failed。
# 表里没列的原因（stopped/replaced）两种都合法——用户可能在出声前就按停/换歌。
ENDING_REQUIRES_PLAYED = {
    "completed": True,
    "interrupted": True,
    "resume_failed": True,
    "start_failed": False,
}
# 用户主动引起的收场：任何情况下不得伴故障音（issue #7 的硬不变量）。
NO_WARNING_ENDINGS = ("stopped", "replaced")
# 播放器锚点与反馈锚点之间的合理延迟：主循环一拍就能转过去；超过就说明反馈丢了。
FEEDBACK_MAX_DELAY_S = 2.0
# 「回到可交互」的合法计数：scheduled = 已安排回 Listening、already = 当时就已
# 空闲、wake_word_only = 音频通道没开、只能恢复唤醒词（离线时能拿到的全部）。
INTERACTIVE_MODES = ("scheduled", "already", "wake_word_only")

DEFAULT_OFFSET_TOL = 0.6  # 位点-挂钟偏差容差（秒）：ring 预读 ≈ +0.8s 必越界
# 续播接缝容差（spec 的 ±0.5s）：锚点行带一位小数，接缝「接在原处」判据是
# |at= - 冻结位点| ≤ 0.5s；重启式续播的回退按锚点自报的 margin 放行
# （回退 ≤ margin + 0.5s），且不许前进超过 0.5s。
RESUME_SEAM_TOL = 0.5
RESTART_MARGIN_FALLBACK = 2.0  # 锚点没带 margin= 时的兜底安全余量（固件常量）
# 屏幕位点与暂停锚点位的容差（秒，issue #11 的验收缝「误差在数秒内」）。
# 两者同源（设备自己的记账），理论上应完全相等；留 2s 是因为屏幕写屏与暂停
# 锚点是两次独立采样，中间可能夹着一次真实的推帧（暂停置位与锚点行打印之间）。
PAUSE_SCREEN_POS_TOL = 2.0

def parse_pipe_line(text):
    """解析 pipe: 行。命中返回 dict，否则 None。

    pos 字段：有限内容为整数秒（绝对位点 = 起点 + 已推帧折算）；直播流为
    字面 'live'；旧固件无 pos 字段时为 None。
    paused：None（在播）/ 'conversation' / 'user'（两种暂停语义可区分）。
    """
    m = PIPE_RE.search(text)
    if not m:
        return None
    pos_raw = m.group("pos")
    if pos_raw is None:
        pos = None
    elif pos_raw == "live":
        pos = "live"
    else:
        # 固件格式 pos=13s：数字 + 单位后缀（沿用管线遥测字段风格）。周期行
        # 是整秒，解析口与锚点行共用（_parse_pos），这里收回整数。
        try:
            pos = int(_parse_pos(pos_raw))
        except ValueError:
            pos = None
    flags = m.group("flags") or ""
    if "PAUSED_USER" in flags:
        paused = "user"
    elif "PAUSED_CONV" in flags:
        paused = "conversation"
    else:
        paused = None
    return {
        "ring": int(m.group(1)),
        "ring_max": int(m.group(2)),
        "in_buf": int(m.group(3)),
        "read": int(m.group(4)),
        "pushed": int(m.group(5)),
        "fail": int(m.group(6)),
        "pos": pos,
        "paused": paused,
        "eof": "EOF" in flags,
        "decode_error": "DECERR" in flags,
    }


def parse_session_line(text):
    """解析起流锚点行（Music stream started: …）。未命中返回 None。"""
    m = SESSION_RE.search(text)
    if not m:
        return None
    return {
        "title": m.group("title"),
        "author": m.group("author"),
        "duration_s": int(m.group("duration")),
        "form": m.group("form"),
        "start_s": int(m.group("start")),
    }


def parse_pause_line(text):
    """解析暂停锚点行（Music pause: kind=… pos=…）。未命中返回 None。"""
    m = PAUSE_RE.search(text)
    if not m:
        return None
    return {"kind": m.group("kind"), "pos": _parse_pos(m.group("pos"))}


def parse_resume_line(text):
    """解析续播锚点行（Music resume: mode=… at=…）。未命中返回 None。

    continue：at= 是接缝处的位点（暂停时冻结的那个），没有 from/margin。
    restart：at= 是请求的重起位点，from= 是暂停时的位点，margin= 是回退余量。
    """
    m = RESUME_RE.search(text)
    if not m:
        return None
    return {
        "mode": m.group("mode"),
        "pos": _parse_pos(m.group("pos")),
        "from_s": _parse_pos(m.group("from")),
        "margin_s": int(m.group("margin")) if m.group("margin") is not None else None,
    }


def parse_auto_resume_line(text):
    """解析自动续播锚点行（Music auto-resume: quiet=Ns）。未命中返回 None。"""
    m = AUTO_RESUME_RE.search(text)
    if not m:
        return None
    return {"quiet_s": int(m.group("quiet"))}


def parse_ending_line(text):
    """解析收场锚点行（Music ended: reason=… played=… pos=… url=…）。

    未命中返回 None。reason 原样返回（不在这里判合法性）——未知原因要能被
    断言组报出来，在这里筛掉就变成「没抓住」的静默假通过。
    """
    m = ENDING_RE.search(text)
    if not m:
        return None
    return {
        "reason": m.group("reason"),
        "played": int(m.group("played")),
        "pos": _parse_pos(m.group("pos")),
        "url": m.group("url") or "",
    }


def parse_feedback_line(text):
    """解析反馈锚点行（Music feedback: reason=…）。未命中返回 None。

    两种形态共用一条理由字段：正常反馈带 sound=/screen=/wake_word=/interactive=，
    换歌的跳过型只有 skipped=（它不出声也不改屏幕）。跳过的几个字段为 None。
    """
    m = FEEDBACK_SKIP_RE.search(text)
    if m:
        return {"reason": m.group("reason"), "pos": _parse_pos(m.group("pos")),
                "skipped": m.group("skipped"), "sound": None, "screen": None,
                "wake_word": None, "interactive": None}
    m = FEEDBACK_RE.search(text)
    if not m:
        return None
    return {
        "reason": m.group("reason"),
        "pos": _parse_pos(m.group("pos")),
        "skipped": None,
        "sound": m.group("sound"),
        "screen": m.group("screen"),
        "wake_word": m.group("wake_word"),
        "interactive": m.group("interactive"),
    }


def parse_screen_line(text):
    """解析屏幕出口锚点行（Music screen: action=…）。未命中返回 None。

    文本与字段同一条行：text= 是设备真正写给消息区的那串字符，title=/author=/
    form=/duration=/total= 是当时的事实（曲目、内容形态、总量），device= 是写屏
    那一刻的设备状态。断言拿它们两两对照——「屏幕被设成了什么」而不是「应该
    是什么」。
    """
    m = SCREEN_RE.search(text)
    if not m:
        return None
    return {
        "action": m.group("action"),
        "seq": int(m.group("seq")),
        "owns": m.group("owns"),
        "idle_gen": int(m.group("idle_gen")),
        "device": m.group("device"),
        "title": m.group("title"),
        "author": m.group("author"),
        "form": m.group("form"),
        "duration_s": int(m.group("duration")),
        "total": m.group("total"),
        # 呈现态（issue #11）：playing / paused_conversation / paused_user。
        # 与 pos= 配合才是「两种暂停可区分」的判据——单看 state 只能证明设备
        # 知道自己在哪一态，state + text 才能证明屏幕上真写了不同的字。
        "state": m.group("state"),
        # 位点（issue #11）：锚点口径（`72s` / `live` / `none`）。屏幕上是人读的
        # `1:12`，这里是脚本核的数字；两者同一判据，不会一个有一个没有。
        "pos": _parse_pos(m.group("pos")),
        "text": m.group("text"),
    }


def parse_state_line(text):
    """解析设备状态转移行（`State: speaking -> idle`）。未命中返回 None。"""
    m = STATE_RE.search(text)
    if not m:
        return None
    return {"old": m.group("old"), "new": m.group("new")}


def _parse_pos(raw):
    """位点原始字段 → 数值（'live' / 'none' / None 原样返回）。

    单一解析口：`pipe:` 行（整秒）与三条锚点行（一位小数）都经它。单位后缀
    只在这里 strip 一次，别各处再 spread 出一份 rstrip("s")。
    'none' 是屏幕锚点的「无位点」（issue #11）：它与 'live' 是两回事——前者
    说的是「这一屏不必显示位点」（播放态/未知），后者说的是「这个内容没有位点」。
    """
    if raw is None or raw in ("live", "none"):
        return raw
    return float(raw.rstrip("s"))


def _is_num(value):
    """数字位点（int/float 都算；'live' / None 不算）。

    pipe: 行是整秒（int），锚点行是一位小数（float）——两处都算数字。
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _add_pause_kind_assertion(add, paused_events, pause_markers, live):
    """暂停种类断言（issue #4）：live 与非 live 逐字同一段，别抄两遍。

    取值来自两个来源（暂停期间的 pipe: 标记 + 暂停锚点自报的 kind），
    只允许 'user' / 'conversation' 两个值，且至少要出现一个。
    """
    kinds = sorted({e["parsed"]["paused"] for e in paused_events} |
                   {m["pause"]["kind"] for m in pause_markers})
    bad_kinds = [k for k in kinds if k not in ("user", "conversation")]
    label = "直播流暂停种类：" if live else "暂停种类："
    add("pause_kind_recorded", not bad_kinds and bool(kinds),
        label + ", ".join(kinds) + (f"（非法：{bad_kinds}）" if bad_kinds else ""))


class AssertionResult:
    def __init__(self, name, ok, detail):
        self.name = name
        self.ok = ok
        self.detail = detail

    def __repr__(self):
        return f"AssertionResult({self.name!r}, ok={self.ok}, detail={self.detail!r})"


def effective_start(session, cli_start):
    """本会话的起点：锚点行自报的 start= 优先，CLI --start 只在无锚点时兜底。"""
    if session["session"] is not None:
        return session["session"]["start_s"]
    return cli_start if cli_start is not None else 0


def evaluate_capture(samples, *, start_s=None, live=None, min_lines=3,
                     offset_tol=DEFAULT_OFFSET_TOL, expect_restart=False,
                     expect_auto_resume=False, expect_ending=(),
                     expect_screen=None):
    """对抓到的采样做断言。samples = [{'t': 抓到时刻, 'line': 原始行}, …]。

    返回 [AssertionResult, …]；全部 .ok 为 True 才算验收通过。

    按会话分段：起流锚点行（Music stream started）开一段，其后到下一个锚点
    之间的 pipe: 行都属于该段。起点与形态取自该段的锚点，CLI 参数只作兜底与
    核对——这样「位点是绝对值」「直播不记位点」不靠调用者记得传参。

    暂停/续播（issue #4）：暂停跨度的样本带 PAUSED_CONV / PAUSED_USER 标记，
    位点在那里必须冻结；续播锚点（Music resume: mode=continue|restart）标出
    接缝位置——continue 必须接在冻结位点 ±0.5s 内，restart 按锚点自报的
    margin 放行回退（≤ margin+0.5s）且不许前进超过 0.5s；用户暂停的跨度内
    不许出现 auto-resume。**暂停过的会话，推进率与挂钟偏差按净播放时长折算**
    ——否则一段正常录音只要含暂停，今天必然误报。
    """
    results = []

    def add(name, ok, detail):
        results.append(AssertionResult(name, bool(ok), detail))

    sessions = []  # [{'t', 'session', 'events': […], 'markers': […], 'endings': […]}]
    orphan_events = []  # 锚点缺失时的兜底容器
    orphan_markers = []
    orphan_endings = []  # 起流失败时**没有**锚点（从未出声就没打过）
    feedbacks = []  # 反馈锚点不归会话：它是应用侧对「刚刚那次收场」的处置
    # 屏幕出口（issue #8）：也不归会话——写屏与应用侧的其他动作同行，而会话
    # 分段靠起流锚点（写屏可能早于锚点：起播那一刻与推第一帧不是同一拍）。
    screens = []
    states = []  # 设备状态转移行（写屏必须晚于它那一次 -> idle）
    # 行序（不是时间戳）：真机抓取里相邻两行可能落在同一秒（pipe: 与锚点行
    # 同一拍打出），拿 t 比先后会退化成浮点相等的运气。行序是设备实际打印的
    # 次序，恒可分辨。
    for idx, s in enumerate(samples):
        line = s["line"]
        session = parse_session_line(line)
        if session is not None:
            sessions.append({"t": s["t"], "i": idx, "session": session, "events": [],
                             "markers": [], "endings": []})
            continue
        ending = parse_ending_line(line)
        if ending is not None:
            record = {"t": s["t"], "i": idx, "ending": ending}
            if sessions:
                sessions[-1]["endings"].append(record)
            else:
                # 收场锚点先于任何起流锚点：**起流失败就是这种形状**（从未出声
                # 就不会打 Music stream started）。不能丢——丢了就成了「没抓到」
                # 的静默假通过。
                orphan_endings.append(record)
            continue
        feedback = parse_feedback_line(line)
        if feedback is not None:
            feedbacks.append({"t": s["t"], "i": idx, "feedback": feedback})
            continue
        screen = parse_screen_line(line)
        if screen is not None:
            screens.append({"t": s["t"], "i": idx, "screen": screen})
            continue
        state = parse_state_line(line)
        if state is not None:
            states.append({"t": s["t"], "i": idx, "state": state, "line": line})
            continue
        pause = parse_pause_line(line)
        resume = parse_resume_line(line)
        auto_resume = parse_auto_resume_line(line)
        if pause is not None or resume is not None or auto_resume is not None:
            marker = {"t": s["t"], "i": idx, "line": line, "pause": pause,
                      "resume": resume, "auto_resume": auto_resume}
            if sessions:
                sessions[-1]["markers"].append(marker)
            else:
                orphan_markers.append(marker)
            continue
        parsed = parse_pipe_line(line)
        if parsed is None:
            continue
        event = {"t": s["t"], "i": idx, "parsed": parsed}
        if sessions:
            sessions[-1]["events"].append(event)
        else:
            orphan_events.append(event)
    if orphan_events or orphan_markers:
        # 没抓到锚点的抓取（旧固件或起播早于开抓）：整段当一次会话，起点靠 CLI。
        sessions.insert(0, {"t": None, "i": None, "session": None,
                            "events": orphan_events, "markers": orphan_markers,
                            "endings": orphan_endings})
    elif orphan_endings:
        # 只有孤儿收场（起流失败）：照样要能被断言看见，但不造出一个空会话
        # （那会让位点断言对着零个样本失败，把一条真结论误报成三条噪声）。
        sessions.append({"t": None, "i": None, "session": None, "events": [],
                         "markers": [], "endings": orphan_endings})

    events = [e for s in sessions for e in s["events"]]
    markers = [m for s in sessions for m in s["markers"]]
    endings = [e for s in sessions for e in s["endings"]]

    # ── 收场断言（issue #7）：三种收场可区分、用户主动停止不报故障 ────
    # 没有收场锚点的抓取（issue #3/#4 那套核验）这里什么都不加——不凭空多断言。
    _add_ending_assertions(add, endings, feedbacks, expect_ending, events, markers,
                           last_t=max((s["t"] for s in samples), default=None))

    # ── 屏幕出口断言（issue #8）：屏幕被设成了什么，不靠人眼 ──────────
    # 缺省「有屏幕锚点就断言」：旧固件的抓取（无锚点）与 issue #3/#4 那几套
    # 只关心位点的核验不该凭空多出五条必然失败的断言；--expect-screen 则显式
    # 点名要求（没抓到就是失败——「这次要验屏幕」不能静默放行）。
    if screens or expect_screen:
        _add_screen_assertions(add, screens, states, sessions, endings, feedbacks,
                               events, markers)

    if not events and endings:
        # 一次根本没出声的抓取（典型：起流失败）：位点类断言无从谈起，不适用。
        # 如实报一条，而不是用五条必然失败把真正的结论（收场分类与不报警）淹掉。
        add("position_checks_not_applicable", True,
            "本段抓取无 pipe: 位点样本（未出声，如起流失败）——位点类断言不适用")
        return results

    add("enough_position_lines", len(events) >= min_lines,
        f"{len(events)} 行 pipe: 遥测（要求 ≥ {min_lines}）")

    anchored = [s for s in sessions if s["session"] is not None]
    add("session_anchor_seen", bool(anchored),
        f"{len(anchored)} 条起流锚点（Music stream started）"
        + ("" if anchored else "——请先开始抓取再触发播放"))

    is_live = live if live is not None else (
        anchored[0]["session"]["form"] == "live" if anchored else False)

    # ── 暂停 / 续播断言（两种内容形态都适用）────────────────────────
    # 直播流位点无意义（pos=live），冻结断言不适用——只核对暂停种类与续播路径。
    _add_pause_assertions(add, events, markers, expect_restart, expect_auto_resume,
                          live=is_live)

    if is_live:
        bad = [e for e in events if _is_num(e["parsed"]["pos"])]
        add("live_records_no_position", len(bad) == 0 and len(events) > 0,
            f"数字位点 {len(bad)} 处（直播流必须全部 pos=live）")
        return results

    # ── 有限内容：逐会话断言 ──────────────────────────────────────
    # 暂停的处置：暂停期间挂钟在走、位点不动。若把暂停跨度按在播算，一段
    # 正常的「播→暂停→继续」录音必然失败（推进率被拉低、挂钟偏差变负数）。
    # 故：跨过暂停的样本对不参与单调性；挂钟/推进率按净播放时长折算。
    monotonic_ok = True
    monotonic_detail = "同一会话内位点单调不减"
    below_start = []
    # 重启式续播的安全余量：位点回退 1–2s 是「宁可重复不跳词」的既定代价，
    # 由续播锚点（mode=restart）放行，其余任何回退仍是失败。
    restart_positions = [m["resume"]["pos"] for m in markers
                         if m["resume"] is not None and m["resume"]["mode"] == "restart"
                         and _is_num(m["resume"]["pos"])]
    for s in sessions:
        numeric = [e for e in s["events"] if _is_num(e["parsed"]["pos"])]
        seg_start = effective_start(s, start_s)
        for e in numeric:
            if e["parsed"]["pos"] < seg_start - 1:
                below_start.append((seg_start, e["parsed"]["pos"]))
        for a, b in zip(numeric, numeric[1:]):
            if a["parsed"]["paused"] is not None or b["parsed"]["paused"] is not None:
                continue  # 暂停跨度的接缝：由续播断言单独负责
            if b["parsed"]["pos"] < a["parsed"]["pos"]:
                if any(b["parsed"]["pos"] <= rp <= a["parsed"]["pos"]
                       for rp in restart_positions):
                    continue
                monotonic_ok = False
                monotonic_detail = (f"{a['parsed']['pos']}s → "
                                    f"{b['parsed']['pos']}s 出现回退")
    add("position_monotonic", monotonic_ok, monotonic_detail)

    # 起点核对：锚点行是设备自报口径，CLI 只是兜底。两者都在却不一致 =
    # 调用方传错了参数，断言会静默放行——单独报出来。
    if start_s is not None and anchored:
        mismatch = [s for s in anchored if s["session"]["start_s"] != start_s]
        add("cli_matches_anchor", not mismatch,
            f"CLI --start={start_s}s 与锚点 start={anchored[0]['session']['start_s']}s "
            + ("一致" if not mismatch else f"不一致（{len(mismatch)} 条锚点）"))

    if not any(_is_num(e["parsed"]["pos"]) for e in events):
        add("position_not_below_start", False, "没有数字位点样本")
    else:
        add("position_not_below_start", not below_start,
            "最小位点均不低于各自会话起点"
            if not below_start else
            "位点低于起点：" + ", ".join(f"起点{st}s 处见 {p}s"
                                        for st, p in below_start[:3])
            + "（位点像相对位点，起点丢了）")

    # 位点-挂钟：以本会话锚点为 0 点。位点若含 ring 预读会恒定领先（+0.8s 量
    # 级）；网络卡顿只会造成负偏差——故领先检测取上四分位（q75），卡顿免疫。
    offsets = []
    advances = []
    pause_intervals = _pause_intervals(events, markers)
    for s in sessions:
        numeric = [e for e in s["events"] if _is_num(e["parsed"]["pos"])]
        if s["t"] is None or len(numeric) < 2:
            continue
        seg_start = effective_start(s, start_s)
        # 净播放时刻：从会话锚点起算的挂钟，减去暂停区间。
        played_at = _net_playback_offsets(numeric, s["t"], pause_intervals)
        for (t, pos), net in zip(
                [(e["t"], e["parsed"]["pos"]) for e in numeric], played_at):
            offsets.append(pos - seg_start - net)
        net_elapsed = played_at[-1] - played_at[0]
        if net_elapsed > 0:
            # 重启式续播的 2s 安全余量是**既定代价**（宁可重复不跳词），
            # 不是停滞：把这段受控回退加回分子，否则一次重起流就会把推进率
            # 拉低到下限以下。
            rewound = sum(max(0, m["resume"]["from_s"] - m["resume"]["pos"])
                          for m in markers
                          if m["resume"] is not None and m["resume"]["mode"] == "restart"
                          and _is_num(m["resume"]["pos"])
                          and _is_num(m["resume"]["from_s"])
                          and m["t"] >= s["events"][0]["t"])
            advanced = numeric[-1]["parsed"]["pos"] - numeric[0]["parsed"]["pos"]
            advances.append((advanced + rewound) / net_elapsed)

    if not offsets:
        add("position_tracks_wall_clock", False,
            "可分段的数字位点样本不足（每个会话需 ≥ 2 行，且要有起流锚点）")
        add("position_advance_rate", False, "同上，无从计算推进率")
    else:
        q75 = statistics.quantiles(offsets, n=4)[1] if len(offsets) >= 4 else max(offsets)
        add("position_tracks_wall_clock", q75 <= offset_tol,
            f"位点-挂钟上四分位偏差 {q75:+.2f}s（容差 +{offset_tol}s，已扣暂停）；"
            f"恒定领先 ≈ +0.8s 即 ring 预读被计入位点")
        worst = min(advances) if advances else 0.0
        add("position_advance_rate", bool(advances) and worst >= 0.7,
            f"各会话净播放推进率最低 {worst:.2f}（下限 0.7，已扣暂停）")
    return results


def _pause_intervals(events, markers):
    """每次暂停的墙钟区间 [(start_t, end_t), …]。

    边界**优先取锚点行**（`Music pause:` / `Music resume:`）——它们标记的是
    暂停真正开始/结束的那一刻；位点是 2 秒一拍的遥测，用样本时刻当边界会
    系统性少算半拍到一拍，挂钟断言就被这点误差顶过容差。锚点缺失时退回
    第一/最后一个带暂停标记的样本。
    """
    intervals = []
    for start, end in _pause_spans(events):
        t0 = events[start]["t"]
        t1 = events[end]["t"]
        pause_marks = [m["t"] for m in markers
                       if m["pause"] is not None and m["t"] <= t0]
        if pause_marks:
            t0 = max(pause_marks)
        resume_marks = [m["t"] for m in markers
                        if m["resume"] is not None and m["t"] >= t1]
        if resume_marks:
            t1 = min(resume_marks)
        intervals.append((t0, t1))
    return intervals


def _net_playback_offsets(numeric_events, session_t=None, pause_intervals=None):
    """把会话内的样本时刻折算成「净播放时刻」（扣掉暂停区间）。

    起点是会话锚点行（没有锚点时退回第一个位点样本）：位点-挂钟断言的口径
    是「位点相对起点走了多少」对「锚点以来真正在播多久」。
    """
    if not numeric_events:
        return []
    origin = numeric_events[0]["t"] if session_t is None else session_t
    intervals = pause_intervals or []
    played = []
    for event in numeric_events:
        t = event["t"]
        paused = sum(max(0.0, min(t, end) - max(origin, start))
                     for start, end in intervals)
        played.append(max(0.0, t - origin - paused))
    return played


def _add_screen_assertions(add, screens, states, sessions, endings, feedbacks,
                           events, markers):
    """屏幕出口断言组（issue #8、#11）：屏幕被设成了什么，不靠人眼。

    判据一律以**锚点行自报的信息**为准：`Music screen: action=… text='…'` 是
    设备真正写给消息区的那串字符，title=/author=/form=/total=/state=/pos= 是
    当时的事实。本组证明八件事：
      - 播放中的文本含曲目与作者；
      - 换歌后屏幕上立刻是新曲目（不是顶着上一首）；
      - 曲目文本晚于它那一次 `State: … -> idle`（写屏在状态转移之后，idle
        分支的清屏在前——这是 issue #8 唯一必须在真机上验证的时序）；
      - 直播流的 form/total 都是 live/none，且文本里没有 m:ss；
      - 收场之后屏幕上再没有旧曲目名，且参与反馈的原因都有一次 end-state 写屏；
      - **两种暂停的文本不同**（issue #11 的核心：用户据此决定该等还是该说继续）；
      - **暂停写屏带位点、播放写屏带总量**（位点与总量互斥，两态各自只出用户
        当时关心的那个数字）；
      - **屏幕上的位点等于当时真实的位点**（误差在数秒内）——这是 issue #11
        最难用肉眼验的一条（人读不出 1:12 还是 1:16），所以必须在串口上核。

    会话（sessions）用来把「换歌」认出来：每首一条起流锚点，写屏只认「向后最近
    的那条锚点」——那正是它写的曲目。markers 用来把「暂停」认出来（`Music
    pause: kind=…`），跨设备侧的冻结论述与屏幕的呈现对齐。
    """
    now = [s for s in screens if s["screen"]["action"] in ("now-playing", "repaint")]
    ends = [s for s in screens if s["screen"]["action"] == "end-state"]

    # 1) 曲目与作者显示出来了。没抓到任何写屏时只报一条，不把「一个都没写」
    #    重复成五条错误。
    actions = ", ".join(dict.fromkeys(s["screen"]["action"] for s in screens))
    if not now:
        add("screen_shows_track", False,
            "未见任何曲目写屏（Music screen: action=now-playing）"
            + (f"——实际动作：{actions}" if screens
               else "——旧固件没有屏幕锚点；已点名 --expect-screen"))
    else:
        missing = [s for s in now
                   if not s["screen"]["title"]
                   or s["screen"]["title"] not in s["screen"]["text"]
                   or s["screen"]["author"] not in s["screen"]["text"]]
        shown = now[-1]["screen"]
        add("screen_shows_track", not missing,
            f"{len(now)} 次曲目写屏（{actions}），最后一条：text='{shown['text']}'"
            + (f"；{len(missing)} 条缺曲目/作者" if missing else ""))

    # 2) 换歌立刻更新：每条起流锚点后面都该有一条写它那首的曲目写屏。只抓到
    #    一首时无从比较——如实报「不适用」，而不是用一条必然失败把它算成回归。
    anchored = [s for s in sessions if s["session"] is not None]
    stale = []
    for sess in anchored:
        title = sess["session"]["title"]
        after = [s for s in now if s["i"] >= sess["i"]]
        if not after:
            stale.append(f"{title}（起流后无写屏）")
            continue
        first = after[0]["screen"]
        if first["title"] != title:
            stale.append(f"{title} → 屏幕上却是 '{first['title']}'")
    if len(anchored) <= 1:
        add("screen_updates_on_song_change", True,
            f"本次抓取只有 {len(anchored)} 条起流锚点——换歌断言不适用"
            "（需连播两首或换一首）"
            + ("；但仅有的那首也无写屏" if stale else ""))
    else:
        add("screen_updates_on_song_change", not stale,
            f"{len(anchored)} 首均有各自的曲目写屏" if not stale else
            "换歌后屏幕未更新：" + "；".join(stale))

    # 3) 写屏在状态转移之后（issue #8 的核心时序）。两条判据：
    #    a) 曲目写屏必须在 idle 态下（音乐在空闲态下播）；
    #    b) 任何一条曲目写屏之后紧跟着 `-> idle`（中间没有别的写屏）= 那次清屏
    #       吃掉了刚写的曲目——这正是 ticket 要防的那一刻；
    #    c) 至少要有一条写屏前面有过 `-> idle`——否则「写屏在状态转移之后」
    #       这句话在本次抓取里没有任何佐证（没抓到转移行，如抓取开始得太晚）。
    eaten = []
    proven = []
    for s in now:
        after_states = [st for st in states if st["i"] > s["i"]]
        next_write = next((w["i"] for w in screens if w["i"] > s["i"]), None)
        if (after_states and after_states[0]["state"]["new"] == "idle"
                and (next_write is None or after_states[0]["i"] < next_write)):
            eaten.append(s["screen"]["text"])
        if any(st["i"] <= s["i"] and st["state"]["new"] == "idle" for st in states):
            proven.append(s["screen"]["text"])
    if eaten:
        add("screen_set_after_state_change", False,
            "写屏后紧跟着 `-> idle`（那次清屏会吃掉刚写的曲目）："
            + "; ".join(eaten))
    elif proven:
        add("screen_set_after_state_change", True,
            f"{len(proven)}/{len(now)} 条曲目写屏晚于 `-> idle`（未被清屏吃掉）；"
            f"写屏时设备状态：{', '.join(dict.fromkeys(s['screen']['device'] for s in now))}")
    elif any(st["state"]["new"] == "idle" for st in states):
        # 抓到了 `-> idle` 却没有任何写屏在它之后：要么写屏丢在清屏前（就是 ticket
        # 要防的那件事），要么抓取窗口没盖住真正那次起播。两条都不能当通过。
        add("screen_set_after_state_change", False,
            f"抓取里有 {sum(1 for st in states if st['state']['new'] == 'idle')} 次"
            " `-> idle`，但没有一条曲目写屏在它们之后——清屏在前、写屏在后这句"
            "话没得到佐证")
    elif anchored and min(s["i"] for s in anchored) == 0:
        # 抓取从起流那一刻（或更晚）才开始：那次 `-> idle` 在窗口之前，没有
        # 转移行可比——如实报「不适用」，而不是拿一条假红线把它算成回归。
        add("screen_set_after_state_change", True,
            f"{len(now)} 条曲目写屏，但抓取始于起流那一刻（无 `-> idle` 转移）"
            "——转态时序无从比对，请先开抓取再触发播放")
    else:
        # 抓取盖住了起播之前的窗口，却一条 `-> idle` 都没抓到：那说明状态转移
        # 行根本没进日志（或被抓取过滤掉了），「写屏在转移之后」这句话就成了
        # 无凭之谈——不当通过。
        add("screen_set_after_state_change", False,
            f"{len(now)} 条曲目写屏，但整个抓取窗口里一条 `State: … -> idle` "
            "都没有——转态时序无法核对（确认设备日志与抓取过滤条件）")

    # 4) 直播流不显示总量：form/total 与文本三处互相印证。
    live_screens = [s for s in now if s["screen"]["form"] == "live"]
    if live_screens:
        bad = [s for s in live_screens
               if s["screen"]["total"] != "none"
               or _CLOCK_RE.search(s["screen"]["text"])]
        add("live_screen_has_no_total", not bad,
            f"{len(live_screens)} 条直播写屏 total=none、文本无时钟" if not bad else
            "直播流上出现了总量：" + "; ".join(
                f"total={s['screen']['total']} text='{s['screen']['text']}'"
                for s in bad))
    elif any(s["session"] is not None and s["session"]["form"] == "live"
             for s in sessions):
        add("live_screen_has_no_total", False,
            "直播会话未见任何写屏——无法核对「不显示总量」")

    # 5) 收场不留陈旧曲目：每条有屏幕文案的收场都要有一次 end-state 写屏，且写屏
    #    之后不得再出现旧曲目的文本。
    needs_end = [f for f in feedbacks
                 if f["feedback"]["skipped"] is None
                 and f["feedback"]["screen"] not in (None, "none")]
    if needs_end:
        missing = []
        leftover = []
        for f in needs_end:
            hit = [s for s in ends if s["i"] >= f["i"]]
            if not hit:
                missing.append(f["feedback"]["reason"])
                continue
            write = hit[0]
            if write["screen"]["title"]:
                leftover.append(f"{f['feedback']['reason']}：写屏仍带曲目 "
                                f"'{write['screen']['title']}'")
            # 结束态之后（同一次收场里）不得再出现旧曲目的文本。
            later = [s for s in now if s["i"] >= write["i"]]
            titles = {s["session"]["title"] for s in sessions
                      if s["session"] is not None}
            for s in later:
                if any(t and t in s["screen"]["text"] for t in titles):
                    leftover.append(s["screen"]["text"])
        add("ended_screen_has_no_stale_track", not missing and not leftover,
            f"{len(needs_end)} 次收场均给出结束态且不留旧曲目"
            if not missing and not leftover else
            "; ".join(([f"未见结束态写屏：{', '.join(missing)}"] if missing else [])
                      + ([f"屏幕仍留旧曲目：{'; '.join(leftover)}"] if leftover else [])))

    _add_paused_screen_assertions(add, screens, now, sessions, markers)


def _add_paused_screen_assertions(add, screens, now, sessions, markers):
    """暂停态屏幕断言（issue #11）：位点、位点正确性、两种暂停可区分。

    四条断言各抓一类真错（工单要求「断言必须能抓住两种暂停文案被写成一样与
    直播流显示了位点这两个真实错误」）：
      - `pause_screen_distinguishes_kinds`  两种暂停写屏的 text 不同。一条把两种
        写成同一句的实现（典型：忘了按 state 挑前缀）在这里必失败——只断言
        「文本含曲目」的测试看不见。
      - `paused_screen_shows_position`      暂停写屏有数字 pos 且文本含时钟；
        播放写屏不得带 pos（位点与总量互斥）。抓住「暂停了却不显示位点」与
        「播放中反而显示位点」两个方向。
      - `live_screen_has_no_position`       直播写屏的 pos 一律为 live（或无），
        且文本无时钟——抓住「直播流显示了位点」这个真错。本组独立于 #8 的
        `live_screen_has_no_total`：那条只管总量，这条管位点（工单：两样都不显示）。
      - `paused_position_matches_truth`     屏幕上的 pos= 与暂停锚点（`Music
        pause: pos=`）同源：两者都是设备自己报的，同一份记账，故必须一致
        （容差数秒，spec 的验收缝）。它抓的是「屏幕显示了一个设备自己都不认的
        位点」——例如把总量错当位点、或拿陈旧快照去写。

    为什么暂停断言只在真的出现暂停写屏时跑：没按暂停的抓取（只验播放中屏幕）
    不该凭空多出四条必然失败的断言——与 #8 的取舍一致。
    """
    # 3) 直播流不显示位点（issue #11）：**暂停态也照样不能有**。与 #8 的
    #    live_screen_has_no_total 分开——两样都不显示的判据要各自可断言。
    #    扫的是**全部**写屏（now + paused + repaint），不只是 now：漏掉暂停
    #    等于把 issue 里「直播没位点」这半张验收表空着。
    #    ⚠ 位置在前面的「没有暂停写屏就返回」**之前**：那一条是为「四条暂停
    #    断言不该凭空失败」而设的，而直播这条在没按暂停的抓取里同样成立。
    live_writes = [s for s in screens if s["screen"]["form"] == "live"]
    if live_writes:
        bad = [s for s in live_writes
               if s["screen"]["pos"] not in (None, "none", "live")]
        add("live_screen_has_no_position", not bad,
            f"{len(live_writes)} 条直播写屏 pos=live，无数字位点"
            if not bad else
            "直播流上出现了数字位点：" + "; ".join(
                f"action={s['screen']['action']} state={s['screen']['state']} "
                f"pos={s['screen']['pos']} text='{s['screen']['text']}'"
                for s in bad))

    paused_writes = [s for s in screens if s["screen"]["state"].startswith("paused_")]
    if not paused_writes:
        return

    # 1) 两种暂停的文本必须不同（同一首曲目、同一位置时尤其如此——那是最容易
    #    被写成一样的形状）。按 state 分组比对：只要有至少两组，它们的 text
    #    就不能撞。只出现一种暂停时报「不适用」，不用一条必然失败算回归。
    by_state = {}
    for s in paused_writes:
        by_state.setdefault(s["screen"]["state"], []).append(s["screen"]["text"])
    if len(by_state) >= 2:
        # 取每一态最后一次写屏的正文（去掉前缀后的部分）来比：前缀不同才算
        # 可区分，正文本就该一样（曲目/作者/位点）。直接比整串会把「前缀不同、
        # 正文也碰巧不同」的偶然当判据。这里比整串——两个 Lang 前缀本就不同，
        # 整串不同是它们必然的结果；相同才是错。
        texts = {state: texts[-1] for state, texts in by_state.items()}
        distinct = len(set(texts.values())) == len(texts)
        add("pause_screen_distinguishes_kinds", distinct,
            "两种暂停的屏幕文本不同：" + "；".join(
                f"{state}={text!r}" for state, text in texts.items())
            if distinct else
            "两种暂停被写成了同一个文本（用户分不出该等还是该说继续）："
            + "；".join(f"{state}={text!r}" for state, text in texts.items()))
    else:
        add("pause_screen_distinguishes_kinds", True,
            f"本次抓取只有一种暂停（{next(iter(by_state))}）——两种文案的区分"
            "断言不适用（会话性暂停与用户暂停各来一次才能比对）")

    # 2) 暂停写屏带位点、播放写屏不带。方向上正反都查：暂停缺位点、播放多了位点
    #    都是错（位点与总量在屏幕上是互斥的两态）。
    #    直播流的暂停写屏例外：它本来就**不该**有位点（pos=live，见上面第 3 条），
    #    拿「暂停就必须有位点」去卡它会让正确的屏幕被误报成违规。
    paused_without_pos = [s for s in paused_writes
                          if s["screen"]["form"] != "live"
                          and (s["screen"]["pos"] in (None, "none", "live")
                               or not _CLOCK_RE.search(s["screen"]["text"]))]
    playing_with_pos = [s for s in now
                        if s["screen"]["state"] == "playing"
                        and s["screen"]["pos"] not in (None, "none", "live")]
    add("paused_screen_shows_position",
        not paused_without_pos and not playing_with_pos,
        f"{len(paused_writes)} 次暂停写屏各带位点，播放写屏不带"
        if not paused_without_pos and not playing_with_pos else
        "; ".join(
            ([f"暂停写屏未显示位点：" + "; ".join(
                f"state={s['screen']['state']} pos={s['screen']['pos']} "
                f"text='{s['screen']['text']}'" for s in paused_without_pos)]
             if paused_without_pos else [])
            + ([f"播放写屏带了位点：" + "; ".join(
                f"pos={s['screen']['pos']} text='{s['screen']['text']}'"
                for s in playing_with_pos)] if playing_with_pos else [])))

    # 4) 屏幕上的位点等于当时真实的位点（误差数秒）：暂停锚点的 pos= 与它那条
    #    写屏的 pos= 同源（设备自己的记账），故取「写屏之前最近的一条暂停锚点」
    #    比对。抓的是：屏幕拿总量冒充位点、拿陈旧快照写、或两处折算分叉。
    pause_anchors = [m for m in markers if m.get("pause") is not None]
    mismatched = []
    compared = 0
    for s in paused_writes:
        pos = s["screen"]["pos"]
        if pos in (None, "none", "live"):
            continue
        before = [m for m in pause_anchors if m["i"] is not None and m["i"] <= s["i"]]
        if not before:
            continue
        anchor_pos = before[-1]["pause"]["pos"]
        if anchor_pos in (None, "none", "live"):
            continue
        compared += 1
        if abs(float(pos) - float(anchor_pos)) > PAUSE_SCREEN_POS_TOL:
            mismatched.append(
                f"屏幕 pos={pos} 与暂停锚点 pos={anchor_pos}（state="
                f"{s['screen']['state']}）差超过 {PAUSE_SCREEN_POS_TOL}s")
    if compared:
        add("paused_position_matches_truth", not mismatched,
            f"{compared} 条暂停写屏的位点与暂停锚点一致（±{PAUSE_SCREEN_POS_TOL}s）"
            if not mismatched else "; ".join(mismatched))
    else:
        # 有暂停写屏但没有可比的暂停锚点（例如抓取只覆盖了屏幕那一侧）：不适用。
        add("paused_position_matches_truth", True,
            f"有 {len(paused_writes)} 次暂停写屏，但无相邻的暂停锚点可比——"
            "位点真值核对不适用")


def _add_ending_assertions(add, endings, feedbacks, expect_ending, events, markers,
                           last_t=None):
    """收场断言组（issue #7）：三种收场可区分、用户主动停止不报故障。

    只在抓取里真的出现收场锚点（`Music ended:`）或调用方用 `--expect-ending`
    点名要求时断言——没跑到收场的抓取（issue #3/#4 那套核验）不该凭空多断言。

    判据一律以**锚点行自报的信息**为准，不靠耳朵：
      - ended 锚点的 reason/played 固定了这次会话的真相；
      - feedback 锚点的 sound/screen 是应用侧对真相的处置，逐条比对照表；
      - 用户主动停止与换歌一律不得伴提示音（issue #7 的硬不变量）；
      - 暂停不是收场：按钮打断只暂停，暂停跨度里不该出现收场锚点；
      - 收场后设备必须回到可交互（唤醒词恢复 / 回到 Listening）。
    """
    expected = tuple(expect_ending or ())
    if not endings and not expected:
        return

    records = [e["ending"] for e in endings]
    if records:
        reasons = list(dict.fromkeys(r["reason"] for r in records))
        unknown = sorted({r for r in reasons if r not in ENDING_FEEDBACK})
        add("ending_reason_recorded", not unknown,
            ("收场原因：" + ", ".join(reasons)) if not unknown else
            "未知收场原因：" + ", ".join(unknown)
            + f"（实际收到：{', '.join(reasons)}）")

        # 自我声明的一致性：没出过声就没有「中断」可言（那是起流失败）。
        bad_flags = []
        for r in records:
            want = ENDING_REQUIRES_PLAYED.get(r["reason"])
            if want is not None and bool(r["played"]) != want:
                bad_flags.append(f"{r['reason']} 却报 played={r['played']}")
        add("ending_played_flag_consistent", not bad_flags,
            "played 标志与收场原因一致" if not bad_flags else "；".join(bad_flags))

        # 逐条比对照表：提示音与屏幕文案都得对上，两个方向都查（反馈缺了、错了、
        # 或者根本收场错）——这样「固件改了一边忘了另一边」藏不住。
        mismatched = []
        missing = []
        tolerated = []
        used = set()
        for e in endings:
            r = e["ending"]
            want = ENDING_FEEDBACK.get(r["reason"])
            if want is None:
                continue  # 未知原因已由 ending_reason_recorded 报过
            # 跳过已被前一条收场消费的反馈：一次抓取可能连播多首且原因相同
            # （两首都是 completed），逐条配对才不会串位。
            hits = [(i, f) for i, f in enumerate(feedbacks)
                    if i not in used and f["feedback"]["reason"] == r["reason"]
                    and 0 <= f["t"] - e["t"] <= FEEDBACK_MAX_DELAY_S]
            if not hits:
                if _ending_is_last_sample(r, endings, last_t):
                    # 收场落在抓取窗口末尾：反馈还没打出来就被截了。
                    tolerated.append(r["reason"])
                else:
                    missing.append(r["reason"])
                continue
            idx, hit = hits[0]
            used.add(idx)
            # 跳过型反馈（换歌）没有 sound=/screen= 字段：它本来就该无声无屏，
            # 按 none 计——「没字段」与「字段写 none」在这里是同一件事。
            got = (hit["feedback"]["sound"] or "none",
                   hit["feedback"]["screen"] or "none")
            if got != want:
                mismatched.append(f"{r['reason']}：期望 sound={want[0]} "
                                  f"screen={want[1]}，实收 sound={got[0]} "
                                  f"screen={got[1]}")
        # 两条分开报：「反馈有没有到」和「到的那条对不对」是两件事——混在
        # 一条里，音错了的详情会被「未见反馈」的措辞遮蔽。
        add("ending_has_feedback", not missing,
            _ending_feedback_detail(records, [], missing, tolerated))
        add("ending_feedback_matches_reason", not mismatched,
            _ending_feedback_detail(records, mismatched, [], []))

        # 自然播完与链路中断必须给**不同**的音（issue #7 的核心），且都不是静音
        # ——上面那条比的是实际抓到的音；这条卡的是对照表本身，防止两边一起
        # 被改成同一个音而断言仍绿。
        done = ENDING_FEEDBACK["completed"][0]
        broken = ENDING_FEEDBACK["interrupted"][0]
        add("ending_cues_are_distinguishable",
            done != broken and done != "none" and broken != "none",
            f"自然播完 sound={done}，链路中断 sound={broken}（必须不同且都不是静音）")

        # 续播失败与链路中断共用 `alert` 音——它们的分辨出口只有屏幕（issue
        # #12）。这条卡对照表本身：两条的 screen 一旦被写成同一个值，「续播失败
        # 看得见」就退化成「跟中断长得一样」，而上面那条（音不同）照样绿。
        warn_cue = ENDING_FEEDBACK["interrupted"][0]
        warn_screens = {ENDING_FEEDBACK[name][1]
                        for name in ("interrupted", "resume_failed")
                        if ENDING_FEEDBACK[name][0] == warn_cue}
        add("warning_screens_are_distinguishable",
            len(warn_screens) == len([n for n in ("interrupted", "resume_failed")
                                      if ENDING_FEEDBACK[n][0] == warn_cue])
            and "none" not in warn_screens,
            "共用告警音的收场屏幕文案：" + ", ".join(
                f"{n}={ENDING_FEEDBACK[n][1]}"
                for n in ("interrupted", "resume_failed")))

        # 用户主动停止 / 换歌：绝不报故障音——也不能报「放完了」的喜庆音。
        noisy = [f"{f['feedback']['reason']}(sound={f['feedback']['sound']})"
                 for f in feedbacks
                 if f["feedback"]["reason"] in NO_WARNING_ENDINGS
                 and f["feedback"]["sound"] not in (None, "none")]
        silent = list(dict.fromkeys(r["reason"] for r in records
                                    if r["reason"] in NO_WARNING_ENDINGS))
        add("user_stop_never_warns", not noisy,
            ("用户主动停止/换歌均无声（"
             + (", ".join(silent) if silent else "本次未出现")
             + "）") if not noisy else
            "用户主动停止/换歌却报了提示音：" + ", ".join(noisy))

        # 暂停不是收场：按钮打断只暂停（会话继续），暂停跨度里不该出现收场锚点。
        inside = [f"{e['ending']['reason']}@{t0:.1f}s" for e in endings
                  for t0, t1 in _pause_intervals(events, markers)
                  if t0 <= e["t"] <= t1]
        add("pause_is_not_an_ending", not inside,
            "收场均发生在暂停跨度之外" if not inside else
            "暂停跨度里出现收场锚点：" + ", ".join(inside)
            + "（按钮打断只是暂停，不该结束会话）")

        # 回到可交互：每次收场后设备必须重新可用（唤醒词恢复 / 回到 Listening）。
        # 跳过型反馈（换歌）没有这组字段——那时新会话持有屏幕与音箱。
        #
        # 关于 wake_word=on 这条曾经写在这里的检查：它是**恒真**的，已在
        # 2026-09-13 核实并删除。原因：interactive 的取值由 wake_word 推导而来
        # （application.cc：wake_word ? "wake_word_only" : "none"），所以
        # interactive==wake_word_only **蕴含** wake_word 为真，那句
        # `wake_word != "on"` 永远不成立，零判别力。
        #
        # 为什么不换成硬要 wake_word=on：`EnableWakeWordDetection(true)` 在缺
        # 唤醒词资源（未配唤醒词／引擎起不来）时会**如实**清掉
        # AS_EVENT_WAKE_WORD_RUNNING（audio_service.cc:663），固件也刻意如实
        # 上报（application.cc：「唤醒词可能因没配/引擎起不来而真没恢复，那时
        # 报 on 就是在撒谎」）。硬要 on 会把「设备本来就没配唤醒词」误判成回归；
        # interactive=already（已回 Listening）时唤醒词本就该关（聆听走 AFE
        # 路径），off 也是正确状态。
        #
        # 真正有判别力的判据：`none` 是**非法收场态**——它意味着设备既没在聆听、
        # 也没空闲到能回聆听、唤醒词又没起来，正是固件刻意避免的「半死状态」。
        # 所以白名单就是主判据，而播放期唤醒词本应关闭（音乐模式停唤醒词，
        # ADR-0008），若收场后仍报 off 且没回 Listening，就是恢复没做。
        bad_interactive = []
        for f in feedbacks:
            fb = f["feedback"]
            if fb["skipped"] is not None:
                continue
            if fb["interactive"] not in INTERACTIVE_MODES:
                bad_interactive.append(f"{fb['reason']}：interactive={fb['interactive']}")
            elif (fb["interactive"] == "wake_word_only"
                  and fb["wake_word"] != "on"):
                # 不可达（见上），但留着做解析自洽校验：日志格式改歪时先叫。
                bad_interactive.append(
                    f"{fb['reason']}：报 interactive=wake_word_only "
                    f"却 wake_word={fb['wake_word']}（日志自相矛盾）")
        restored = [f["feedback"] for f in feedbacks
                    if f["feedback"]["skipped"] is None]
        # 只在本该恢复的场景下断言：换歌的跳过型反馈本来就无事可做（新会话
        # 持有屏幕与音箱），没有反馈锚点的情形已由上面那条报「未见反馈」。
        if restored:
            if bad_interactive:
                detail = "收场后未回到可交互：" + "; ".join(bad_interactive)
            else:
                last = restored[-1]
                detail = (f"{len(restored)} 次收场后回到可交互"
                          f"（wake_word={last['wake_word']}, "
                          f"interactive={last['interactive']}）")
            add("playback_returns_interactive", not bad_interactive, detail)

    # 可选：本次抓取要覆盖指定的收场原因（--expect-ending）。
    if expected:
        seen = {r["reason"] for r in records}
        absent = [r for r in expected if r not in seen]
        add("ending_expected_seen", not absent,
            ("出现的收场原因：" + (", ".join(sorted(seen)) if seen else "（无）"))
            + ("" if not absent else "；缺：" + ", ".join(absent)))


def _ending_is_last_sample(ending, endings, last_t):
    """这条收场是不是抓取窗口里的**最后一行**（反馈没来得及打就被截了）。

    ending 是收场记录本身（`e["ending"]`）。只放行这一种情况：收场就是窗口
    末尾——那时「反馈还没到」与「反馈丢了」在数据上不可分。只要后面还有任何
    一行遥测，设备就有时间打反馈却没打，按失败算。
    """
    if endings and ending is not endings[-1]["ending"]:
        return False
    t = next(e["t"] for e in endings if e["ending"] is ending)
    return last_t is None or t >= last_t


def _ending_feedback_detail(records, mismatched, missing, tolerated):
    """收场-反馈对照的明细：对了几条、哪条错了、哪条没等到。"""
    shown = ", ".join(dict.fromkeys(r["reason"] for r in records))
    if not mismatched and not missing:
        parts = [f"{len(records)} 条收场（{shown}）均已反馈"]
    else:
        parts = [f"{len(records)} 条收场（{shown}）"]
    if mismatched:
        parts.append("音屏不符：" + "; ".join(mismatched))
    if missing:
        parts.append("未见反馈：" + ", ".join(dict.fromkeys(missing)))
    if tolerated:
        parts.append("窗口末尾未及反馈：" + ", ".join(dict.fromkeys(tolerated)))
    return "；".join(parts)


def _add_pause_assertions(add, events, markers, expect_restart, expect_auto_resume,
                          live=False):
    """暂停/续播断言组（issue #4）。

    只在抓取里真的出现暂停/续播痕迹时断言——没暂停的抓取（issue #3 那套
    「播到哪了」核验）不该因为「没暂停」而报失败。要强制要求覆盖暂停，用
    `--expect-restart` / `--expect-auto-resume`，或直接把抓取窗口覆盖住暂停。
    """
    pause_markers = [m for m in markers if m["pause"] is not None]
    resume_markers = [m for m in markers if m["resume"] is not None]
    auto_markers = [m for m in markers if m["auto_resume"] is not None]
    paused_events = [e for e in events if e["parsed"]["paused"] is not None]

    if paused_events or pause_markers:
        # 暂停「段」= 连续的暂停样本（可能多次暂停）。位点冻结与续播接缝都在
        # 每一段内部比对：整段抓取里的第一段与第二段之间位点当然会变（中间
        # 播过一段），拿全局首尾比就把正常抓取判成失败。
        spans = _pause_spans(events)
        # 两种暂停语义可区分：暂停痕迹带种类，且是允许的两个值之一。
        _add_pause_kind_assertion(add, paused_events, pause_markers, live)
        if not live:
            # 1) 位点冻结：每一段暂停内的位点都不再增长。两个来源都要核——进
            #    暂停时的锚点（Music pause: pos=…，紧挨着这一段之前打出）与
            #    暂停期间的 pipe: 行，它们必须停在同一个值上，这也就是「暂停后
            #    位点不再增长」的直接证据。
            growths = []
            frozen_values = []
            for start, end in spans:
                seg = [e["parsed"]["pos"] for e in events[start:end + 1]
                       if _is_num(e["parsed"]["pos"])]
                if not seg:
                    continue
                span_t0 = events[start]["t"]
                # 紧邻这段暂停之前的锚点（同一拍或最多一条遥测之内）。
                anchors = [m["pause"]["pos"] for m in pause_markers
                           if span_t0 - 3.0 <= m["t"] <= span_t0
                           and _is_num(m["pause"]["pos"])]
                frozen_values.append(seg[0])
                for a, b in zip(seg, seg[1:]):
                    if b > a:
                        growths.append((a, b))
                if anchors and seg[0] > anchors[-1] + RESUME_SEAM_TOL:
                    # 锚点自报冻在更靠前的位点，而暂停期间的 pipe 行却报得更大
                    # ——暂停那一刻之后位点还在涨。
                    growths.append((anchors[-1], seg[0]))
            if not spans and not pause_markers:
                add("pause_freezes_position", False,
                    "暂停样本没有数字位点（暂停时空闲？确认抓取窗口覆盖住暂停）")
            elif not frozen_values:
                add("pause_freezes_position", False,
                    "暂停跨度内没有数字位点（直播流？确认抓取窗口覆盖住暂停）")
            else:
                detail = (f"{len(frozen_values)} 段暂停，位点分别冻结在 "
                          + ", ".join(_fmt_secs(v) for v in frozen_values))
                add("pause_freezes_position", not growths,
                    detail if not growths else
                    "暂停期间位点仍在增长：" + ", ".join(
                        f"{_fmt_secs(a)}→{_fmt_secs(b)}" for a, b in growths[:3]))

        # 用户暂停绝不自动续（spec：「说『暂停』后随便聊一句，音乐不自动响」）：
        # 任何一个 user 种类的暂停跨度内出现 Music auto-resume: 标记即失败。
        offending = _user_pause_auto_resume_hits(spans, events, pause_markers,
                                                 resume_markers, auto_markers)
        if _has_user_pause(events, pause_markers):
            add("user_pause_never_auto_resumes", not offending,
                "用户暂停跨度内无 Music auto-resume: 标记"
                if not offending else
                f"用户暂停期间音乐自动响了 {len(offending)} 次"
                + ("（自动续播只允许发生在会话性暂停跨度内）"))

    if resume_markers:
        # 3) 续播接在原处：continue 必须接在**上一段**暂停冻结的位点上
        #    （±0.5s，spec 的验收缝），restart 的回退按锚点自报的 margin 判
        #    （≤ margin+0.5s）且不前进超过 0.5s。每段暂停各配一次续播，按序配对。
        spans = _pause_spans(events)
        resume_frozen = []
        for m in resume_markers:
            frozen = None
            for start, end in spans:
                if events[start]["t"] <= m["t"]:
                    frozen = events[start]["parsed"]["pos"]
                else:
                    break
            for pm in pause_markers:
                if pm["t"] <= m["t"] and _is_num(pm["pause"]["pos"]):
                    frozen = pm["pause"]["pos"]
            resume_frozen.append(frozen)
        bad = []
        for m, frozen in zip(resume_markers, resume_frozen):
            r = m["resume"]
            if r["pos"] == "live" or r["from_s"] == "live":
                continue  # 直播流位点无意义：重连即正确
            if r["mode"] == "continue":
                # continue 没有 from=（它没换流）：接缝就是 at= 本身。核对它
                # 是否就是暂停冻结的那个位点——跳了就说明续播没接在原处。
                if frozen is None or not _is_num(r["pos"]):
                    continue
                if abs(r["pos"] - frozen) > RESUME_SEAM_TOL:
                    bad.append(f"continue at={_fmt_secs(r['pos'])} 但暂停冻在 "
                               f"{_fmt_secs(frozen)}")
            else:
                if r["from_s"] is None or not _is_num(r["pos"]):
                    continue
                # 回退不超过锚点自报的 margin + 容差；前进不超过容差。
                margin = (float(r["margin_s"]) if r["margin_s"] is not None
                          else RESTART_MARGIN_FALLBACK)
                delta = r["pos"] - r["from_s"]
                if not (-(margin + RESUME_SEAM_TOL) <= delta <= RESUME_SEAM_TOL):
                    bad.append(f"restart at={_fmt_secs(r['pos'])} from="
                               f"{_fmt_secs(r['from_s'])} margin={margin:g}s "
                               f"（Δ={delta:+.1f}s 越界）")
        modes = ", ".join(f"{m['resume']['mode']}@at={_fmt_secs(m['resume']['pos'])}"
                          for m in resume_markers)
        add("resume_continues_position", not bad,
            f"续播接缝：{modes}" if not bad else "接缝越界：" + "; ".join(bad))
    elif paused_events:
        add("resume_continues_position", False,
            "有暂停但没有续播锚点（Music resume: mode=…）——音乐没接上？")

    # 4) 可选：本次抓取要覆盖「原连接不可用 → 重新起流」这条分支。
    if expect_restart:
        restarts = [m for m in resume_markers if m["resume"]["mode"] == "restart"]
        add("restart_path_seen", bool(restarts),
            f"重启式续播 {len(restarts)} 次（mode=restart）"
            if restarts else
            "未见 mode=restart——暂停要够久（超过上游 proxy_read_timeout）才会走这条")

    # 5) 可选：静默数秒确实触发了自动续播。
    if expect_auto_resume:
        add("auto_resume_fired", bool(auto_markers),
            f"自动续播 {len(auto_markers)} 次"
            + (f"（quiet={auto_markers[0]['auto_resume']['quiet_s']}s）"
               if auto_markers else "——会话性暂停后静默数秒未触发"))


def _pause_spans(events):
    """暂停跨度（连续带暂停标记的样本段）的 [start, end] 下标列表。

    位点冻结与续播接缝都在**每一段内部**比对：一次抓取可能有多次暂停，
    段与段之间位点当然会变（中间播了一段），拿全局首尾比会把正常抓取判成
    失败。
    """
    spans = []
    start = None
    for idx, event in enumerate(events):
        paused = event["parsed"]["paused"] is not None
        if paused and start is None:
            start = idx
        elif not paused and start is not None:
            spans.append((start, idx - 1))
            start = None
    if start is not None:
        spans.append((start, len(events) - 1))
    return spans


def _has_user_pause(events, pause_markers):
    """抓取里是否出现过**用户**种类的暂停（pipe: 标记或暂停锚点任一）。"""
    return any(e["parsed"]["paused"] == "user" for e in events) or any(
        m["pause"]["kind"] == "user" for m in pause_markers)


def _user_pause_auto_resume_hits(spans, events, pause_markers, resume_markers,
                                 auto_markers):
    """落在**用户**暂停窗口内的自动续播标记 list（非空即失败）。

    窗口按「暂停锚点切开的子段」算，不把整段暂停当一种语义——两种暂停可以
    紧挨着（用户暂停后立刻被唤醒 → 会话性暂停），中间没有在播样本，位点跨度
    会把它们并成一段；拿整段按 user 判，会话性那半段的合法自动续播就被误伤。

    窗口有两类来源：
      - 锚点：每条 `Music pause: kind=user` 到**下一个暂停锚点 / 第一条续播
        锚点 / +8s**（三者取最近）——覆盖「用户暂停短于一拍遥测、没有暂停
        样本」的情形，同时不越界吞掉后面那段别的语义的暂停；
      - 子段：连续带暂停标记的 pipe: 样本段按暂停锚点切分，子段语义取**它
        起点之前最近的暂停锚点**（锚点先打、随后才是暂停样本，真机就这样），
        没有锚点时取样本自己的标记。窗口终点延到该子段之后的第一条续播锚点
        （最多 +2.5s，略多于一拍遥测）。
    只认「有 user 痕迹」的子段：会话性暂停里的 auto-resume 是正常行为——用户
    的「暂停」之后随便聊一句，音乐不该自己响。用户暂停若被误降级成会话性
    （管道样本标成 PAUSED_CONV），锚点那份 kind=user 仍会把这段判成用户窗口，
    bug 藏不住。
    """
    kAnchorTailS = 8.0   # 锚点后既无下一条锚点也无续播锚点时的兜底（> 静默阈值 5s）
    kSpanTailS = 2.5     # 子段后的尾窗（略多于一拍遥测）
    windows = []
    anchor_times = sorted(m["t"] for m in pause_markers)
    resume_times = sorted(r["t"] for r in resume_markers)
    for m in pause_markers:
        if m["pause"]["kind"] != "user":
            continue
        t0 = m["t"]
        later = [t for t in resume_times if t >= t0]
        later += [t for t in anchor_times if t > t0]
        t_end = min(later) if later else t0 + kAnchorTailS
        windows.append((t0, t_end))
    for t0, t1, kind in _pause_segments(spans, events, pause_markers):
        if kind != "user":
            continue
        later = [t for t in resume_times if t >= t1]
        t_end = min(t1 + kSpanTailS, min(later)) if later else t1 + kSpanTailS
        windows.append((t0, t_end))
    return [m for m in auto_markers
            if any(t0 <= m["t"] <= t1 for t0, t1 in windows)]


def _pause_segments(spans, events, pause_markers):
    """把暂停跨度按暂停锚点切成同语义子段：[(t0, t1, kind), …]。

    子段语义 = 起点之前最近的暂停锚点的 kind（kAnchorLookbackS 内），没有就取
    子段第一个样本的标记；两者都没有（既无锚点也无标记）时按 conversation 算
    ——「无证据」不该升级成用户暂停，那是会误报的方向。
    """
    kAnchorLookbackS = 3.0
    segments = []
    for start, end in spans:
        t_start = events[start]["t"]
        t_end = events[end]["t"]
        cuts = [m["t"] for m in pause_markers if t_start < m["t"] <= t_end]
        bounds = [t_start] + sorted(cuts) + [t_end]
        for idx in range(len(bounds) - 1):
            seg_t0, seg_t1 = bounds[idx], bounds[idx + 1]
            nearby = [m for m in pause_markers
                      if seg_t0 - kAnchorLookbackS <= m["t"] <= seg_t0 + 0.5]
            if nearby:
                kind = max(nearby, key=lambda m: m["t"])["pause"]["kind"]
            else:
                sample_kinds = {events[i]["parsed"]["paused"]
                                for i in range(start, end + 1)
                                if seg_t0 <= events[i]["t"] <= seg_t1}
                kind = ("user" if "user" in sample_kinds
                        else "conversation")
            segments.append((seg_t0, seg_t1, kind))
    return segments


def _fmt_secs(value):
    """位点显示：int 按整秒（`73s`），float 按一位小数（`73.4s`），非数字原样。"""
    if not _is_num(value):
        return str(value)
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.1f}s"
    return f"{value:g}s"


def find_port():
    ports = (glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
             + glob.glob("/dev/cu.SLAB*"))
    return ports[0] if ports else None


def capture_with_log(port, duration, log_file):
    """抓串口 duration 秒，原始字节写进 log_file。

    返回 (samples, 关键行文本列表)。samples 供 --assert 断言；
    pyserial 延迟到调用才导入：纯逻辑测试不依赖串口库。
    """
    import serial

    samples = []
    key_lines = []
    ser = serial.Serial(port, 115200, timeout=1)
    try:
        buf = b""
        start = time.time()
        while time.time() - start < duration:
            chunk = ser.read(4096)
            if not chunk:
                continue
            log_file.write(chunk)
            log_file.flush()
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                t = time.time()
                if ("pipe:" in text or "Music" in text or "music" in text
                        or "PAUSED" in text or "StateMachine" in text):
                    ts = time.strftime("%H:%M:%S", time.localtime(t))
                    tagged = f"[{ts}] {text}"
                    key_lines.append(tagged)
                    print(tagged)
                    samples.append({"t": t, "line": text})
    finally:
        ser.close()
    return samples, key_lines


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    # start/live 缺省为 None（未指定）：起流锚点自报的 start=/form= 优先，
    # CLI 只在没抓到锚点时兜底。
    flags = {"assert": False, "start": None, "live": None, "min-lines": 3,
             "offset-tol": DEFAULT_OFFSET_TOL, "expect-restart": False,
             "expect-auto-resume": False, "expect-ending": [],
             "expect-screen": False}
    positional = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--assert":
            flags["assert"] = True
        elif a == "--live":
            flags["live"] = True
        elif a == "--expect-restart":
            # 本次抓取要求覆盖「原连接不可用 → 重新起流」这条分支
            # （暂停够久，超过上游 proxy_read_timeout 才会走到）。
            flags["expect-restart"] = True
        elif a == "--expect-auto-resume":
            # 本次抓取要求覆盖「会话性暂停 → 静默数秒自动续播」。
            flags["expect-auto-resume"] = True
        elif a == "--expect-screen":
            # 本次抓取要验证屏幕出口（issue #8）：没抓到屏幕锚点就是失败，
            # 不静默放行——「这次要验屏幕」是个明确的意图。
            flags["expect-screen"] = True
        elif a == "--expect-ending":
            # 本次抓取要求覆盖指定的收场原因（issue #7）。可重复，也可逗号分隔：
            #   --expect-ending completed --expect-ending interrupted
            i += 1
            flags["expect-ending"] += [part.strip() for part in args[i].split(",")
                                       if part.strip()]
        elif a == "--start":
            i += 1
            flags["start"] = int(args[i])
        elif a == "--min-lines":
            i += 1
            flags["min-lines"] = int(args[i])
        elif a == "--offset-tol":
            i += 1
            flags["offset-tol"] = float(args[i])
        else:
            positional.append(a)
        i += 1
    duration = int(positional[0]) if positional else 120

    port = find_port()
    if not port:
        print("❌ 没找到 USB 串口（/dev/cu.usbmodem*）。确认 USB 线已插好。")
        return 1
    print(f"✅ 打开 {port}，抓取 {duration}s，全量日志 → /tmp/serial_full.log")
    print("   关注行: pipe: ring=…(ring水位) in_buf=…(解码缓冲) read=…/2s(网络读) "
          "pushed=… fail=… pos=…(位点) PAUSED_CONV/PAUSED_USER(暂停种类)")
    print("           Music screen: action=…(屏幕被设成什么) "
          "State: … -> idle(状态转移，用于核对写屏时机)")

    with open("/tmp/serial_full.log", "ab") as f:
        f.write(f"\n==== capture {time.strftime('%Y-%m-%d %H:%M:%S')} ====\n".encode())
        samples, key_lines = capture_with_log(port, duration, f)

    if not flags["assert"]:
        print(f"\n==== 完成，共 {len(key_lines)} 行关键日志，全量见 /tmp/serial_full.log ====")
        return 0

    print(f"\n==== 位点断言（start={flags['start']}s live={flags['live']}）====")
    results = evaluate_capture(
        samples, start_s=flags["start"], live=flags["live"],
        min_lines=flags["min-lines"], offset_tol=flags["offset-tol"],
        expect_restart=flags["expect-restart"],
        expect_auto_resume=flags["expect-auto-resume"],
        expect_ending=tuple(flags["expect-ending"]),
        expect_screen=True if flags["expect-screen"] else None)
    ok = True
    for r in results:
        mark = "✅" if r.ok else "❌"
        print(f"  {mark} {r.name}: {r.detail}")
        ok = ok and r.ok
    print(f"==== 断言{'全部通过' if ok else '未通过'} ====")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
