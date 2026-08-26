"""baby_care 提醒检查 — 设备唤醒时查询喂养状态，注入提醒上下文。

流程：
1. 设备连接（hello）→ fetch_baby_care_reminder() 查 baby-care-bridge /state
2. 有过期/到期的主体 → conn.pending_reminder = 提醒文本
3. 用户第一句对话（chat depth==0）→ 注入 system 消息 → LLM 回复开头自然播报提醒
"""
import time

import httpx
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

BABY_CARE_BASE = "http://127.0.0.1:8000"


def _fmt_gap(seconds: float) -> str:
    """秒数 → 'X小时Y分钟' 自然语言"""
    m = int(seconds // 60)
    if m < 1:
        return "不到1分钟"
    if m < 60:
        return f"{m}分钟"
    h, m2 = divmod(m, 60)
    return f"{h}小时{m2}分钟" if m2 else f"{h}小时"


async def fetch_baby_care_reminder(conn) -> None:
    """查询喂养状态，若有到期/过期主体则把提醒文本挂到 conn.pending_reminder。"""
    conn.pending_reminder = ""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=2.0)) as c:
            resp = await c.get(f"{BABY_CARE_BASE}/state")
            data = resp.json()
    except Exception as e:
        # baby-care-bridge 不在线不算错误（静默跳过）
        logger.bind(tag=TAG).debug(f"baby-care state 不可用: {e}")
        return

    now = time.time()
    overdue = []
    for s in data.get("subjects", []):
        name = s.get("name", "")
        next_due = s.get("next_due")
        last_ts = s.get("last_record_ts")
        if not next_due or not last_ts:
            continue
        overdue_sec = now - next_due
        if overdue_sec > 0:
            overdue.append(
                f"- {name}：距上次喂养已过 {_fmt_gap(now - last_ts)}，"
                f"已超过喂养间隔 {_fmt_gap(overdue_sec)}，应尽快喂养"
            )

    if not overdue:
        return

    conn.pending_reminder = (
        "[系统提醒] 以下是育儿喂养提醒，请在本次回复的开头自然地向用户播报"
        "（用自己的话说，不要逐字朗读，不要使用列表格式），然后正常回应用户的问题：\n"
        + "\n".join(overdue)
        + "\n如果用户这句话正是在记录喂养（如'喂了'），则正常记录，不必再提醒。"
    )
    logger.bind(tag=TAG).info(f"唤醒提醒已挂载: {conn.pending_reminder[:120]}")
