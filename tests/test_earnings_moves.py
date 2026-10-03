"""历史财报涨幅工具的纯函数测试(跨财报定价的事实层)。

规则 2026-10-02 起:财报季不必跳过,但 strike 要按该标的历史财报涨幅上提。
"上提多少"由本模块给数字,所以反应日定位、分位数、击穿率必须可验证。
"""
from datetime import date

from src.data.earnings_moves import (
    EarningsMove,
    breach_rate,
    compute_moves,
    percentile,
    summarize,
)

# (date, high, low, close) 升序;构造 6 个交易日
BARS = [
    (date(2026, 8, 24), 101.0, 99.0, 100.0),
    (date(2026, 8, 25), 102.0, 99.5, 100.0),   # 盘后财报的前一交易日
    (date(2026, 8, 26), 118.0, 104.0, 112.0),  # 反应日:盘中最高 +18%,收盘 +12%
    (date(2026, 8, 27), 113.0, 108.0, 110.0),
    (date(2026, 8, 28), 111.0, 104.0, 105.0),
    (date(2026, 8, 31), 106.0, 100.0, 101.0),
]


def test_amc_reaction_is_next_trading_day():
    [m] = compute_moves(BARS, [(date(2026, 8, 25), "amc")])
    assert m.reaction_date == date(2026, 8, 26)
    assert m.prior_close == 100.0
    assert round(m.close_move, 4) == 0.12
    assert round(m.max_move, 4) == 0.18


def test_bmo_reaction_is_same_day():
    [m] = compute_moves(BARS, [(date(2026, 8, 26), "bmo")])
    assert m.reaction_date == date(2026, 8, 26)
    assert m.prior_close == 100.0      # 前一交易日 8/25
    assert round(m.max_move, 4) == 0.18


def test_amc_on_friday_skips_weekend():
    # 8/28 是周五,盘后财报 → 反应日是 8/31 周一
    [m] = compute_moves(BARS, [(date(2026, 8, 28), "amc")])
    assert m.reaction_date == date(2026, 8, 31)
    assert m.prior_close == 105.0


def test_events_outside_bar_range_are_skipped_not_guessed():
    assert compute_moves(BARS, [(date(2026, 9, 30), "amc")]) == []
    # 反应日是序列第一根 → 没有前收,跳过
    assert compute_moves(BARS, [(date(2026, 8, 23), "bmo")]) == []


def test_percentile_interpolates_and_handles_small_samples():
    assert percentile([], 0.5) is None
    assert percentile([0.1], 0.85) == 0.1
    assert percentile([0.0, 0.1], 0.5) == 0.05
    assert round(percentile([0.02, 0.05, 0.09, 0.18], 0.85), 4) == 0.1395


def mv(close_pct, max_pct):
    return EarningsMove(earnings_date=date(2026, 1, 1), reaction_date=date(2026, 1, 2),
                        when="amc", prior_close=100.0,
                        reaction_close=100.0 * (1 + close_pct),
                        reaction_high=100.0 * (1 + max_pct))


def test_summarize_counts_only_upside_for_percentiles():
    moves = [mv(0.10, 0.12), mv(-0.08, 0.01), mv(0.04, 0.06), mv(0.20, 0.25)]
    s = summarize(moves)
    assert s["n"] == 4 and s["n_up"] == 3
    assert s["up_rate"] == 0.75
    assert s["max_up_max"] == 0.25
    assert round(s["median_up_close"], 4) == 0.10


def test_breach_rate_max_basis_is_conservative():
    moves = [mv(0.10, 0.12), mv(-0.08, 0.01), mv(0.04, 0.06), mv(0.20, 0.25)]
    # strike 距现价 +11%:盘中口径 2/4 被触及,收盘口径只有 1/4
    bm = breach_rate(moves, 0.11, "max")
    bc = breach_rate(moves, 0.11, "close")
    assert (bm["breached"], bm["n"]) == (2, 4)
    assert (bc["breached"], bc["n"]) == (1, 4)
    assert bm["rate"] == 0.5 and bc["rate"] == 0.25
    # 距现价 +30%:历史上一次都没打穿
    assert breach_rate(moves, 0.30, "max")["breached"] == 0
    assert breach_rate([], 0.1) is None
