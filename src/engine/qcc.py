"""QCC(Qualified Covered Call)合规 + 持有期税务计算 — 纯函数。

注意:这里实现的是简化版内部纪律(OTM + 开仓 DTE > 30)。
完整 IRS 定义(IRC §1092(c)(4),含深度价内 LQSP 判定)比这严格,
但对"只卖 OTM、30 天以上"的打法,简化规则是充分条件。
本系统输出不构成税务建议,不作报税依据。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from src.models import QccConfig

# 长期资本利得要求持有超过 1 年(> 365 天)
LONG_TERM_DAYS = 365


def is_otm(strike: float, price: float) -> bool:
    return strike > price


def qualifies(strike: float, price: float, open_dte: int, cfg: QccConfig) -> tuple[bool, list[str]]:
    """开仓合规检查。返回 (是否合规, 不合规原因列表)。"""
    problems: list[str] = []
    if not is_otm(strike, price):
        problems.append(
            f"strike {strike:g} 不是 OTM(现价 {price:g})— ITM call 会暂停/清零持有期"
        )
    if open_dte < cfg.min_open_dte:
        problems.append(f"开仓 DTE {open_dte} < {cfg.min_open_dte},不满足 QCC 的 >30 天要求")
    return (not problems, problems)


def crosses_event(expiry: date, event_date: Optional[date], today: date) -> bool:
    """到期日是否横跨某事件日(财报/除息)。"""
    return event_date is not None and today <= event_date <= expiry


def days_held(acquired: date, today: date) -> int:
    return (today - acquired).days


def is_long_term(acquired: date, today: date) -> bool:
    return days_held(acquired, today) > LONG_TERM_DAYS


def days_to_long_term(acquired: date, today: date) -> int:
    """距离满足长期资本利得还差几天;已满足则为 0。"""
    return max(0, LONG_TERM_DAYS + 1 - days_held(acquired, today))
