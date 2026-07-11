"""执行器测试:kind 路由、三重开关、改价护栏、执行前二次校验、每日限额。"""
import json
from datetime import date, datetime, timezone

import pytest

from src.execution.executor import Executor
from src.execution.proposals import (
    APPROVED,
    DRY_RUN_EXECUTED,
    FAILED,
    PENDING,
    SUBMITTED,
    Proposal,
    ProposalLeg,
    ProposalStore,
)

EXP = date(2026, 8, 21)
EXP2 = date(2026, 9, 18)


class FakeNotifier:
    def __init__(self):
        self.pushes = []

    def push(self, title, body="", severity=2, actions=None, tags=None):
        self.pushes.append((title, body))
        return True

    def approval_actions(self, pid):
        return []

    def titles(self):
        return [t for t, _ in self.pushes]


class FakeBroker:
    name = "fake"
    supports_trading = True

    def __init__(self, quote=None):
        self.quote = quote or {"bid": 2.40, "ask": 2.55, "mid": 2.475,
                               "delta": 0.22, "stock_price": 175.0}
        self.orders = []

    def quote_option(self, ticker, strike, expiry):
        return self.quote

    def place_open_call(self, **kw):
        self.orders.append(("open", kw))
        return "订单已提交(状态 Submitted)"

    def place_roll(self, **kw):
        self.orders.append(("roll", kw))
        return "订单已提交(状态 Submitted)"


def write_positions(tmp_path, qty=200, shorts=0):
    entries = [{"ticker": "NVDA", "stock": {"qty": qty, "avg_cost": 100, "price": 175},
                "call": None}]
    if shorts:
        entries.append({"ticker": "NVDA",
                        "stock": {"qty": qty, "avg_cost": 100, "price": 175},
                        "call": {"strike": 180.0, "expiry": "2026-08-21",
                                 "contracts": shorts, "open_premium": 2.0, "mid": 1.0}})
    (tmp_path / "positions.json").write_text(
        json.dumps({"positions": entries}), encoding="utf-8")


def make_executor(tmp_path, *, enabled=False, dry_run=True, broker=None, qcc=None):
    cfg = {"execution": {"enabled": enabled, "dry_run": dry_run,
                         "proposal_ttl_minutes": 30, "max_orders_per_day": 5},
           "qcc": qcc or {"coverage_ratio": 1.0}, "tickers": {}}
    store = ProposalStore(tmp_path / "proposals.json")
    notifier = FakeNotifier()
    ex = Executor(cfg, store, notifier, broker=broker)
    return ex, store, notifier


def open_proposal(store, contracts=1, limit=2.45) -> Proposal:
    p = Proposal.new(kind="OPEN_CALL", ticker="NVDA", position_id="NVDA",
                     legs=[ProposalLeg("SELL", 190.0, EXP, contracts)],
                     limit_net_credit=limit, rationale="t", ttl_minutes=30)
    store.save(p)
    return p


def roll_proposal(store) -> Proposal:
    p = Proposal.new(kind="ROLL", ticker="NVDA", position_id="NVDA 260821C180",
                     legs=[ProposalLeg("BUY", 180.0, EXP, 1),
                           ProposalLeg("SELL", 195.0, EXP2, 1)],
                     limit_net_credit=0.85, rationale="t", ttl_minutes=30)
    store.save(p)
    return p


# ---------------------------------------------------------------- 三重开关

def test_disabled_records_approval_only(tmp_path):
    ex, store, notifier = make_executor(tmp_path, enabled=False)
    p = open_proposal(store)
    ex.handle_approve(p.id)
    assert store.get(p.id).status == APPROVED
    assert any("未执行" in t for t in notifier.titles())


def test_dry_run_simulates(tmp_path):
    ex, store, notifier = make_executor(tmp_path, enabled=True, dry_run=True)
    p = open_proposal(store)
    ex.handle_approve(p.id)
    assert store.get(p.id).status == DRY_RUN_EXECUTED
    assert any("dry-run" in t for t in notifier.titles())


# ---------------------------------------------------------------- kind 路由

def test_open_call_routes_to_place_open_call(tmp_path):
    write_positions(tmp_path, qty=200)
    broker = FakeBroker()
    ex, store, _ = make_executor(tmp_path, enabled=True, dry_run=False, broker=broker)
    p = open_proposal(store)
    ex.handle_approve(p.id)
    assert store.get(p.id).status == SUBMITTED
    [(kind, kw)] = broker.orders
    assert kind == "open"
    assert kw["order_ref"] == p.id and kw["limit_price"] == 2.45
    assert kw["strike"] == 190.0 and kw["contracts"] == 1


def test_roll_routes_to_place_roll(tmp_path):
    broker = FakeBroker(quote={"bid": 2.0, "ask": 2.2, "mid": 2.1,
                               "delta": 0.3, "stock_price": 185.0})
    ex, store, _ = make_executor(tmp_path, enabled=True, dry_run=False, broker=broker)
    p = roll_proposal(store)
    ex.handle_approve(p.id)
    assert store.get(p.id).status == SUBMITTED
    [(kind, kw)] = broker.orders
    assert kind == "roll"
    assert kw["order_ref"] == p.id and kw["limit_credit"] == 0.85
    assert kw["old_strike"] == 180.0 and kw["new_strike"] == 195.0


# ---------------------------------------------------------------- 执行前二次校验

def test_precheck_rejects_no_longer_otm(tmp_path):
    write_positions(tmp_path, qty=200)
    broker = FakeBroker(quote={"bid": 8.0, "ask": 8.4, "mid": 8.2,
                               "delta": 0.62, "stock_price": 192.0})   # 现价 ≥ 190 strike
    ex, store, notifier = make_executor(tmp_path, enabled=True, dry_run=False, broker=broker)
    p = open_proposal(store)
    ex.handle_approve(p.id)
    assert store.get(p.id).status == FAILED
    assert not broker.orders
    assert any("OTM" in b for _, b in notifier.pushes)


def test_precheck_rejects_deteriorated_mid(tmp_path):
    write_positions(tmp_path, qty=200)
    broker = FakeBroker(quote={"bid": 1.0, "ask": 1.2, "mid": 1.1,
                               "delta": 0.1, "stock_price": 175.0})   # mid 1.1 < 2.45×0.7
    ex, store, _ = make_executor(tmp_path, enabled=True, dry_run=False, broker=broker)
    p = open_proposal(store)
    ex.handle_approve(p.id)
    assert store.get(p.id).status == FAILED
    assert not broker.orders


def test_precheck_rejects_coverage_shortfall(tmp_path):
    # 400 股 × ratio 0.5 = 2 张上限,已有空头 2 张 → 允许 0
    write_positions(tmp_path, qty=400, shorts=2)
    broker = FakeBroker()
    ex, store, _ = make_executor(tmp_path, enabled=True, dry_run=False, broker=broker,
                                 qcc={"coverage_ratio": 0.5})
    p = open_proposal(store)
    ex.handle_approve(p.id)
    assert store.get(p.id).status == FAILED
    assert not broker.orders


# ---------------------------------------------------------------- 改价护栏

def test_limit_override_fat_finger_rejected(tmp_path):
    ex, store, notifier = make_executor(tmp_path, enabled=False)
    p = open_proposal(store, limit=2.45)
    ex.handle_approve(p.id, limit_override=0.24)   # < 一半
    got = store.get(p.id)
    assert got.status == PENDING                    # 提案保持待批,可重试
    assert got.limit_net_credit == 2.45
    assert any("改价被拒" in t for t in notifier.titles())


def test_limit_override_applied(tmp_path):
    ex, store, _ = make_executor(tmp_path, enabled=False)
    p = open_proposal(store, limit=2.45)
    ex.handle_approve(p.id, limit_override=2.60)
    got = store.get(p.id)
    assert got.limit_net_credit == 2.60
    assert got.status == APPROVED   # 决策支持模式:改价后照常记录批准


# ---------------------------------------------------------------- 限额与过期

def test_daily_order_limit(tmp_path):
    write_positions(tmp_path, qty=1000)
    broker = FakeBroker()
    ex, store, notifier = make_executor(tmp_path, enabled=True, dry_run=False, broker=broker)
    today = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(ex.orders_log, "w", encoding="utf-8") as f:
        for i in range(5):
            f.write(json.dumps({"ts": today, "proposal_id": f"x{i}",
                                "ticker": "NVDA", "kind": "OPEN_CALL",
                                "dry_run": False}) + "\n")
    p = open_proposal(store)
    ex.handle_approve(p.id)
    assert store.get(p.id).status == FAILED
    assert not broker.orders


def test_expired_proposal_rejected(tmp_path):
    from datetime import timedelta

    ex, store, _ = make_executor(tmp_path, enabled=True, dry_run=False,
                                 broker=FakeBroker())
    p = open_proposal(store)
    p.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    store.save(p)
    ex.handle_approve(p.id)
    assert store.get(p.id).status == "expired"
