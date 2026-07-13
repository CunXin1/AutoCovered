"""SnapTrade 持仓解析(次级监控源)。fixture 形状取自 2026-07-13 真实账户响应。"""
from datetime import date

from src.brokers.snaptrade import build_account_positions, parse_equity, parse_short_call
from src.models import Position, StockHolding


def eq(ticker: str, units: float, avg: float, price: float) -> dict:
    return {
        "symbol": {"symbol": {"raw_symbol": ticker, "description": ticker}},
        "units": units,
        "average_purchase_price": avg,
        "price": price,
    }


def opt(underlying: str, right: str, strike: float, expiry: str,
        units: float, avg: float, price: float) -> dict:
    return {
        "symbol": {"option_symbol": {
            "option_type": right,
            "strike_price": strike,
            "expiration_date": expiry,
            "underlying_symbol": {"symbol": underlying},
        }},
        "units": units,
        "average_purchase_price": avg,
        "price": price,
    }


# ------------------------------------------------------------------ 单条解析


def test_parse_equity_basic():
    ticker, stock = parse_equity(eq("NVDA", 250.0, 192.5672, 204.24))
    assert ticker == "NVDA"
    assert stock.qty == 250.0
    assert stock.avg_cost == 192.5672
    assert stock.price == 204.24


def test_parse_equity_rejects_empty_and_nonpositive():
    assert parse_equity(eq("", 100, 1, 1)) is None
    assert parse_equity(eq("NOK", 0, 1, 1)) is None
    assert parse_equity(eq("NOK", -100, 1, 1)) is None


def test_parse_short_call_premium_per_share():
    # avg_purchase_price 含 100 乘数(179.34 → 1.7934/股);price 已是每股
    underlying, call = parse_short_call(
        opt("NOW", "CALL", 150.0, "2026-08-21", -1.0, 179.34, 1.74))
    assert underlying == "NOW"
    assert call.strike == 150.0
    assert call.expiry == date(2026, 8, 21)
    assert call.contracts == 1
    assert abs(call.open_premium - 1.7934) < 1e-9
    assert call.mid == 1.74
    assert call.delta is None


def test_parse_short_call_skips_puts_longs_and_bad_expiry():
    assert parse_short_call(opt("NOK", "PUT", 10, "2026-08-21", -5, 50, 0.5)) is None
    assert parse_short_call(opt("NOK", "CALL", 18, "2026-08-21", 3, 50, 0.5)) is None
    assert parse_short_call(opt("NOK", "CALL", 18, "garbage", -3, 50, 0.5)) is None


# ------------------------------------------------------------------ 账户组装


def test_build_pairs_stock_with_call_and_tags_account():
    positions = build_account_positions(
        equities=[eq("NVDA", 250, 192.5672, 204.24)],
        options=[opt("NVDA", "CALL", 245, "2026-08-28", -2, 219.325, 2.14)],
        account_tag="schwab",
        lots={"NVDA": date(2026, 6, 15)},
    )
    assert len(positions) == 1
    p = positions[0]
    assert p.account == "schwab"
    assert p.position_id == "NVDA 260828C245@schwab"
    assert p.call.contracts == 2
    assert p.stock.qty == 250
    assert p.stock.acquired_date == date(2026, 6, 15)


def test_build_keeps_bare_stock_and_skips_odd_lots():
    positions = build_account_positions(
        equities=[eq("KMEM", 1000, 23.235, 18.92), eq("AVGO", 40, 387.34, 386.5)],
        options=[],
        account_tag="schwab",
    )
    ids = [p.position_id for p in positions]
    assert ids == ["KMEM@schwab"]          # AVGO 40 股无腿 → 不跟踪
    assert positions[0].call is None


def test_build_skips_orphan_call_without_stock():
    positions = build_account_positions(
        equities=[],
        options=[opt("NOK", "CALL", 18, "2026-08-21", -8, 14.2075, 0.145)],
        account_tag="schwab",
    )
    assert positions == []                 # 裸 call 非 covered,不产出


def test_build_multiple_legs_share_stock():
    positions = build_account_positions(
        equities=[eq("NOK", 1000, 16.9, 11.755)],
        options=[
            opt("NOK", "CALL", 18, "2026-08-21", -8, 14.2075, 0.145),
            opt("NOK", "CALL", 20, "2026-09-18", -2, 10.0, 0.10),
        ],
        account_tag="schwab",
    )
    assert {p.position_id for p in positions} == {
        "NOK 260821C18@schwab", "NOK 260918C20@schwab"}
    assert positions[0].stock is positions[1].stock


# ------------------------------------------------------------------ 账户维度隔离


def test_position_id_no_collision_across_accounts():
    stock = StockHolding(qty=100, avg_cost=100.0, price=100.0)
    primary = Position(ticker="NVDA", stock=stock)
    schwab = Position(ticker="NVDA", stock=stock, account="schwab")
    assert primary.position_id == "NVDA"
    assert schwab.position_id == "NVDA@schwab"


def test_position_account_roundtrip_and_backcompat():
    stock = StockHolding(qty=100, avg_cost=100.0, price=100.0)
    p = Position(ticker="GOOG", stock=stock, account="schwab")
    d = p.to_dict()
    assert d["account"] == "schwab"
    assert Position.from_dict(d).account == "schwab"
    # 旧快照没有 account 字段 → 主账户
    d.pop("account")
    assert Position.from_dict(d).account == ""
    # watcher 账本作用域过滤的最小语义
    book = [p, Position(ticker="MSFT", stock=stock)]
    assert [x.ticker for x in book if not x.account] == ["MSFT"]
