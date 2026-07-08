"""状态机全覆盖测试 — 系统里唯一不许出错的部分。"""
from datetime import date, timedelta

from src.engine.pnl import compute_metrics
from src.engine.state_machine import evaluate, should_notify
from src.models import (
    AlertConfig,
    EventDates,
    Position,
    PositionState,
    ShortCall,
    StateResult,
    StockHolding,
)

TODAY = date(2026, 7, 9)
CFG = AlertConfig()


def make_pos(
    price=100.0,
    strike=115.0,
    dte=45,
    delta=0.20,
    open_premium=2.0,
    mid=1.5,
    qty=100,
    avg_cost=90.0,
    acquired=None,
    earnings=None,
    ex_div=None,
    with_call=True,
) -> Position:
    call = None
    if with_call:
        call = ShortCall(
            strike=strike,
            expiry=TODAY + timedelta(days=dte),
            contracts=int(qty // 100),
            open_premium=open_premium,
            mid=mid,
            delta=delta,
        )
    pos = Position(
        ticker="TEST",
        stock=StockHolding(qty=qty, avg_cost=avg_cost, price=price, acquired_date=acquired),
        call=call,
        events=EventDates(earnings=earnings, ex_div=ex_div),
    )
    pos.metrics = compute_metrics(pos, TODAY)
    return pos


def state_of(pos) -> StateResult:
    return evaluate(pos, CFG, TODAY)


# ---------------------------------------------------------------- 基础状态


def test_uncovered():
    r = state_of(make_pos(with_call=False))
    assert r.state == PositionState.UNCOVERED


def test_on_track_default():
    r = state_of(make_pos())  # OTM 15%, delta 0.20, dte 45, 利润 25%
    assert r.state == PositionState.ON_TRACK
    assert r.severity == 0


def test_profit_take():
    r = state_of(make_pos(open_premium=2.0, mid=0.9))  # 赚回 55%
    assert r.state == PositionState.PROFIT_TAKE


def test_manage_dte():
    r = state_of(make_pos(dte=20, mid=1.5))  # 20 DTE, 利润仅 25%
    assert r.state == PositionState.MANAGE_DTE
    assert PositionState.MANAGE_DTE in r.flags


def test_profit_take_beats_manage_dte_on_tie():
    # 同为 severity 1:PROFIT_TAKE 排在前
    r = state_of(make_pos(dte=20, open_premium=2.0, mid=0.9))
    assert r.state == PositionState.PROFIT_TAKE
    assert PositionState.MANAGE_DTE in r.flags


def test_expiring():
    r = state_of(make_pos(dte=5, mid=1.5))
    assert r.state == PositionState.EXPIRING


# ---------------------------------------------------------------- 用户点名的两个场景


def test_tested_by_distance():
    """快被击穿防线:距 strike 3% 以内。"""
    r = state_of(make_pos(price=112.0, strike=115.0, delta=0.40))  # 距 2.68%
    assert r.state == PositionState.TESTED


def test_tested_by_delta():
    r = state_of(make_pos(delta=0.50))
    assert r.state == PositionState.TESTED


def test_breached_stock_surge():
    """股票暴涨越过 strike:BREACHED 为主状态,TESTED/OPTION_LOSS 进 flags。"""
    r = state_of(make_pos(price=120.0, strike=115.0, delta=0.80, open_premium=2.0, mid=6.5))
    assert r.state == PositionState.BREACHED
    assert PositionState.TESTED in r.flags
    assert PositionState.OPTION_LOSS in r.flags
    assert r.severity == 4


def test_option_loss_before_breach():
    """股价还没到 strike,但 call 腿买回成本已翻倍 → 止损告警。"""
    r = state_of(make_pos(price=110.0, strike=115.0, delta=0.42, open_premium=2.0, mid=4.2))
    assert r.state == PositionState.OPTION_LOSS


def test_option_loss_threshold_exact():
    r = state_of(make_pos(open_premium=2.0, mid=4.0))  # 恰好 2.0x
    assert PositionState.OPTION_LOSS in r.flags


# ---------------------------------------------------------------- roll 窗口与事件


def test_roll_window():
    r = state_of(make_pos(delta=0.65, dte=20))
    assert r.state == PositionState.ROLL_WINDOW
    assert PositionState.TESTED in r.flags  # delta 0.65 也触发 tested


def test_roll_window_requires_dte_range():
    r = state_of(make_pos(delta=0.65, dte=45))  # dte 超窗口 → 只算 TESTED
    assert r.state == PositionState.TESTED


def test_event_risk_earnings():
    r = state_of(make_pos(earnings=TODAY + timedelta(days=2)))
    assert r.state == PositionState.EVENT_RISK


def test_event_risk_earnings_beyond_warn_window():
    r = state_of(make_pos(earnings=TODAY + timedelta(days=10)))  # 超出 3 天预警窗
    assert r.state == PositionState.ON_TRACK


def test_event_risk_exdiv_needs_high_delta():
    ex = TODAY + timedelta(days=2)
    assert state_of(make_pos(ex_div=ex, delta=0.42)).state == PositionState.EVENT_RISK
    assert state_of(make_pos(ex_div=ex, delta=0.20)).state == PositionState.ON_TRACK


def test_breached_beats_everything():
    r = state_of(make_pos(price=120.0, strike=115.0, delta=0.9, dte=5,
                          earnings=TODAY + timedelta(days=2)))
    assert r.state == PositionState.BREACHED
    assert PositionState.EXPIRING in r.flags
    assert PositionState.EVENT_RISK in r.flags


# ---------------------------------------------------------------- Greeks 缺失降级


def test_degraded_no_delta_price_rules_still_work():
    """备源(SnapTrade)无 Greeks:delta 类规则跳过,价格类规则照常。"""
    r = state_of(make_pos(price=120.0, strike=115.0, delta=None))
    assert r.state == PositionState.BREACHED
    r2 = state_of(make_pos(delta=None))
    assert r2.state == PositionState.ON_TRACK


# ---------------------------------------------------------------- 推送去抖


def test_notify_on_escalation():
    r = state_of(make_pos(delta=0.50))  # TESTED
    assert should_notify(PositionState.ON_TRACK, r, None, TODAY)


def test_no_notify_when_unchanged():
    r = state_of(make_pos(delta=0.50))
    assert not should_notify(PositionState.TESTED, r, TODAY - timedelta(days=1), TODAY)


def test_notify_on_recovery_from_severe():
    r = state_of(make_pos())  # ON_TRACK
    assert should_notify(PositionState.BREACHED, r, None, TODAY)


def test_no_notify_on_first_sight_normal():
    r = state_of(make_pos())
    assert not should_notify(None, r, None, TODAY)


def test_expiring_daily_reminder():
    r = state_of(make_pos(dte=5, mid=1.5))
    assert should_notify(PositionState.EXPIRING, r, TODAY - timedelta(days=1), TODAY)
    assert not should_notify(PositionState.EXPIRING, r, TODAY, TODAY)
