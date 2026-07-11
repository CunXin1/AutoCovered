"""开仓提案校验(候选集成员资格 + 覆盖率 + 限价区间)与风格合并测试。"""
from datetime import date

import pytest

from src.config import ticker_qcc
from src.engine.roll import ChainQuote, RollCandidate
from src.execution.propose import default_limit, validate_limit, validate_open

EXP = date(2026, 8, 21)
EXP2 = date(2026, 9, 18)


def cand(strike, expiry=EXP, delta=0.22, premium=2.5, dte=40):
    return RollCandidate(expiry=expiry, strike=strike, dte=dte, delta=delta,
                         premium=premium, buyback_cost=0.0, net_credit=premium,
                         net_credit_annualized_pct=0.12, strike_improvement_pct=0.0)


def quote(strike, expiry=EXP, bid=2.40, ask=2.55):
    return ChainQuote(expiry=expiry, strike=strike, bid=bid, ask=ask, delta=0.22)


CANDS = [cand(190.0), cand(195.0)]
CHAIN = [quote(190.0), quote(195.0)]


def check(**kw):
    base = dict(strike=190.0, expiry=EXP, contracts=1, candidates=CANDS,
                chain=CHAIN, stock_qty=200, coverage_ratio=1.0,
                existing_short_contracts=0, pending_open_contracts=0)
    base.update(kw)
    return validate_open(**base)


# ---------------------------------------------------------------- 成员资格

def test_valid_proposal_passes():
    r = check()
    assert r.ok and r.candidate.strike == 190.0 and r.quote.bid == 2.40


def test_strike_not_in_candidate_set_rejected():
    r = check(strike=200.0)   # 不在候选集(delta 过滤后)
    assert not r.ok
    assert any("候选集" in e for e in r.errors)


def test_expiry_not_in_candidate_set_rejected():
    r = check(expiry=EXP2)
    assert not r.ok


# ---------------------------------------------------------------- 覆盖率(含审查 H1 场景)

def test_oversell_with_partial_coverage_blocked():
    """NVDA 400 股、ratio 0.5、已有 2 张空头 → 允许 0 张,禁止再卖。"""
    r = check(stock_qty=400, coverage_ratio=0.5, existing_short_contracts=2)
    assert not r.ok
    assert any("允许 0 张" in e for e in r.errors)


def test_pending_proposals_count_against_coverage():
    """两条各自合规的提案不许联合超卖:pending 也占额度。"""
    r = check(stock_qty=200, contracts=1, pending_open_contracts=2)
    assert not r.ok


def test_coverage_ok_at_boundary():
    r = check(stock_qty=200, contracts=2)
    assert r.ok


def test_zero_contracts_rejected():
    r = check(contracts=0)
    assert not r.ok


# ---------------------------------------------------------------- 限价

def test_default_limit_floor_to_nickel_not_below_bid():
    q = ChainQuote(expiry=EXP, strike=190.0, bid=2.40, ask=2.55, delta=0.2)  # mid 2.475
    assert default_limit(q) == 2.45
    q2 = ChainQuote(expiry=EXP, strike=190.0, bid=2.44, ask=2.46, delta=0.2)  # mid 2.45
    assert default_limit(q2) == 2.45


def test_validate_limit_bands():
    q = quote(190.0)   # bid 2.40 ask 2.55
    assert validate_limit(2.45, q) is None
    assert "贱卖" in validate_limit(2.0, q)        # < bid×0.9=2.16
    assert "不可能成交" in validate_limit(3.0, q)   # > ask×1.1=2.805
    assert validate_limit(0, q) is not None


# ---------------------------------------------------------------- 风格合并顺序

CFG = {
    "qcc": {"min_open_dte": 31, "target_delta_min": 0.20,
            "target_delta_max": 0.30, "coverage_ratio": 1.0},
    "styles": {"conservative": {"target_delta_min": 0.15, "target_delta_max": 0.25},
               "aggressive": {"target_delta_min": 0.30, "target_delta_max": 0.40}},
    "tickers": {"NVDA": {"coverage_ratio": 0.5,
                         "target_delta_min": 0.15, "target_delta_max": 0.20}},
}


def test_style_applies_to_plain_ticker():
    q = ticker_qcc(CFG, "MSFT", style="aggressive")
    assert q.target_delta_max == 0.40


def test_ticker_override_is_hard_cap_over_style():
    """aggressive 不能击穿 NVDA 的 per-ticker 风险上限。"""
    q = ticker_qcc(CFG, "NVDA", style="aggressive")
    assert q.target_delta_max == 0.20 and q.coverage_ratio == 0.5


def test_unknown_style_raises():
    with pytest.raises(ValueError):
        ticker_qcc(CFG, "MSFT", style="yolo")
