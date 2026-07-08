from datetime import date, timedelta

import pytest

from src.engine.roll import ChainQuote, find_open_candidates, find_roll_candidates
from src.models import QccConfig, RollConfig

TODAY = date(2026, 7, 9)
CFG = RollConfig()
QCC = QccConfig()

# 现状:strike 100 已被击穿(现价 105),旧腿买回成本 6.0/股
CURRENT = dict(current_mid=6.0, current_strike=100.0, stock_price=105.0)


def q(days, strike, bid, ask, delta=None):
    return ChainQuote(expiry=TODAY + timedelta(days=days), strike=strike,
                      bid=bid, ask=ask, delta=delta)


def test_chain_quote_mid():
    assert q(45, 110, 6.2, 6.6).mid == pytest.approx(6.4)
    assert q(45, 110, 6.2, 0.0).mid == pytest.approx(6.2)  # 单边报价取保守值


def test_net_credit_filter():
    chain = [
        q(45, 110, 6.2, 6.6, 0.30),   # mid 6.4 → net +0.4 ✓
        q(45, 115, 5.4, 5.8, 0.25),   # mid 5.6 → net -0.4 ✗(默认只要 credit)
    ]
    out = find_roll_candidates(**CURRENT, chain=chain, today=TODAY, cfg=CFG)
    assert len(out) == 1
    assert out[0].strike == 110
    assert out[0].net_credit == pytest.approx(0.4)
    assert out[0].net_credit_annualized_pct == pytest.approx(0.4 / 105 * 365 / 45)


def test_dte_window_and_otm_and_delta_filters():
    chain = [
        q(20, 110, 7.0, 7.4, 0.30),   # dte 20 < 30 ✗
        q(80, 110, 8.0, 8.4, 0.30),   # dte 80 > 60 ✗
        q(45, 100, 8.0, 8.4, 0.45),   # strike 100 <= 现价 105(ITM)✗
        q(45, 108, 7.0, 7.4, 0.45),   # delta 0.45 > 0.35 ✗
        q(45, 110, 6.2, 6.6, 0.30),   # ✓
    ]
    out = find_roll_candidates(**CURRENT, chain=chain, today=TODAY, cfg=CFG)
    assert [c.strike for c in out] == [110]


def test_earnings_exclusion():
    chain = [q(45, 110, 6.2, 6.6, 0.30)]
    out = find_roll_candidates(**CURRENT, chain=chain, today=TODAY, cfg=CFG,
                               earnings_date=TODAY + timedelta(days=40))
    assert out == []
    # 财报在到期之后则不影响
    out2 = find_roll_candidates(**CURRENT, chain=chain, today=TODAY, cfg=CFG,
                                earnings_date=TODAY + timedelta(days=50))
    assert len(out2) == 1


def test_debit_for_improvement():
    cfg = RollConfig(allow_debit_for_improvement=True)
    chain = [q(45, 115, 5.4, 5.8, 0.25)]  # net -0.4, strike 改善 15%
    out = find_roll_candidates(**CURRENT, chain=chain, today=TODAY, cfg=cfg)
    assert len(out) == 1
    assert out[0].net_credit == pytest.approx(-0.4)
    # debit 超上限(0.5)仍拒绝
    chain2 = [q(45, 120, 5.0, 5.2, 0.20)]  # mid 5.1 → net -0.9
    assert find_roll_candidates(**CURRENT, chain=chain2, today=TODAY, cfg=cfg) == []


def test_sorted_by_annualized_and_top_n():
    chain = [
        q(60, 112, 6.5, 6.9, 0.30),   # net +0.7 / 60d
        q(31, 110, 6.4, 6.8, 0.32),   # net +0.6 / 31d → 年化更高
        q(45, 111, 6.3, 6.7, 0.30),   # net +0.5 / 45d
    ]
    out = find_roll_candidates(**CURRENT, chain=chain, today=TODAY, cfg=CFG, top_n=2)
    assert len(out) == 2
    assert out[0].dte == 31  # 年化最高在前


def test_open_candidates_qcc_filters():
    chain = [
        q(45, 115, 1.4, 1.6, 0.22),   # ✓
        q(45, 112, 2.0, 2.2, 0.35),   # delta 超出 0.30 ✗
        q(20, 115, 1.0, 1.2, 0.22),   # dte < 31 ✗
        q(45, 100, 6.0, 6.4, 0.55),   # ITM ✗
        q(45, 118, 1.0, 1.2, None),   # 无 delta(开仓必须有)✗
    ]
    out = find_open_candidates(stock_price=105.0, chain=chain, today=TODAY, qcc=QCC)
    assert [c.strike for c in out] == [115]
    assert out[0].net_credit == pytest.approx(1.5)
