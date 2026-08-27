"""baby_care — 小智语音 ↔ baby-care-bridge 育儿记录联动。

对着小智设备说：
- "花生喂了120毫升"     → baby_care_record(subject=花生, note=120毫升)
- "宝妈吸完奶了"         → baby_care_record(subject=宝妈)
- "补记一下咖啡半小时前喂过" → baby_care_record(subject=咖啡, ts=过去时间)
- "上次喂奶什么时候""今天喂了几次" → baby_care_get_state()

baby-care-bridge 跑在本机 8000（回环直连，不经 nginx）。

语音侧用宝宝小名（哥哥咖啡、弟弟花生）；bridge 落库仍用
"大宝/小宝/宝妈"（历史数据与飞书群沿用），两侧经映射表双向转换。
"""
import time

import httpx
from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

BABY_CARE_BASE = "http://127.0.0.1:8000"
SUBJECTS = ("咖啡", "花生", "宝妈")  # 语音交互用小名；宝妈=吸奶记录（用吸奶间隔）
NAME_TO_BRIDGE = {"咖啡": "大宝", "花生": "小宝", "宝妈": "宝妈"}
BRIDGE_TO_NAME = {v: k for k, v in NAME_TO_BRIDGE.items()}


def _client():
    return httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=3.0))  # bridge 同步飞书常态 6~8s


BABY_CARE_RECORD_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "baby_care_record",
        "description": (
            "记录育儿喂养事件（喂奶/吸奶），会同步到飞书群。"
            "用户说'喂了''吃完了''吸完奶了''记一下'等时调用。"
            "同一主体30分钟内的重复记录会被幂等拒绝（返回idempotent）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "enum": list(SUBJECTS),
                    "description": "主体：咖啡（哥哥）、花生（弟弟）喂奶；宝妈吸奶",
                },
                "note": {
                    "type": "string",
                    "description": "备注：奶量（如'120毫升'）、时长、特殊情况等。可选",
                },
                "ts": {
                    "type": "integer",
                    "description": "事件发生的Unix时间戳(秒)。不传=现在；用户说'半小时前'等过去时间时换算后传入",
                },
                "lang": {"type": "string", "description": "用户语言code，默认zh_CN"},
            },
            "required": ["subject", "lang"],
        },
    },
}


@register_function("baby_care_record", BABY_CARE_RECORD_FUNCTION_DESC, ToolType.SYSTEM_CTL)
async def baby_care_record(
    conn: "ConnectionHandler",
    subject: str,
    lang: str = "zh_CN",
    note: str = "",
    ts: int = 0,
):
    if subject not in SUBJECTS:
        return ActionResponse(Action.REQLLM, f"主体必须是 {'、'.join(SUBJECTS)} 之一，请向用户确认。", None)

    body = {"subject": NAME_TO_BRIDGE[subject], "note": note or ""}
    if ts and int(ts) > 0:
        body["ts"] = int(ts)
        body["mode"] = "backfill"
    else:
        body["ts"] = int(time.time())
        body["mode"] = "now"

    try:
        async with _client() as c:
            resp = await c.post(f"{BABY_CARE_BASE}/feed", json=body)
        data = resp.json()
    except Exception as e:
        logger.bind(tag=TAG).error(f"baby_care_record 请求失败: {e}")
        # 超时≠失败：bridge 飞书同步慢时可能已落库，让用户可查询确认，不要断言失败
        hint = "育儿记录服务响应很慢" if isinstance(e, httpx.TimeoutException) else "育儿记录服务暂时连不上"
        return ActionResponse(
            Action.REQLLM,
            f"{hint}，刚才的记录可能已经记上了，不要直接说失败；"
            f"建议用户稍后问一句'今天喂了几次'确认，或到网页端查看。",
            None,
        )

    logger.bind(tag=TAG).info(f"baby_care_record: {body} -> {data}")

    status = data.get("status", "")
    next_due = data.get("next_due", "")
    if status == "idempotent":
        report = (
            f"根据下列数据用{lang}回应：刚尝试给{subject}记录喂养，但系统提示30分钟内已有记录（幂等去重）。\n"
            f"(向用户说明这条没有被重复记录；如需强制补记，请用户到网页端操作)"
        )
    else:
        extra = f"，备注'{note}'" if note else ""
        report = (
            f"根据下列数据用{lang}回应：{subject}的喂养已记录成功{extra}。\n"
            f"下次预计时间: {next_due}\n"
            f"(自然地播报记录成功和下次时间)"
        )
    return ActionResponse(Action.REQLLM, report, None)


BABY_CARE_STATE_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "baby_care_get_state",
        "description": (
            "查询育儿喂养状态：各主体上次喂养时间、下次预计时间、今日次数。"
            "用户问'上次喂奶什么时候''下次几点喂''今天喂了几次''该喂了吗'等时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "lang": {"type": "string", "description": "用户语言code，默认zh_CN"},
            },
            "required": ["lang"],
        },
    },
}


@register_function("baby_care_get_state", BABY_CARE_STATE_FUNCTION_DESC, ToolType.SYSTEM_CTL)
async def baby_care_get_state(conn: "ConnectionHandler", lang: str = "zh_CN"):
    try:
        async with _client() as c:
            resp = await c.get(f"{BABY_CARE_BASE}/state")
        data = resp.json()
    except Exception as e:
        logger.bind(tag=TAG).error(f"baby_care_get_state 请求失败: {e}")
        return ActionResponse(Action.REQLLM, "育儿记录服务暂时连不上，请稍后再试。", None)

    logger.bind(tag=TAG).info(f"baby_care_get_state: {str(data)[:300]}")
    state_text = str(data)
    for bridge_name, display in BRIDGE_TO_NAME.items():
        state_text = state_text.replace(bridge_name, display)
    report = (
        f"根据下列数据用{lang}回应用户的喂养状态查询（把时间转成自然语言，如'两小时前'）：\n\n"
        f"{state_text}\n\n(根据用户具体问题聚焦回答，例如问'该喂了吗'就对比下次时间和现在；宝宝小名：哥哥咖啡、弟弟花生)"
    )
    return ActionResponse(Action.REQLLM, report, None)
