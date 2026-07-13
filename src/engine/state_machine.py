"""盈亏状态机 — 整个系统唯一不许出错的部分。

纯函数:输入(持仓快照 + 阈值配置 + 今天日期)→ 输出 StateResult。
不做任何 IO,pytest 全覆盖(tests/test_state_machine.py)。

多条规则同时命中时全部记入 flags,severity 最高者为主状态;
同级冲突按追加顺序取先者:
BREACHED > ROLL_WINDOW > EVENT_RISK > TESTED > OPTION_LOSS > EXPIRING
> PROFIT_TAKE > MANAGE_DTE。

Greeks 缺失(降级数据源如 SnapTrade)时,delta 类规则自动跳过,
价格类规则(BREACHED/TESTED-距离/OPTION_LOSS/时间类)照常工作。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from src.models import (
    SEVERITY,
    AlertConfig,
    Position,
    PositionState,
    StateResult,
)


def evaluate(pos: Position, cfg: AlertConfig, today: date) -> StateResult:
    s, c, m = pos.stock, pos.call, pos.metrics

    if c is None:
        return StateResult(
            PositionState.UNCOVERED,
            [PositionState.UNCOVERED],
            [f"持股 {int(s.qty)} 股,无 covered call,晨报将给出开仓候选"],
        )

    matched: list[tuple[PositionState, str]] = []

    # 🔴 已击穿:股票暴涨越过 strike
    if s.price > c.strike:
        matched.append((
            PositionState.BREACHED,
            f"现价 {s.price:.2f} 已越过 strike {c.strike:g}",
        ))

    # 🟠 roll 窗口:delta 高企且时间合适
    if (
        c.delta is not None
        and c.delta >= cfg.roll_delta
        and m.dte is not None
        and cfg.roll_dte_min <= m.dte <= cfg.roll_dte_max
    ):
        matched.append((
            PositionState.ROLL_WINDOW,
            f"delta {c.delta:.2f} ≥ {cfg.roll_delta:g} 且 DTE {m.dte} 处于 roll 窗口"
            f" [{cfg.roll_dte_min}, {cfg.roll_dte_max}]",
        ))

    # 🟠 事件风险:横跨财报 / 除息前高 delta(提前 event_warn_days 天告警)
    ev = pos.events
    if (
        ev.earnings is not None
        and today <= ev.earnings <= c.expiry
        and (ev.earnings - today).days <= cfg.event_warn_days
    ):
        matched.append((
            PositionState.EVENT_RISK,
            f"到期日 {c.expiry.isoformat()} 横跨财报日 {ev.earnings.isoformat()}",
        ))
    if (
        ev.ex_div is not None
        and today <= ev.ex_div <= c.expiry
        and c.delta is not None
        and c.delta >= cfg.exdiv_delta
        and (ev.ex_div - today).days <= cfg.event_warn_days
    ):
        matched.append((
            PositionState.EVENT_RISK,
            f"除息日 {ev.ex_div.isoformat()} 前 delta {c.delta:.2f} ≥ {cfg.exdiv_delta:g},"
            f"存在提前指派风险",
        ))

    # 🟡 快被击穿防线:距 strike 很近或 delta 偏高
    tested_by_distance = (
        m.distance_to_strike_pct is not None
        and m.distance_to_strike_pct <= cfg.tested_distance_pct
    )
    tested_by_delta = c.delta is not None and c.delta >= cfg.tested_delta
    if tested_by_distance or tested_by_delta:
        parts = []
        if tested_by_distance:
            parts.append(f"距 strike 仅 {m.distance_to_strike_pct:.1%}")
        if tested_by_delta:
            parts.append(f"delta {c.delta:.2f} ≥ {cfg.tested_delta:g}")
        matched.append((PositionState.TESTED, ",".join(parts)))

    # 🟡 call 腿亏损止损线:买回成本已达开仓权利金的 N 倍
    if c.open_premium > 0 and c.mid >= cfg.option_loss_multiple * c.open_premium:
        matched.append((
            PositionState.OPTION_LOSS,
            f"call 腿买回成本 {c.mid:.2f} 已达开仓权利金 {c.open_premium:.2f} 的"
            f" {c.mid / c.open_premium:.1f} 倍(止损线 {cfg.option_loss_multiple:g}x)",
        ))

    # 🟡 临近到期
    if m.dte is not None and m.dte <= cfg.expiring_dte:
        matched.append((
            PositionState.EXPIRING,
            f"DTE {m.dte} ≤ {cfg.expiring_dte},临近到期需处理",
        ))

    # 🟢 可止盈
    if m.pct_max_profit is not None and m.pct_max_profit >= cfg.profit_take_pct:
        matched.append((
            PositionState.PROFIT_TAKE,
            f"已实现最大利润的 {m.pct_max_profit:.0%},可平仓锁定并开下一轮",
        ))

    # 🟡 管理窗口:21 DTE 纪律(即使 OTM 也该收尾,躲 gamma 风险)
    if m.dte is not None and m.dte <= cfg.manage_dte:
        matched.append((
            PositionState.MANAGE_DTE,
            f"DTE {m.dte} ≤ {cfg.manage_dte},进入管理窗口(止盈或滚动开下一轮)",
        ))

    if not matched:
        return StateResult(PositionState.ON_TRACK, [PositionState.ON_TRACK], [])

    primary = max(matched, key=lambda t: SEVERITY[t[0]])[0]
    flags = [st for st, _ in matched]
    reasons = [r for _, r in matched]
    return StateResult(primary, flags, reasons)


def coverage_shortfalls(positions: list[Position]) -> list[tuple[str, str, str]]:
    """跨腿聚合的覆盖完整性检查:同(账户, 标的)的空头 call 总张数 × 100
    不得超过正股股数,超出即裸卖敞口(理论无限风险,必须最高级别告警)。

    多条腿的 Position 共享同一份正股快照,股数取组内最大值防止重复相加。
    返回 (account, ticker, 描述) 列表,空列表 = 全覆盖。纯函数。
    """
    groups: dict[tuple[str, str], dict] = {}
    for pos in positions:
        if pos.call is None:
            continue
        g = groups.setdefault((pos.account, pos.ticker), {"contracts": 0, "qty": 0.0})
        g["contracts"] += pos.call.contracts
        g["qty"] = max(g["qty"], pos.stock.qty)

    out: list[tuple[str, str, str]] = []
    for (account, ticker), g in sorted(groups.items()):
        need = g["contracts"] * 100
        if need > g["qty"]:
            acct_txt = account or "主账户"
            out.append((account, ticker, (
                f"{ticker}({acct_txt})空头 call 共 {g['contracts']} 张需 {need} 股,"
                f"实持 {int(g['qty'])} 股,缺口 {int(need - g['qty'])} 股 — 裸卖敞口"
            )))
    return out


def should_notify(
    prev_state: Optional[PositionState],
    result: StateResult,
    last_notified_on: Optional[date],
    today: date,
) -> bool:
    """只在状态跃迁时推送,避免轰炸;两个例外:

    - EXPIRING 每日提醒一次(状态没变也推)
    - 从 severity>=2 回落到正常时推一条"解除"通知
    """
    if prev_state != result.state:
        if result.severity > 0:
            return True
        if prev_state is not None and SEVERITY.get(prev_state, 0) >= 2:
            return True
        return False
    if result.state == PositionState.EXPIRING and last_notified_on != today:
        return True
    return False
