"""生命周期推断(diff_positions / events_from_executions / backfill)全分支测试。"""
from datetime import date

from src.engine.lifecycle import (
    ASSIGN,
    BUY_TO_CLOSE,
    EXPIRE,
    OUT_ASSIGNED,
    OUT_BOUGHT_BACK,
    OUT_EXPIRED,
    OUT_ROLLED,
    OUT_UNKNOWN,
    SELL_TO_OPEN,
    CallKey,
    ExecutionRecord,
    backfill_events,
    diff_positions,
    events_from_executions,
)
from src.models import Position, ShortCall, StockHolding

TODAY = date(2026, 7, 10)
EXP_PAST = date(2026, 7, 2)     # 已到期
EXP_FUT = date(2026, 8, 21)     # 未到期
EXP_FUT2 = date(2026, 9, 18)
NOW = "2026-07-10T18:00:00+00:00"
SRC = "ibkr_gateway"


def pos(ticker, qty, strike=None, expiry=None, contracts=1, premium=2.5, mid=1.2):
    call = None
    if strike is not None:
        call = ShortCall(strike=strike, expiry=expiry, contracts=contracts,
                         open_premium=premium, mid=mid)
    return Position(ticker=ticker,
                    stock=StockHolding(qty=qty, avg_cost=100.0, price=150.0),
                    call=call)


def run_diff(prev, curr, *, executions=None, closes=None, approved=None,
             prev_source=SRC, curr_source=SRC):
    return diff_positions(
        prev=prev, curr=curr, prev_source=prev_source, curr_source=curr_source,
        executions=executions or [], expiry_closes=closes or {},
        approved_rolls=approved or [], today=TODAY, now_iso=NOW)


# ---------------------------------------------------------------- 熔断

def test_fuse_non_primary_source():
    r = run_diff([pos("NVDA", 200)], [pos("NVDA", 200)], curr_source="snaptrade")
    assert r.skip_reason and not r.events


def test_fuse_source_switch():
    r = run_diff([pos("NVDA", 200)], [pos("NVDA", 200)], prev_source="snaptrade")
    assert "切换" in r.skip_reason


def test_fuse_no_prev_snapshot():
    r = run_diff([], [pos("NVDA", 200, 180.0, EXP_FUT)])
    assert "无前置快照" in r.skip_reason


def test_fuse_mass_unexplained_disappearance():
    prev = [pos(t, 200, 150.0, EXP_FUT) for t in ("AAAA", "BBBB", "CCCC", "DDDD")]
    curr = [pos(t, 200) for t in ("AAAA", "BBBB", "CCCC", "DDDD")]  # 全消失,股数没动
    r = run_diff(prev, curr)
    assert "坏快照" in r.skip_reason and not r.events


def test_mass_disappearance_by_expiry_is_fine():
    """同一到期日多条腿集体作废是正常形态,不触发熔断。"""
    prev = [pos(t, 200, 150.0, EXP_PAST) for t in ("AAAA", "BBBB", "CCCC", "DDDD")]
    curr = [pos(t, 200) for t in ("AAAA", "BBBB", "CCCC", "DDDD")]
    closes = {(t, EXP_PAST): 140.0 for t in ("AAAA", "BBBB", "CCCC", "DDDD")}
    r = run_diff(prev, curr, closes=closes)
    assert not r.skip_reason
    assert len(r.events) == 4
    assert all(e.action == EXPIRE and e.outcome == OUT_EXPIRED for e in r.events)


# ---------------------------------------------------------------- 到期判定

def test_expired_otm():
    prev = [pos("NVDA", 200, 180.0, EXP_PAST)]
    curr = [pos("NVDA", 200)]
    r = run_diff(prev, curr, closes={("NVDA", EXP_PAST): 170.0})
    [e] = r.events
    assert e.action == EXPIRE and e.outcome == OUT_EXPIRED
    assert e.price == 0.0 and e.price_quality == "exact"
    assert e.ts.startswith(EXP_PAST.isoformat())   # 经济事件日,不是检测日
    assert not e.needs_confirm


def test_expired_gray_zone_unknown():
    prev = [pos("NVDA", 200, 180.0, EXP_PAST)]
    r = run_diff(prev, [pos("NVDA", 200)], closes={("NVDA", EXP_PAST): 180.2})
    [e] = r.events
    assert e.outcome == OUT_UNKNOWN and e.needs_confirm


def test_expired_itm_but_stock_intact_contradiction():
    prev = [pos("NVDA", 200, 180.0, EXP_PAST)]
    r = run_diff(prev, [pos("NVDA", 200)], closes={("NVDA", EXP_PAST): 195.0})
    [e] = r.events
    assert e.outcome == OUT_UNKNOWN and e.needs_confirm and "矛盾" in e.note


def test_expired_no_close_data_unknown():
    prev = [pos("NVDA", 200, 180.0, EXP_PAST)]
    r = run_diff(prev, [pos("NVDA", 200)])
    [e] = r.events
    assert e.outcome == OUT_UNKNOWN and e.needs_confirm


# ---------------------------------------------------------------- 指派

def test_assigned_at_expiry():
    prev = [pos("NVDA", 200, 180.0, EXP_PAST, contracts=2)]
    curr = [pos("NVDA", 0)] if False else []   # 全部叫走,散股条目消失
    r = run_diff(prev, curr, closes={("NVDA", EXP_PAST): 195.0})
    [e] = r.events
    assert e.action == ASSIGN and e.outcome == OUT_ASSIGNED and e.contracts == 2
    assert e.aux_price == 195.0
    assert e.ts.startswith(EXP_PAST.isoformat())


def test_partial_early_assignment_beats_partial_buyback():
    """3 张中 1 张被提前指派(股数 -100):判 ASSIGN,不是部分买回。"""
    prev = [pos("NVDA", 300, 150.0, EXP_FUT, contracts=3)]
    curr = [pos("NVDA", 200, 150.0, EXP_FUT, contracts=2)]
    r = run_diff(prev, curr)
    [e] = r.events
    assert e.action == ASSIGN and e.contracts == 1
    assert "提前指派" in e.note
    assert e.exec_id.endswith(TODAY.isoformat())   # 提前指派带检测日,跨日不误去重


def test_assignment_allocated_itm_first():
    """股数只掉 100,两条腿消失:分摊给更 ITM(低 strike)的那条。"""
    prev = [pos("NVDA", 200, 140.0, EXP_FUT), pos("NVDA", 200, 180.0, EXP_FUT2)]
    prev[1].stock = prev[0].stock
    curr = [pos("NVDA", 100)]
    r = run_diff(prev, curr)
    assigns = [e for e in r.events if e.action == ASSIGN]
    btcs = [e for e in r.events if e.action == BUY_TO_CLOSE]
    assert len(assigns) == 1 and assigns[0].strike == 140.0
    assert len(btcs) == 1 and btcs[0].strike == 180.0   # 另一条走买回推断


# ---------------------------------------------------------------- 买回/开仓推断与成交证据

def test_inferred_buyback_needs_confirm():
    prev = [pos("NVDA", 200, 180.0, EXP_FUT, mid=1.35)]
    r = run_diff(prev, [pos("NVDA", 200)])
    [e] = r.events
    assert e.action == BUY_TO_CLOSE and e.outcome == OUT_BOUGHT_BACK
    assert e.price == 1.35 and e.price_quality == "inferred" and e.needs_confirm


def test_execution_evidence_suppresses_synthetic_close():
    prev = [pos("NVDA", 200, 180.0, EXP_FUT)]
    execs = [ExecutionRecord(
        exec_id="0001.abc", ts=NOW, ticker="NVDA", action=BUY_TO_CLOSE,
        strike=180.0, expiry=EXP_FUT, contracts=1, price=1.30, fees=0.66)]
    r = run_diff(prev, [pos("NVDA", 200)], executions=execs)
    assert r.events == []   # execution 行由 events_from_executions 入账,diff 不重复


def test_inferred_open_from_average_cost():
    curr = [pos("NVDA", 200, 190.0, EXP_FUT, premium=3.1)]
    r = run_diff([pos("NVDA", 200)], curr)
    [e] = r.events
    assert e.action == SELL_TO_OPEN and e.price == 3.1
    assert e.price_quality == "inferred" and not e.needs_confirm


def test_execution_evidence_suppresses_synthetic_open():
    execs = [ExecutionRecord(
        exec_id="0002.def", ts=NOW, ticker="NVDA", action=SELL_TO_OPEN,
        strike=190.0, expiry=EXP_FUT, contracts=1, price=3.15)]
    r = run_diff([pos("NVDA", 200)],
                 [pos("NVDA", 200, 190.0, EXP_FUT)], executions=execs)
    assert r.events == []


def test_add_to_position_blended_avgcost_flagged():
    prev = [pos("NVDA", 300, 190.0, EXP_FUT, contracts=1, premium=2.5)]
    curr = [pos("NVDA", 300, 190.0, EXP_FUT, contracts=3, premium=2.9)]
    r = run_diff(prev, curr)
    [e] = r.events
    assert e.action == SELL_TO_OPEN and e.contracts == 2
    assert e.needs_confirm and "混合均价" in e.note


def test_avgcost_jump_same_contracts_notice_only():
    prev = [pos("NVDA", 200, 190.0, EXP_FUT, premium=2.5)]
    curr = [pos("NVDA", 200, 190.0, EXP_FUT, premium=3.4)]
    r = run_diff(prev, curr)
    assert not r.events
    assert any("突变" in n for n in r.notices)


# ---------------------------------------------------------------- 手动 roll 配对

def test_manual_roll_pairing_without_proposal():
    prev = [pos("NVDA", 200, 180.0, EXP_FUT, mid=5.2)]
    curr = [pos("NVDA", 200, 195.0, EXP_FUT2, premium=6.0)]
    r = run_diff(prev, curr)
    close = next(e for e in r.events if e.action == BUY_TO_CLOSE)
    opn = next(e for e in r.events if e.action == SELL_TO_OPEN)
    assert close.outcome == OUT_ROLLED and close.needs_confirm
    assert opn.rolled_from == CallKey("NVDA", 180.0, EXP_FUT)


def test_manual_roll_matched_to_approved_proposal():
    prev = [pos("NVDA", 200, 180.0, EXP_FUT, mid=5.2)]
    curr = [pos("NVDA", 200, 195.0, EXP_FUT2, premium=6.0)]
    approved = [("NVDA-abc123",
                 CallKey("NVDA", 180.0, EXP_FUT), CallKey("NVDA", 195.0, EXP_FUT2))]
    r = run_diff(prev, curr, approved=approved)
    close = next(e for e in r.events if e.action == BUY_TO_CLOSE)
    opn = next(e for e in r.events if e.action == SELL_TO_OPEN)
    assert close.proposal_id == "NVDA-abc123" and not close.needs_confirm
    assert opn.proposal_id == "NVDA-abc123"
    assert close.outcome == OUT_ROLLED


# ---------------------------------------------------------------- executions → 事件

def test_events_from_executions_attribution():
    old_key = CallKey("NVDA", 180.0, EXP_FUT)
    execs = [
        ExecutionRecord(exec_id="e1", ts=NOW, ticker="NVDA", action=BUY_TO_CLOSE,
                        strike=180.0, expiry=EXP_FUT, contracts=1, price=1.3,
                        fees=0.66, order_ref="NVDA-r1"),
        ExecutionRecord(exec_id="e2", ts=NOW, ticker="NVDA", action=SELL_TO_OPEN,
                        strike=195.0, expiry=EXP_FUT2, contracts=1, price=2.4,
                        fees=0.66, order_ref="NVDA-r1"),
        ExecutionRecord(exec_id="e3", ts=NOW, ticker="MSFT", action=SELL_TO_OPEN,
                        strike=500.0, expiry=EXP_FUT, contracts=1, price=4.0),
    ]
    events = events_from_executions(
        execs, {"NVDA-r1": "ROLL"}, {"NVDA-r1": old_key})
    close, opn, manual = events
    assert close.source == "executor" and close.outcome == OUT_ROLLED
    assert opn.rolled_from == old_key
    assert manual.source == "manual_tws" and manual.price_quality == "exact"


# ---------------------------------------------------------------- backfill

def test_backfill_events():
    positions = [pos("NVDA", 200, 190.0, EXP_FUT, contracts=2, premium=2.75),
                 pos("MSFT", 100)]
    events = backfill_events(positions, NOW)
    [e] = events
    assert e.action == SELL_TO_OPEN and e.source == "backfill"
    assert e.price == 2.75 and e.contracts == 2
    assert e.exec_id == f"synthetic:backfill:NVDA:190:{EXP_FUT.isoformat()}"
