from datetime import date, datetime, timedelta, timezone

from src.execution.proposals import (
    EXPIRED,
    PENDING,
    Proposal,
    ProposalLeg,
    ProposalStore,
)

EXP_OLD = date(2026, 7, 17)
EXP_NEW = date(2026, 8, 21)


def make_proposal(ttl_minutes=30) -> Proposal:
    return Proposal.new(
        kind="ROLL",
        ticker="NVDA",
        position_id="NVDA 260717C180",
        legs=[
            ProposalLeg("BUY", 180.0, EXP_OLD, 2),
            ProposalLeg("SELL", 195.0, EXP_NEW, 2),
        ],
        limit_net_credit=0.85,
        rationale="test",
        ttl_minutes=ttl_minutes,
    )


def test_roundtrip(tmp_path):
    store = ProposalStore(tmp_path / "proposals.json")
    p = make_proposal()
    store.save(p)
    loaded = store.get(p.id)
    assert loaded is not None
    assert loaded.status == PENDING
    assert loaded.legs[0].action == "BUY"
    assert loaded.legs[1].expiry == EXP_NEW
    assert loaded.limit_net_credit == 0.85


def test_pending_dedup(tmp_path):
    store = ProposalStore(tmp_path / "proposals.json")
    p = make_proposal()
    store.save(p)
    assert store.has_pending_for("NVDA 260717C180")
    assert not store.has_pending_for("MSFT 260717C500")
    p.status = "rejected"
    store.save(p)
    assert not store.has_pending_for("NVDA 260717C180")


def test_expiry(tmp_path):
    store = ProposalStore(tmp_path / "proposals.json")
    p = make_proposal()
    p.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    store.save(p)
    assert p.is_expired()
    assert store.expire_stale() == 1
    assert store.get(p.id).status == EXPIRED
    assert not store.has_pending_for("NVDA 260717C180")


def test_missing_returns_none(tmp_path):
    store = ProposalStore(tmp_path / "proposals.json")
    assert store.get("nope") is None
