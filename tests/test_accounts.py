"""主券商多账户的隔离语义。

2026-10 起正股分散在 IBKR 的 3 个账户(U24088860 / U25868195 / U27626748),
"account 非空 = 外部只读账户"的旧假设作废:作用域看 external,身份看 account。
账户之间的股票不能互相覆盖,所以覆盖检查、指派推断、账本轮次都必须按账户隔离。
"""
from datetime import date

import pytest

from src.engine.lifecycle import ASSIGN, EXPIRE, SELL_TO_OPEN, TradeEvent, diff_positions
from src.ledger import Ledger
from src.models import Position, ShortCall, StockHolding

A1 = "U24088860"
A2 = "U25868195"
TODAY = date(2026, 10, 2)
EXP_FUT = date(2026, 11, 20)
NOW = "2026-10-02T20:00:00+00:00"
SRC = "ibkr_gateway"


def pos(ticker, qty, account, strike=None, expiry=EXP_FUT, contracts=1,
        premium=2.5, mid=1.2, external=False):
    call = None
    if strike is not None:
        call = ShortCall(strike=strike, expiry=expiry, contracts=contracts,
                         open_premium=premium, mid=mid)
    return Position(ticker=ticker,
                    stock=StockHolding(qty=qty, avg_cost=100.0, price=150.0),
                    call=call, account=account, external=external)


# ---------------------------------------------------------------- 身份与作用域

def test_position_id_distinct_across_broker_accounts():
    a = pos("NVDA", 100, A2)
    b = pos("NVDA", 250, "U27626748")
    assert a.position_id != b.position_id
    assert a.position_id == f"NVDA@{A2}"


def test_broker_accounts_are_in_ledger_scope_but_external_is_not():
    book = [pos("NVDA", 100, A2), pos("GOOG", 200, "schwab", external=True)]
    assert [p.ticker for p in book if not p.external] == ["NVDA"]


def test_external_backcompat_from_old_snapshot():
    """旧快照只有 account 字段,当时非空就等于次级只读账户。"""
    d = pos("GOOG", 200, "schwab", external=True).to_dict()
    assert d["external"] is True
    d.pop("external")
    assert Position.from_dict(d).external is True
    # 新快照显式写 external:IBKR 账户号不再被误判为外部
    d2 = pos("NVDA", 100, A2).to_dict()
    assert d2["external"] is False
    assert Position.from_dict(d2).external is False


# ---------------------------------------------------------------- 指派推断隔离

def test_assignment_does_not_pool_shares_across_accounts():
    """A2 被指派 100 股;A1 同标的股数不变。

    回归:股数曾按 ticker 聚合且取首见账户的值 —— 先遇到未变动的 A1 时
    整体 drop=0,A2 的真实指派会被误判成到期/买回。
    """
    prev = [pos("NVDA", 100, A1), pos("NVDA", 100, A2, strike=190.0)]
    curr = [pos("NVDA", 100, A1)]      # A2 的股票与腿一起消失 = 被指派
    r = diff_positions(prev=prev, curr=curr, prev_source=SRC, curr_source=SRC,
                       executions=[], expiry_closes={}, today=TODAY, now_iso=NOW)
    assert [(e.action, e.account) for e in r.events] == [(ASSIGN, A2)]


def test_same_contract_in_two_accounts_is_tracked_separately():
    """两个账户持同一合约:只有一个账户到期消失,另一个不受影响。"""
    exp_past = date(2026, 9, 18)
    prev = [pos("MSFT", 200, A1, strike=550.0, expiry=exp_past, contracts=2),
            pos("MSFT", 200, A2, strike=550.0, expiry=exp_past, contracts=2)]
    curr = [pos("MSFT", 200, A1, strike=550.0, expiry=exp_past, contracts=2),
            pos("MSFT", 200, A2)]
    r = diff_positions(prev=prev, curr=curr, prev_source=SRC, curr_source=SRC,
                       executions=[], expiry_closes={("MSFT", exp_past): 500.0},
                       today=TODAY, now_iso=NOW)
    assert [(e.action, e.account, e.contracts) for e in r.events] == [(EXPIRE, A2, 2)]


# ---------------------------------------------------------------- 账本轮次隔离

@pytest.fixture
def ledger(tmp_path):
    lg = Ledger(tmp_path / "ledger.db")
    yield lg
    lg.close()


def sto(account, exec_id):
    return TradeEvent(exec_id=exec_id, ts=NOW, ticker="MSFT", action=SELL_TO_OPEN,
                      strike=550.0, expiry=EXP_FUT, contracts=2, price=17.92,
                      source="manual_tws", price_quality="exact", account=account)


def test_same_contract_two_accounts_two_rounds(ledger):
    [a] = ledger.apply([sto(A1, "x1")])
    [b] = ledger.apply([sto(A2, "x2")])
    assert a.round_id != b.round_id
    rows = ledger.conn.execute(
        "SELECT account, outcome FROM rounds ORDER BY id").fetchall()
    assert [r["account"] for r in rows] == [A1, A2]


def test_close_attaches_to_same_account_round(ledger):
    [a] = ledger.apply([sto(A1, "x1")])
    ledger.apply([sto(A2, "x2")])
    [c] = ledger.apply([TradeEvent(
        exec_id="c1", ts=NOW, ticker="MSFT", action=EXPIRE, strike=550.0,
        expiry=EXP_FUT, contracts=2, price=0.0, source="inferred",
        price_quality="exact", account=A1)])
    assert c.round_id == a.round_id
    assert ledger.conn.execute(
        "SELECT outcome FROM rounds WHERE id=?", (a.round_id,)).fetchone()[0] == "expired"
    # 另一个账户的轮次不受影响
    assert ledger.conn.execute(
        "SELECT COUNT(*) FROM rounds WHERE outcome='open'").fetchone()[0] == 1


def test_account_column_migration_is_idempotent(tmp_path):
    """老库(无 account 列)打开即补列,重复打开不报错。"""
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
      CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT,
        exec_id TEXT UNIQUE NOT NULL, ts TEXT NOT NULL, ticker TEXT NOT NULL,
        action TEXT NOT NULL, strike REAL NOT NULL, expiry TEXT NOT NULL,
        contracts INTEGER NOT NULL, price REAL NOT NULL DEFAULT 0,
        fees REAL NOT NULL DEFAULT 0, source TEXT NOT NULL,
        price_quality TEXT NOT NULL, proposal_id TEXT DEFAULT '',
        round_id INTEGER, aux_price REAL, note TEXT DEFAULT '');
      CREATE TABLE rounds (id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL, strike REAL NOT NULL, expiry TEXT NOT NULL,
        opened_ts TEXT NOT NULL, closed_ts TEXT, outcome TEXT NOT NULL DEFAULT 'open',
        rolled_from_round_id INTEGER);
    """)
    conn.commit()
    conn.close()
    for _ in range(2):
        lg = Ledger(path)
        assert "account" in {r["name"] for r in
                             lg.conn.execute("PRAGMA table_info(trades)")}
        lg.apply([sto(A1, "m1")])
        lg.close()


# ---------------------------------------------------------------- 建仓日期(税务倒计时)

def test_lot_date_prefers_account_scoped_key():
    from src.config import lot_date

    lots = {"NVDA": date(2026, 6, 15),
            "NVDA@U27626748": date(2026, 2, 3),
            "NVDA@U25868195": date(2026, 6, 30)}
    # 同一标的在两个账户的建仓日相差 4 个月,倒计时不能混用
    assert lot_date(lots, "NVDA", "U27626748") == date(2026, 2, 3)
    assert lot_date(lots, "NVDA", "U25868195") == date(2026, 6, 30)
    assert lot_date(lots, "NVDA") == date(2026, 6, 15)          # 无账户 → 通用键
    assert lot_date(lots, "NVDA", "U99999999") == date(2026, 6, 15)  # 未列账户 → 退回
    assert lot_date(lots, "MSFT", "U27626748") is None
