"""账本(SQLite trades/rounds)测试:幂等、rounds 生命周期、roll 链、CONFIRM。"""
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

EXP = date(2026, 8, 21)
EXP2 = date(2026, 9, 18)


@pytest.fixture
def ledger(tmp_path):
    lg = Ledger(tmp_path / "ledger.db")
    yield lg
    lg.close()


def ev(action, *, exec_id, ticker="NVDA", strike=190.0, expiry=EXP, contracts=1,
       price=2.5, ts="2026-07-10T18:00:00+00:00", outcome="", rolled_from=None,
       source="manual_tws", quality="exact", proposal_id=""):
    return TradeEvent(exec_id=exec_id, ts=ts, ticker=ticker, action=action,
                      strike=strike, expiry=expiry, contracts=contracts,
                      price=price, source=source, price_quality=quality,
                      proposal_id=proposal_id, outcome=outcome,
                      rolled_from=rolled_from)


def round_row(ledger, rid):
    return ledger.conn.execute("SELECT * FROM rounds WHERE id=?", (rid,)).fetchone()


# ---------------------------------------------------------------- 基本生命周期

def test_open_then_full_close(ledger):
    [a] = ledger.apply([ev(SELL_TO_OPEN, exec_id="o1", contracts=2)])
    r = round_row(ledger, a.round_id)
    assert r["outcome"] == "open"

    [c] = ledger.apply([ev(BUY_TO_CLOSE, exec_id="c1", contracts=2, price=1.1,
                           ts="2026-07-15T18:00:00+00:00")])
    r = round_row(ledger, c.round_id)
    assert c.round_id == a.round_id
    assert r["outcome"] == "bought_back"
    assert r["closed_ts"].startswith("2026-07-15")


def test_partial_close_keeps_round_open(ledger):
    ledger.apply([ev(SELL_TO_OPEN, exec_id="o1", contracts=3)])
    [c] = ledger.apply([ev(BUY_TO_CLOSE, exec_id="c1", contracts=1)])
    assert round_row(ledger, c.round_id)["outcome"] == "open"
    [c2] = ledger.apply([ev(BUY_TO_CLOSE, exec_id="c2", contracts=2)])
    assert round_row(ledger, c2.round_id)["outcome"] == "bought_back"


def test_expire_and_assign_outcomes(ledger):
    ledger.apply([ev(SELL_TO_OPEN, exec_id="o1", contracts=2)])
    applied = ledger.apply([
        ev(ASSIGN, exec_id="a1", contracts=1, price=0.0, outcome="assigned"),
        ev(EXPIRE, exec_id="x1", contracts=1, price=0.0, outcome="expired",
           ts="2026-08-21T20:00:00+00:00"),
    ])
    rid = applied[0].round_id
    # 最后一笔(EXPIRE)关掉 round,outcome 取该笔的判定
    assert round_row(ledger, rid)["outcome"] == "expired"


def test_open_after_expiry_starts_new_round(ledger):
    """同 strike/expiry 二次开仓(前一轮已关)→ 新 round,不复用旧的。"""
    ledger.apply([ev(SELL_TO_OPEN, exec_id="o1")])
    [c] = ledger.apply([ev(BUY_TO_CLOSE, exec_id="c1", price=1.0)])
    [o2] = ledger.apply([ev(SELL_TO_OPEN, exec_id="o2")])
    assert o2.round_id != c.round_id


# ---------------------------------------------------------------- roll 链

def test_roll_chain_links_rounds(ledger):
    [a] = ledger.apply([ev(SELL_TO_OPEN, exec_id="o1", strike=180.0)])
    applied = ledger.apply([
        ev(BUY_TO_CLOSE, exec_id="c1", strike=180.0, price=5.2, outcome="rolled",
           ts="2026-07-20T18:00:00+00:00"),
        ev(SELL_TO_OPEN, exec_id="o2", strike=195.0, expiry=EXP2, price=6.0,
           ts="2026-07-20T18:00:00+00:00",
           rolled_from=CallKey("NVDA", 180.0, EXP)),
    ])
    close = next(x for x in applied if x.event.action == BUY_TO_CLOSE)
    opn = next(x for x in applied if x.event.action == SELL_TO_OPEN)
    assert round_row(ledger, close.round_id)["outcome"] == "rolled"
    new_round = round_row(ledger, opn.round_id)
    assert new_round["rolled_from_round_id"] == a.round_id
    # 同时间戳时平仓先于开仓执行(exec_id 顺序无关)
    assert close.round_id == a.round_id


# ---------------------------------------------------------------- 幂等与孤儿

def test_replay_is_noop(ledger):
    events = [ev(SELL_TO_OPEN, exec_id="o1", contracts=2),
              ev(BUY_TO_CLOSE, exec_id="c1", contracts=2, price=1.0,
                 ts="2026-07-11T15:00:00+00:00")]
    first = ledger.apply(events)
    assert len(first) == 2
    assert ledger.apply(events) == []   # 重放:零新行
    n = ledger.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    assert n == 2
    rn = ledger.conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0]
    assert rn == 1


def test_orphan_close_creates_closed_round(ledger):
    [c] = ledger.apply([ev(BUY_TO_CLOSE, exec_id="c1", price=1.0)])
    r = round_row(ledger, c.round_id)
    assert r["outcome"] == "bought_back"


# ---------------------------------------------------------------- 查询与修正

def test_is_empty_and_backfill_flag(ledger):
    assert ledger.is_empty()
    ledger.apply([ev(SELL_TO_OPEN, exec_id="synthetic:backfill:x",
                     source="backfill", quality="backfill")])
    assert not ledger.is_empty()


def test_confirm_trade_price(ledger):
    [a] = ledger.apply([ev(BUY_TO_CLOSE, exec_id="c1", price=1.35,
                           quality="inferred", source="inferred")])
    row = ledger.confirm_trade_price(a.trade_id, 1.28)
    assert row["price"] == 1.28 and row["price_quality"] == "user_confirmed"
    assert ledger.confirm_trade_price(9999, 1.0) is None


def test_filled_contracts_by_proposal(ledger):
    ledger.apply([
        ev(SELL_TO_OPEN, exec_id="e1", contracts=1, source="executor",
           proposal_id="NVDA-p1"),
        ev(SELL_TO_OPEN, exec_id="e2", contracts=2, source="executor",
           proposal_id="NVDA-p1"),
        ev(SELL_TO_OPEN, exec_id="e3", contracts=5, source="manual_tws"),
    ])
    assert ledger.filled_contracts("NVDA-p1") == 3
