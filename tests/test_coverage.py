"""覆盖完整性(裸卖守卫)与事件日期卫生。"""
from datetime import date

from src.data.events import upcoming_or_none
from src.engine.state_machine import coverage_shortfalls
from src.models import Position, ShortCall, StockHolding


def make(ticker: str, qty: float, contracts: int | None, account: str = "",
         strike: float = 100.0, expiry: date = date(2026, 8, 21)) -> Position:
    stock = StockHolding(qty=qty, avg_cost=50.0, price=90.0)
    call = None
    if contracts is not None:
        call = ShortCall(strike=strike, expiry=expiry, contracts=contracts,
                         open_premium=1.0, mid=1.0)
    return Position(ticker=ticker, stock=stock, call=call, account=account)


def test_fully_covered_and_uncovered_are_silent():
    positions = [
        make("NOK", 1000, 8, "schwab"),   # 800 ≤ 1000
        make("MSFT", 200, 2),             # 恰好全覆盖
        make("KMEM", 1000, None, "schwab"),  # 无腿不检查
    ]
    assert coverage_shortfalls(positions) == []


def test_naked_exposure_detected():
    out = coverage_shortfalls([make("NOK", 1000, 11, "schwab")])
    assert len(out) == 1
    account, ticker, reason = out[0]
    assert (account, ticker) == ("schwab", "NOK")
    assert "缺口 100 股" in reason


def test_multi_leg_aggregates_shared_stock():
    # 两条腿共享同一份正股:8 + 3 = 11 张 > 1000 股,单腿各自却都 ≤ 1000
    a = make("NOK", 1000, 8, "schwab", strike=18)
    b = make("NOK", 1000, 3, "schwab", strike=20, expiry=date(2026, 9, 18))
    b.stock = a.stock
    out = coverage_shortfalls([a, b])
    assert len(out) == 1
    assert "共 11 张" in out[0][2]


def test_accounts_checked_independently():
    positions = [
        make("NVDA", 100, 1),             # 主账户恰好覆盖
        make("NVDA", 250, 3, "schwab"),   # 次级账户超卖 1 张
    ]
    out = coverage_shortfalls(positions)
    assert [(a, t) for a, t, _ in out] == [("schwab", "NVDA")]


def test_upcoming_or_none_filters_past_dates():
    today = date(2026, 7, 14)
    assert upcoming_or_none(date(2026, 4, 28), today) is None   # 上一次除息
    assert upcoming_or_none(date(2026, 7, 28), today) == date(2026, 7, 28)
    assert upcoming_or_none(date(2026, 7, 14), today) == date(2026, 7, 14)  # 当日算未来
    assert upcoming_or_none(None, today) is None
