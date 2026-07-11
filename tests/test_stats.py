"""统计模块测试:fixture 账本 → 手算断言(round 级/链级/质量分层/upside_forgone)。"""
from datetime import date

import pytest

from src.engine.lifecycle import (
    ASSIGN,
    BUY_TO_CLOSE,
    EXPIRE,
    SELL_TO_OPEN,
    CallKey,
    TradeEvent,
)
from src.ledger import Ledger
from src.stats import compute_stats, render_markdown

EXP = date(2026, 8, 21)
EXP2 = date(2026, 9, 18)


def ev(action, *, exec_id, ticker="NVDA", strike=190.0, expiry=EXP, contracts=1,
       price=2.5, fees=0.0, ts="2026-07-10T18:00:00+00:00", outcome="",
       rolled_from=None, quality="exact", aux_price=None):
    return TradeEvent(exec_id=exec_id, ts=ts, ticker=ticker, action=action,
                      strike=strike, expiry=expiry, contracts=contracts,
                      price=price, fees=fees, source="manual_tws",
                      price_quality=quality, outcome=outcome,
                      rolled_from=rolled_from, aux_price=aux_price)


@pytest.fixture
def loaded(tmp_path):
    lg = Ledger(tmp_path / "ledger.db")
    # NVDA round1:STO 2x@2.50(费1.32)→ BTC 2x@1.00(费1.32,推断价)= +297.36 胜
    lg.apply([ev(SELL_TO_OPEN, exec_id="r1o", contracts=2, price=2.50, fees=1.32)])
    lg.apply([ev(BUY_TO_CLOSE, exec_id="r1c", contracts=2, price=1.00, fees=1.32,
                 quality="inferred", ts="2026-07-15T18:00:00+00:00")])
    # NVDA roll 链:round2 STO 1x@5.00 → BTC @6.00(rolled)= −100 负;
    # round3 STO 1x@4.00(rolled_from)→ EXPIRE = +400 胜;链 = +300
    lg.apply([ev(SELL_TO_OPEN, exec_id="r2o", strike=180.0, price=5.00)])
    lg.apply([
        ev(BUY_TO_CLOSE, exec_id="r2c", strike=180.0, price=6.00, outcome="rolled",
           ts="2026-07-20T18:00:00+00:00"),
        ev(SELL_TO_OPEN, exec_id="r3o", strike=200.0, expiry=EXP2, price=4.00,
           ts="2026-07-20T18:00:00+00:00",
           rolled_from=CallKey("NVDA", 180.0, EXP)),
    ])
    lg.apply([ev(EXPIRE, exec_id="r3x", strike=200.0, expiry=EXP2, price=0.0,
                 outcome="expired", ts="2026-09-18T20:00:00+00:00")])
    # MSFT:STO 1x@3.00 → ASSIGN(到期收盘 510,strike 500)= +300;放弃上涨 1000
    lg.apply([ev(SELL_TO_OPEN, exec_id="m1o", ticker="MSFT", strike=500.0, price=3.00)])
    lg.apply([ev(ASSIGN, exec_id="m1a", ticker="MSFT", strike=500.0, price=0.0,
                 outcome="assigned", aux_price=510.0,
                 ts="2026-08-21T20:00:00+00:00")])
    # CRWV:进行中 STO 1x@2.00 = +200
    lg.apply([ev(SELL_TO_OPEN, exec_id="c1o", ticker="CRWV", strike=150.0, price=2.00)])
    yield lg
    lg.close()


def test_per_ticker_realized(loaded):
    s = compute_stats(loaded.conn)
    nvda, msft, crwv = s["tickers"]["NVDA"], s["tickers"]["MSFT"], s["tickers"]["CRWV"]
    assert nvda["realized"] == pytest.approx(297.36 - 100.0 + 400.0)
    assert nvda["closed"] == 3 and nvda["wins"] == 2
    assert nvda["win_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert nvda["outcomes"] == {"bought_back": 1, "rolled": 1, "expired": 1}
    assert msft["realized"] == 300.0 and msft["outcomes"] == {"assigned": 1}
    assert msft["upside_forgone"] == 1000.0
    assert crwv["open"] == 1 and crwv["open_cash"] == 200.0 and crwv["closed"] == 0


def test_chain_level_aggregation(loaded):
    s = compute_stats(loaded.conn)
    closed_chains = [c for c in s["chains"] if c["closed"]]
    # NVDA round1 单轮链 +297.36;roll 链 (round2+round3) = +300;MSFT +300
    chain_cash = sorted(c["cash"] for c in closed_chains)
    assert chain_cash == pytest.approx([297.36, 300.0, 300.0])
    roll_chain = next(c for c in closed_chains if len(c["rounds"]) == 2)
    assert roll_chain["cash"] == pytest.approx(300.0)
    assert s["total"]["chain_wins"] == 3 and s["total"]["closed_chains"] == 3
    # CRWV 进行中链不算已了结
    assert all("CRWV" != c["tickers"] or not c["closed"] for c in s["chains"])


def test_quality_stratification(loaded):
    s = compute_stats(loaded.conn)
    # 含推断价的只有 NVDA round1(+297.36)
    assert s["tickers"]["NVDA"]["inexact_realized"] == pytest.approx(297.36)
    assert s["total"]["inexact_realized"] == pytest.approx(297.36)
    assert s["total"]["confirmable_trades"] == 1


def test_totals_and_filter(loaded):
    s = compute_stats(loaded.conn)
    assert s["total"]["realized"] == pytest.approx(297.36 + 300.0 + 300.0)
    assert s["total"]["open_rounds"] == 1
    only = compute_stats(loaded.conn, ticker="msft")
    assert list(only["tickers"]) == ["MSFT"]


def test_markdown_renders(loaded):
    md = render_markdown(compute_stats(loaded.conn))
    assert "NVDA" in md and "Roll 链口径" in md
    assert "放弃上涨" in md and "CONFIRM" in md


def test_empty_ledger(tmp_path):
    lg = Ledger(tmp_path / "empty.db")
    md = render_markdown(compute_stats(lg.conn))
    assert "账本为空" in md
    lg.close()
