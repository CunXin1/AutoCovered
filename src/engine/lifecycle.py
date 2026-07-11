"""持仓生命周期推断 — 纯函数,账本(ledger)的事件来源之一。

对账分工(主源是 executions,不是持仓 diff):
- IBKR executions(watcher 每周期拉取)→ 精确成交(开/平、每股价、佣金)
  → events_from_executions()
- 持仓 diff(diff_positions)只负责 executions 覆盖不到的事实:
  到期(EXPIRE)、指派(ASSIGN)、停机窗口内的手动交易(inferred)
- 两路事件都进 Ledger.apply(),以 exec_id 做幂等去重,crash 重放 = no-op

安全设计:
- 熔断:非 ibkr 主源快照 / 数据源切换周期 / 无前置快照 / 大量无解释消失
  → 本轮跳过全部 diff 合成事件(executions 入账不受影响)
- 指派优先于部分买回:ticker 级股数下降先按 ITM 程度(strike 升序)分摊
  给消失/减少的 call,剩余才走买回推断
- 到期日判定用官方收盘价(watcher 预取传入),strike±0.5% 灰区 → unknown
  请用户确认;到期时点的期权腿现金流恒为 0,故 unknown 不污染金额
- 推断价格一律 price_quality=inferred,统计层强制分层展示

已知限制(文档化而非静默):卖光正股但保留空头 call 时,IBKR 快照会同时
丢失股票与 call 腿,diff 会误判为指派 — 该操作超出 covered call 纪律范围。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from src.models import Position, ShortCall

PRIMARY_SOURCE = "ibkr_gateway"

# trades.action
SELL_TO_OPEN = "SELL_TO_OPEN"
BUY_TO_CLOSE = "BUY_TO_CLOSE"
EXPIRE = "EXPIRE"
ASSIGN = "ASSIGN"
CLOSE_ACTIONS = {BUY_TO_CLOSE, EXPIRE, ASSIGN}

# rounds.outcome
OUT_OPEN = "open"
OUT_EXPIRED = "expired"
OUT_ASSIGNED = "assigned"
OUT_BOUGHT_BACK = "bought_back"
OUT_ROLLED = "rolled"
OUT_UNKNOWN = "unknown"

DEFAULT_OUTCOME = {EXPIRE: OUT_EXPIRED, ASSIGN: OUT_ASSIGNED, BUY_TO_CLOSE: OUT_BOUGHT_BACK}

# 到期收盘价相对 strike 的灰区(±0.5%):区间内不猜 expired/assigned
EXPIRY_GRAY_BAND = 0.005


@dataclass(frozen=True)
class CallKey:
    ticker: str
    strike: float
    expiry: date

    def __str__(self) -> str:
        return f"{self.ticker}:{self.strike:g}:{self.expiry.isoformat()}"


@dataclass
class ExecutionRecord:
    """IBKR 成交回报(brokers 层转换后)。price=每股,fees=总额。"""

    exec_id: str
    ts: str                  # UTC ISO
    ticker: str
    action: str              # SELL_TO_OPEN | BUY_TO_CLOSE(covered call 账户按 side 映射)
    strike: float
    expiry: date
    contracts: int
    price: float
    fees: float = 0.0
    order_ref: str = ""      # 本系统单 = proposal id;手动 TWS 单为空

    @property
    def key(self) -> CallKey:
        return CallKey(self.ticker, self.strike, self.expiry)


@dataclass
class TradeEvent:
    """一条应写入账本的交易行(来自 execution 或 diff 合成)。"""

    exec_id: str             # execId 或 synthetic:…(幂等键)
    ts: str                  # UTC ISO;到期/指派类为经济事件发生日(expiry)
    ticker: str
    action: str              # SELL_TO_OPEN|BUY_TO_CLOSE|EXPIRE|ASSIGN
    strike: float
    expiry: date
    contracts: int
    price: float             # 每股;EXPIRE/ASSIGN 恒 0
    fees: float = 0.0
    source: str = "inferred"        # executor|manual_tws|inferred|backfill
    price_quality: str = "inferred"  # exact|inferred|user_confirmed|backfill
    proposal_id: str = ""
    note: str = ""
    outcome: str = ""        # 平仓事件的 round outcome;空 = 按 action 默认
    rolled_from: Optional[CallKey] = None  # 开仓事件:roll 链上游合约
    needs_confirm: bool = False
    aux_price: Optional[float] = None      # ASSIGN:到期收盘价(算 upside_forgone 用)

    @property
    def key(self) -> CallKey:
        return CallKey(self.ticker, self.strike, self.expiry)


@dataclass
class DiffResult:
    events: list[TradeEvent] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)   # 只推送不入账的提醒
    skip_reason: str = ""    # 非空 = 本轮 diff 被熔断,events 为空


# ---------------------------------------------------------------- executions → 事件


def events_from_executions(
    execs: list[ExecutionRecord],
    proposal_kinds: Optional[dict[str, str]] = None,   # proposal_id -> kind
    roll_old_keys: Optional[dict[str, CallKey]] = None,  # ROLL 提案 id -> 旧腿 key
) -> list[TradeEvent]:
    """成交回报直转账本事件:价格/佣金精确,orderRef 归因提案。"""
    proposal_kinds = proposal_kinds or {}
    roll_old_keys = roll_old_keys or {}
    out: list[TradeEvent] = []
    for e in execs:
        kind = proposal_kinds.get(e.order_ref, "")
        is_roll = kind == "ROLL"
        out.append(TradeEvent(
            exec_id=e.exec_id,
            ts=e.ts,
            ticker=e.ticker,
            action=e.action,
            strike=e.strike,
            expiry=e.expiry,
            contracts=e.contracts,
            price=round(e.price, 4),
            fees=round(e.fees, 4),
            source="executor" if e.order_ref else "manual_tws",
            price_quality="exact",
            proposal_id=e.order_ref,
            outcome=OUT_ROLLED if (is_roll and e.action == BUY_TO_CLOSE) else "",
            rolled_from=roll_old_keys.get(e.order_ref) if (
                is_roll and e.action == SELL_TO_OPEN) else None,
        ))
    return out


# ---------------------------------------------------------------- 持仓 diff


def _call_maps(positions: list[Position]) -> tuple[dict[CallKey, ShortCall], dict[str, float]]:
    """(key→空头腿, ticker→股数)。同 ticker 多腿共享同一份 stock,取首见。"""
    calls: dict[CallKey, ShortCall] = {}
    qty: dict[str, float] = {}
    for p in positions:
        qty.setdefault(p.ticker, p.stock.qty)
        if p.call is not None:
            k = CallKey(p.ticker, p.call.strike, p.call.expiry)
            if k in calls:
                # 同合约多条目(理论上不该发生):张数合并,防重复计数
                calls[k].contracts += p.call.contracts
            else:
                calls[k] = ShortCall(**{**p.call.__dict__})
    return calls, qty


def _expiry_eod_iso(d: date) -> str:
    """到期/指派类事件的经济时间戳:到期日收盘(约 20:00 UTC)。"""
    return f"{d.isoformat()}T20:00:00+00:00"


def diff_positions(
    *,
    prev: list[Position],
    curr: list[Position],
    prev_source: str,
    curr_source: str,
    executions: list[ExecutionRecord],
    expiry_closes: dict[tuple[str, date], float],   # (ticker, expiry) -> 官方收盘价

    approved_rolls: Optional[list[tuple[str, CallKey, CallKey]]] = None,
    today: Optional[date] = None,
    now_iso: str = "",
    glitch_abs: int = 2,
    glitch_pct: float = 0.30,
) -> DiffResult:
    """对比前后两轮快照,产出 executions 覆盖不到的账本事件。

    approved_rolls:(proposal_id, 旧腿 key, 新腿 key)列表 — 决策支持模式下
    用户批准但系统未执行(去券商 App 手动做)的 ROLL 提案,用于手动 roll 配对。
    """
    today = today or date.today()
    approved_rolls = approved_rolls or []
    res = DiffResult()

    # ---- 熔断闸门
    if curr_source != PRIMARY_SOURCE:
        return DiffResult(skip_reason=f"非主源快照({curr_source}),跳过账本 diff")
    if prev_source and prev_source != curr_source:
        return DiffResult(skip_reason=f"数据源切换周期({prev_source}→{curr_source}),跳过")
    if not prev:
        return DiffResult(skip_reason="无前置快照,跳过 diff(executions 照常入账)")

    prev_calls, prev_qty = _call_maps(prev)
    curr_calls, curr_qty = _call_maps(curr)

    # 成交证据:key → 各方向张数
    closed_by_exec: dict[CallKey, int] = {}
    opened_by_exec: dict[CallKey, int] = {}
    for e in executions:
        if e.action == BUY_TO_CLOSE:
            closed_by_exec[e.key] = closed_by_exec.get(e.key, 0) + e.contracts
        elif e.action == SELL_TO_OPEN:
            opened_by_exec[e.key] = opened_by_exec.get(e.key, 0) + e.contracts

    # ---- 消失/减少的腿(扣除 exec 已解释的部分)
    pending_close: dict[CallKey, int] = {}   # key -> 待解释张数
    for k, c in prev_calls.items():
        cur = curr_calls.get(k)
        gone = c.contracts - (cur.contracts if cur else 0)
        if gone <= 0:
            continue
        gone -= min(gone, closed_by_exec.get(k, 0))   # execution 已精确入账
        if gone > 0:
            pending_close[k] = gone

    # ---- 指派分摊:ticker 级股数下降,ITM 优先(strike 升序)
    assigns: dict[CallKey, int] = {}
    for ticker in {k.ticker for k in pending_close}:
        drop = prev_qty.get(ticker, 0.0) - curr_qty.get(ticker, 0.0)
        budget = int(drop // 100) if drop > 0 else 0
        if budget <= 0:
            continue
        for k in sorted((k for k in pending_close if k.ticker == ticker),
                        key=lambda k: k.strike):
            if budget <= 0:
                break
            n = min(pending_close[k], budget)
            assigns[k] = n
            pending_close[k] -= n
            budget -= n

    # ---- 毛刺熔断:分摊/到期都解释不了的消失过多 → 判坏快照
    unexplained = [k for k, n in pending_close.items() if n > 0 and k.expiry >= today]
    threshold = max(glitch_abs, math.ceil(glitch_pct * max(len(prev_calls), 1)))
    if len(unexplained) > threshold:
        return DiffResult(skip_reason=(
            f"疑似坏快照:{len(unexplained)} 条空头腿无解释消失"
            f"(阈值 {threshold}),本轮 diff 跳过"))

    # ---- 出现/增加的腿(扣除 exec 已解释的部分)
    pending_open: dict[CallKey, int] = {}
    for k, c in curr_calls.items():
        prv = prev_calls.get(k)
        added = c.contracts - (prv.contracts if prv else 0)
        if added <= 0:
            continue
        added -= min(added, opened_by_exec.get(k, 0))
        if added > 0:
            pending_open[k] = added

    # ---- 生成指派事件(期权腿现金流为 0,金额精确)
    for k, n in assigns.items():
        at_expiry = k.expiry <= today
        res.events.append(TradeEvent(
            exec_id=(f"synthetic:ASSIGN:{k}" if at_expiry
                     else f"synthetic:ASSIGN:{k}:{today.isoformat()}"),
            ts=_expiry_eod_iso(k.expiry) if at_expiry else now_iso,
            ticker=k.ticker, action=ASSIGN, strike=k.strike, expiry=k.expiry,
            contracts=n, price=0.0,
            source="inferred", price_quality="exact",
            outcome=OUT_ASSIGNED,
            note="股数下降推断为指派" + ("" if at_expiry else "(提前指派)"),
            aux_price=expiry_closes.get((k.ticker, k.expiry)),
        ))

    # ---- 剩余消失:到期判定 / 买回推断
    inferred_closes: dict[str, TradeEvent] = {}   # ticker → 事件(供手动 roll 配对)
    for k, n in pending_close.items():
        if n <= 0:
            continue
        if k.expiry <= today:
            close = expiry_closes.get((k.ticker, k.expiry))
            if close is None:
                outcome, confirm = OUT_UNKNOWN, True
                note = "到期但无官方收盘价,无法判定 expired/assigned,请确认"
            elif close < k.strike * (1 - EXPIRY_GRAY_BAND):
                outcome, confirm = OUT_EXPIRED, False
                note = f"到期收盘 {close:.2f} < strike,作废"
            elif close > k.strike * (1 + EXPIRY_GRAY_BAND):
                outcome, confirm = OUT_UNKNOWN, True
                note = f"到期收盘 {close:.2f} ITM 但股数未减,矛盾,请确认"
            else:
                outcome, confirm = OUT_UNKNOWN, True
                note = f"到期收盘 {close:.2f} 贴着 strike(pin risk),请确认"
            res.events.append(TradeEvent(
                exec_id=f"synthetic:EXPIRE:{k}",
                ts=_expiry_eod_iso(k.expiry),
                ticker=k.ticker, action=EXPIRE, strike=k.strike, expiry=k.expiry,
                contracts=n, price=0.0,
                source="inferred", price_quality="exact",   # 到期现金流恒 0
                outcome=outcome, note=note, needs_confirm=confirm,
            ))
        else:
            prev_call = prev_calls[k]
            ev = TradeEvent(
                exec_id=f"synthetic:BTC:{k}:{today.isoformat()}",
                ts=now_iso,
                ticker=k.ticker, action=BUY_TO_CLOSE, strike=k.strike, expiry=k.expiry,
                contracts=n, price=round(prev_call.mid, 4),
                source="inferred", price_quality="inferred",
                outcome=OUT_BOUGHT_BACK,
                note="仓位消失且当日无成交记录,买回价取最后已知 mid",
                needs_confirm=True,
            )
            res.events.append(ev)
            inferred_closes.setdefault(k.ticker, ev)

    # ---- 出现:开仓推断(premium 取 IBKR averageCost,净佣金,较准)
    inferred_opens: dict[str, TradeEvent] = {}
    for k, n in pending_open.items():
        c = curr_calls[k]
        blended = k in prev_calls   # 已有同合约仓位,averageCost 是混合均价
        ev = TradeEvent(
            exec_id=f"synthetic:STO:{k}:{today.isoformat()}",
            ts=now_iso,
            ticker=k.ticker, action=SELL_TO_OPEN, strike=k.strike, expiry=k.expiry,
            contracts=n, price=round(c.open_premium, 4),
            source="inferred", price_quality="inferred",
            note=("同合约加仓,averageCost 为混合均价,建议 CONFIRM 修正" if blended
                  else "premium 取自 IBKR averageCost(净佣金)"),
            needs_confirm=blended,
        )
        res.events.append(ev)
        inferred_opens.setdefault(k.ticker, ev)

    # ---- 手动 roll 配对:同周期同 ticker 一平一开
    for ticker, close_ev in inferred_closes.items():
        open_ev = inferred_opens.get(ticker)
        if open_ev is None:
            continue
        matched = next((pid for pid, old, new in approved_rolls
                        if old == close_ev.key and new == open_ev.key), None)
        close_ev.outcome = OUT_ROLLED
        open_ev.rolled_from = close_ev.key
        if matched:
            close_ev.proposal_id = matched
            open_ev.proposal_id = matched
            close_ev.needs_confirm = False   # 用户批准过这笔 roll(决策支持模式手动执行)
            close_ev.note = f"匹配已批准提案 {matched}(手动执行的 roll)"
            open_ev.note = f"匹配已批准提案 {matched}(手动执行的 roll)"
        else:
            close_ev.note = "形态推断为手动 roll(同周期一平一开)"
            open_ev.note = "形态推断为手动 roll(同周期一平一开)"
            close_ev.needs_confirm = True

    # ---- 同合约同张数但 averageCost 突变:疑似周期内平仓重开(仅提醒)
    for k, c in curr_calls.items():
        prv = prev_calls.get(k)
        if (prv and prv.contracts == c.contracts
                and abs(c.open_premium - prv.open_premium) > 0.05
                and closed_by_exec.get(k, 0) == 0 and opened_by_exec.get(k, 0) == 0):
            res.notices.append(
                f"⚠️ {k} averageCost 突变 {prv.open_premium:.2f}→{c.open_premium:.2f} "
                f"且无成交记录,疑似周期内平仓重开,请核对账本")

    return res


# ---------------------------------------------------------------- backfill


def backfill_events(positions: list[Position], now_iso: str) -> list[TradeEvent]:
    """账本首次启动:把现有空头腿按 averageCost 补录(开仓日期不可考)。"""
    calls, _ = _call_maps(positions)
    return [TradeEvent(
        exec_id=f"synthetic:backfill:{k}",
        ts=now_iso,
        ticker=k.ticker, action=SELL_TO_OPEN, strike=k.strike, expiry=k.expiry,
        contracts=c.contracts, price=round(c.open_premium, 4),
        source="backfill", price_quality="backfill",
        note="账本初始化补录,premium 取 IBKR averageCost,真实开仓日期不可考",
    ) for k, c in calls.items()]
