from datetime import date, timedelta

import pytest

from src.engine import pnl
from src.models import Position, ShortCall, StockHolding

TODAY = date(2026, 7, 9)


def test_option_pnl_and_pct():
    assert pnl.option_pnl_per_share(2.35, 1.10) == pytest.approx(1.25)
    assert pnl.pct_max_profit(2.0, 0.9) == pytest.approx(0.55)
    assert pnl.pct_max_profit(2.0, 4.0) == pytest.approx(-1.0)  # 翻倍亏损
    assert pnl.pct_max_profit(0.0, 1.0) is None  # 权利金为 0 无意义


def test_breakeven_and_distance():
    assert pnl.breakeven(150.0, 2.35) == pytest.approx(147.65)
    assert pnl.distance_to_strike_pct(110.0, 100.0) == pytest.approx(0.10)
    assert pnl.distance_to_strike_pct(110.0, 120.0) == pytest.approx(-1 / 12)  # 已越过为负
    assert pnl.distance_to_strike_pct(110.0, 0.0) is None


def test_combined_pnl():
    # 200 股 @150 现价 172.5,2 张 call 收 2.35 现值 1.10
    total = pnl.combined_pnl(200, 150.0, 172.5, 2, 2.35, 1.10)
    assert total == pytest.approx(200 * 22.5 + 2 * 100 * 1.25)  # 4750


def test_annualized():
    assert pnl.annualized_premium_pct(2.0, 100.0, 36.5) == pytest.approx(0.20)
    assert pnl.annualized_premium_pct(2.0, 100.0, 0) is None


def test_compute_metrics_full():
    pos = Position(
        ticker="NVDA",
        stock=StockHolding(qty=200, avg_cost=150.0, price=172.5,
                           acquired_date=TODAY - timedelta(days=200)),
        call=ShortCall(strike=180.0, expiry=TODAY + timedelta(days=40), contracts=2,
                       open_premium=2.35, mid=1.10, delta=0.22,
                       open_date=TODAY - timedelta(days=5)),
    )
    m = pnl.compute_metrics(pos, TODAY)
    assert m.dte == 40
    assert m.pct_max_profit == pytest.approx(1.25 / 2.35)
    assert m.breakeven == pytest.approx(147.65)
    assert m.combined_pnl == pytest.approx(4750.0)
    assert m.days_held == 200
    assert m.is_long_term is False
    assert m.days_to_long_term == 166
    assert m.annualized_premium_pct == pytest.approx(2.35 / 172.5 * 365 / 45)


def test_compute_metrics_uncovered():
    pos = Position(ticker="X", stock=StockHolding(qty=300, avg_cost=10.0, price=12.0))
    m = pnl.compute_metrics(pos, TODAY)
    assert m.dte is None
    assert m.combined_pnl == pytest.approx(600.0)
