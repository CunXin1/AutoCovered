from datetime import date, timedelta

from src.engine import qcc
from src.models import QccConfig

TODAY = date(2026, 7, 9)
CFG = QccConfig()


def test_qualifies_otm_and_dte():
    ok, problems = qcc.qualifies(strike=110, price=100, open_dte=35, cfg=CFG)
    assert ok and not problems


def test_itm_fails():
    ok, problems = qcc.qualifies(strike=95, price=100, open_dte=35, cfg=CFG)
    assert not ok
    assert any("OTM" in p for p in problems)


def test_short_dte_fails():
    ok, problems = qcc.qualifies(strike=110, price=100, open_dte=20, cfg=CFG)
    assert not ok


def test_crosses_event():
    expiry = TODAY + timedelta(days=40)
    assert qcc.crosses_event(expiry, TODAY + timedelta(days=10), TODAY)
    assert not qcc.crosses_event(expiry, TODAY + timedelta(days=50), TODAY)
    assert not qcc.crosses_event(expiry, TODAY - timedelta(days=1), TODAY)  # 已过去
    assert not qcc.crosses_event(expiry, None, TODAY)


def test_long_term_boundary():
    # 持有恰好 365 天:还不是长期(要求 >365)
    acq = TODAY - timedelta(days=365)
    assert not qcc.is_long_term(acq, TODAY)
    assert qcc.days_to_long_term(acq, TODAY) == 1
    # 366 天:长期
    acq2 = TODAY - timedelta(days=366)
    assert qcc.is_long_term(acq2, TODAY)
    assert qcc.days_to_long_term(acq2, TODAY) == 0
