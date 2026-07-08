"""期权腿/组合盈亏的确定性计算 — 纯函数,只依赖标准库。

所有金额为每股口径,除非函数名注明总额(combined_pnl)。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from src.engine.qcc import days_held, days_to_long_term, is_long_term
from src.models import Metrics, Position


def option_pnl_per_share(open_premium: float, current_mid: float) -> float:
    """short call 的每股浮盈:收到的权利金 - 当前买回成本。"""
    return open_premium - current_mid


def pct_max_profit(open_premium: float, current_mid: float) -> Optional[float]:
    """已实现最大利润比例;权利金为 0 或负时无意义,返回 None。"""
    if open_premium <= 0:
        return None
    return (open_premium - current_mid) / open_premium


def breakeven(avg_cost: float, open_premium: float) -> float:
    return avg_cost - open_premium


def distance_to_strike_pct(strike: float, price: float) -> Optional[float]:
    """(strike - 现价) / 现价;负值表示已越过 strike。"""
    if price <= 0:
        return None
    return (strike - price) / price


def days_to_expiry(expiry: date, today: date) -> int:
    return (expiry - today).days


def combined_pnl(
    qty: float,
    avg_cost: float,
    price: float,
    contracts: int,
    open_premium: float,
    current_mid: float,
) -> float:
    """正股浮盈亏 + 期权腿浮盈(总额)。"""
    return qty * (price - avg_cost) + contracts * 100 * (open_premium - current_mid)


def annualized_premium_pct(premium: float, price: float, days: int) -> Optional[float]:
    if days <= 0 or price <= 0:
        return None
    return premium / price * 365 / days


def compute_metrics(pos: Position, today: date) -> Metrics:
    """由持仓快照计算全部派生指标。状态机只消费这里的输出。"""
    m = Metrics()
    s = pos.stock
    if s.acquired_date:
        m.days_held = days_held(s.acquired_date, today)
        m.is_long_term = is_long_term(s.acquired_date, today)
        m.days_to_long_term = days_to_long_term(s.acquired_date, today)

    c = pos.call
    if c is None:
        m.combined_pnl = s.qty * (s.price - s.avg_cost)
        return m

    m.dte = days_to_expiry(c.expiry, today)
    m.option_pnl_per_share = option_pnl_per_share(c.open_premium, c.mid)
    m.pct_max_profit = pct_max_profit(c.open_premium, c.mid)
    m.distance_to_strike_pct = distance_to_strike_pct(c.strike, s.price)
    m.breakeven = breakeven(s.avg_cost, c.open_premium)
    m.combined_pnl = combined_pnl(
        s.qty, s.avg_cost, s.price, c.contracts, c.open_premium, c.mid
    )
    if c.open_date:
        term_days = max((c.expiry - c.open_date).days, 1)
        m.annualized_premium_pct = annualized_premium_pct(c.open_premium, s.price, term_days)
    return m
