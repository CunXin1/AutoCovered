"""维度 1/4/5/6 的确定性事实层:IV 分位、区间位置、涨幅、目标价上行。

存在理由见模块 docstring —— 这些数字原先靠 WebSearch,各站算法不同、不可复核。
"""
from datetime import date

from src.data.market_context import (
    AnalystContext,
    IVContext,
    build_price_context,
    pct_change,
    rank_and_percentile,
)


def test_rank_and_percentile_basics():
    # 当前值就是区间最低 → Rank 0,百分位 0
    assert rank_and_percentile([0.5, 0.9, 0.3]) == (0.0, 0.0)
    # 当前值是最高 → Rank 100,百分位 = 低于它的占比
    rank, pct = rank_and_percentile([0.3, 0.5, 0.9])
    assert rank == 100.0 and round(pct, 2) == round(2 / 3 * 100, 2)
    assert rank_and_percentile([]) == (None, None)


def test_rank_and_percentile_diverge_on_skewed_distribution():
    """少数尖峰把 max 拉高:Rank 看起来居中,百分位仍显示今天偏低。

    这正是 CRWV 2026-10-02 的情形(Rank 26 / 百分位 5),所以两个都要输出。
    """
    # 绝大多数日子 IV 都更高,最低点只被少数几天触及,今天略高于最低点:
    # 端点口径(Rank)因 max 很远而显得居中,分布口径(百分位)则正确反映"偏低"
    values = [0.95] * 90 + [0.52] * 4 + [1.2398] + [0.6985]   # 末位 = 当前
    rank, pct = rank_and_percentile(values)
    assert 20 < rank < 35          # 端点口径:看着居中
    assert pct < 15                # 分布口径:比绝大多数日子低
    assert rank - pct > 15         # 分歧足够大,渲染层会打警告


def test_flat_series_has_no_rank_but_has_percentile():
    rank, pct = rank_and_percentile([0.4, 0.4, 0.4])
    assert rank is None            # 区间退化,不编造
    assert pct == 0.0


def test_pct_change_refuses_to_extrapolate():
    series = [100.0, 110.0, 121.0]
    assert round(pct_change(series, 1), 4) == 0.1
    assert pct_change(series, 3) is None       # 样本不足 → None,不外推
    assert pct_change(series, 0) is None


def test_price_context_52w_window_and_position():
    # 300 根日线:52 周窗口只取最后 252 根,更早的极值不该污染
    bars = [(date(2025, 1, 1), 999.0, 1.0, 500.0)]          # 窗口外的极端值
    bars += [(date(2026, 1, 1), 100.0 + i, 90.0 + i, 95.0 + i) for i in range(260)]
    ctx = build_price_context(bars)
    assert ctx.n == 252
    assert ctx.high_52w < 999.0 and ctx.low_52w > 1.0        # 窗口外的值被排除
    assert ctx.spot == 95.0 + 259
    assert ctx.position_in_range is not None and 0.9 < ctx.position_in_range <= 1.0
    assert ctx.pct_from_high is not None


def test_iv_verdict_thresholds_match_the_rulebook():
    assert "低位" in IVContext(iv_rank=11.0).verdict()
    assert "中位" in IVContext(iv_rank=26.0).verdict()
    assert "中位" in IVContext(iv_rank=50.0).verdict()
    assert "高位" in IVContext(iv_rank=58.0).verdict()
    assert "不足" in IVContext().verdict()


def test_iv_hv_ratio_and_analyst_upside():
    iv = IVContext(iv=0.2873, hv=0.3524)
    assert round(iv.iv_hv_ratio, 2) == 0.82      # <1 = 期权定价低于已实现波动
    assert IVContext(iv=0.7, hv=None).iv_hv_ratio is None
    an = AnalystContext(target_mean=327.7)
    assert round(an.upside_from(234.27), 3) == 0.399
    assert AnalystContext().upside_from(100.0) is None
