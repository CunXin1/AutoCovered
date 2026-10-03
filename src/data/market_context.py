"""市场背景 — 开仓研究维度 1/4/5/6 的确定性事实层。

为什么存在:这四个维度(IV 水位、技术阻力、趋势状态、分析师目标价)原先靠
WebSearch 取数,违反"所有数字来自 state 或确定性脚本输出"的铁律 —— 第三方站点
的 IV Rank 各家算法不同(实测 2026-10-02:CRWV 某站 11,按 IBKR 自己的 IV 序列
算是 26),拿来当决策阈值不可复核。现在:

- 维度 1 IV 水位:IBKR 的 OPTION_IMPLIED_VOLATILITY / HISTORICAL_VOLATILITY 日线
  → IV Rank(在一年区间的位置)、IV 百分位(低于今天的交易日占比)、IV/HV 比
- 维度 4/5 阻力与趋势:IBKR 正股日线 → 52 周高低点、距高低点、各期涨幅、区间位置
- 维度 6 分析师目标价:yfinance 的 targetMeanPrice 等(IBKR 免费档不提供)

WebSearch 的位置随之收窄:只提供**叙述性事实**(为什么异动、有什么催化剂、
核对财报日期),不再提供任何进入决策阈值的数字。

IV Rank 与 IV 百分位会分歧,这不是 bug:Rank 只看区间端点,百分位看整个分布。
少数几天的 IV 尖峰会把 max 拉高、让 Rank 看起来居中,而百分位仍显示"今天比
绝大多数日子都低"。两个都输出,由分析层解释。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

log = logging.getLogger(__name__)

TRADING_DAYS_1Y = 252


# ---------------------------------------------------------------- 纯函数


def rank_and_percentile(values: list[float]) -> tuple[Optional[float], Optional[float]]:
    """(IV Rank, IV 百分位),都是 0–100。

    Rank = (当前 − 区间最低) / (区间最高 − 区间最低);区间退化时返回 None。
    百分位 = 严格低于当前值的样本占比。
    """
    if not values:
        return None, None
    cur = values[-1]
    lo, hi = min(values), max(values)
    rank = None if hi <= lo else (cur - lo) / (hi - lo) * 100
    pct = sum(1 for v in values if v < cur) / len(values) * 100
    return rank, pct


def pct_change(series: list[float], lookback: int) -> Optional[float]:
    """最近 lookback 根之前到现在的涨幅;样本不足返回 None(不外推)。"""
    if len(series) <= lookback or lookback <= 0:
        return None
    past = series[-1 - lookback]
    return (series[-1] / past - 1) if past else None


@dataclass
class IVContext:
    iv: Optional[float] = None
    iv_low: Optional[float] = None
    iv_high: Optional[float] = None
    iv_rank: Optional[float] = None
    iv_percentile: Optional[float] = None
    hv: Optional[float] = None
    n: int = 0

    @property
    def iv_hv_ratio(self) -> Optional[float]:
        if self.iv and self.hv:
            return self.iv / self.hv
        return None

    def verdict(self) -> str:
        """按 strike-research 维度 1 的阈值给判定(IVR<20 偏贱卖,>50 积极卖)。"""
        if self.iv_rank is None:
            return "IV 数据不足"
        if self.iv_rank < 20:
            return "IV 低位(规则:SKIP 或减张)"
        if self.iv_rank > 50:
            return "IV 高位(规则:积极卖,可用同等权利金换更远 strike)"
        return "IV 中位(规则:正常执行)"


@dataclass
class PriceContext:
    spot: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    ret_1m: Optional[float] = None
    ret_3m: Optional[float] = None
    ret_6m: Optional[float] = None
    n: int = 0

    @property
    def pct_from_high(self) -> Optional[float]:
        if self.spot and self.high_52w:
            return self.spot / self.high_52w - 1
        return None

    @property
    def position_in_range(self) -> Optional[float]:
        """现价在 52 周区间里的位置(0=最低,1=最高)。"""
        if self.spot is None or self.high_52w is None or self.low_52w is None:
            return None
        span = self.high_52w - self.low_52w
        return (self.spot - self.low_52w) / span if span > 0 else None


@dataclass
class AnalystContext:
    target_mean: Optional[float] = None
    target_high: Optional[float] = None
    target_low: Optional[float] = None
    n_analysts: Optional[int] = None
    recommendation: str = ""

    def upside_from(self, spot: float) -> Optional[float]:
        if self.target_mean and spot:
            return self.target_mean / spot - 1
        return None


@dataclass
class MarketContext:
    ticker: str
    iv: IVContext = field(default_factory=IVContext)
    price: PriceContext = field(default_factory=PriceContext)
    analyst: AnalystContext = field(default_factory=AnalystContext)
    notes: list[str] = field(default_factory=list)


def build_price_context(bars: list[tuple]) -> PriceContext:
    """bars: [(date, high, low, close)] 升序。52 周 = 最近 252 个交易日。"""
    if not bars:
        return PriceContext()
    window = bars[-TRADING_DAYS_1Y:]
    closes = [b[3] for b in bars if b[3]]
    highs = [b[1] for b in window if b[1]]
    lows = [b[2] for b in window if b[2]]
    return PriceContext(
        spot=closes[-1] if closes else None,
        high_52w=max(highs) if highs else None,
        low_52w=min(lows) if lows else None,
        ret_1m=pct_change(closes, 21),
        ret_3m=pct_change(closes, 63),
        ret_6m=pct_change(closes, 126),
        n=len(window),
    )


# ---------------------------------------------------------------- 取数


def fetch_analyst(ticker: str) -> AnalystContext:
    try:
        import yfinance as yf

        i = yf.Ticker(ticker).info or {}
    except Exception as e:
        log.warning("%s 分析师数据获取失败: %s", ticker, e)
        return AnalystContext()
    return AnalystContext(
        target_mean=i.get("targetMeanPrice"),
        target_high=i.get("targetHighPrice"),
        target_low=i.get("targetLowPrice"),
        n_analysts=i.get("numberOfAnalystOpinions"),
        recommendation=str(i.get("recommendationKey") or ""),
    )


def build(client, ticker: str) -> MarketContext:
    """client 需提供 daily_bars() 与 vol_history()(src.brokers.ibkr)。"""
    ctx = MarketContext(ticker=ticker.upper())

    bars = client.daily_bars(ticker, "1 Y")
    if bars:
        ctx.price = build_price_context(bars)
    else:
        ctx.notes.append("拿不到正股日线,维度 4/5 无数据")

    iv_series = [v for _, v in client.vol_history(ticker, "OPTION_IMPLIED_VOLATILITY")]
    hv_series = [v for _, v in client.vol_history(ticker, "HISTORICAL_VOLATILITY")]
    if iv_series:
        rank, pct = rank_and_percentile(iv_series)
        ctx.iv = IVContext(iv=iv_series[-1], iv_low=min(iv_series), iv_high=max(iv_series),
                           iv_rank=rank, iv_percentile=pct,
                           hv=hv_series[-1] if hv_series else None, n=len(iv_series))
    else:
        ctx.notes.append("拿不到 IV 历史,维度 1 无数据(该标的可能无期权行情权限)")

    ctx.analyst = fetch_analyst(ticker)
    if ctx.analyst.target_mean is None:
        ctx.notes.append("拿不到分析师目标价,维度 6 无数据")
    return ctx


# ---------------------------------------------------------------- CLI


def render(ctx: MarketContext) -> str:
    iv, pr, an = ctx.iv, ctx.price, ctx.analyst
    out = [f"# {ctx.ticker} 市场背景(维度 1/4/5/6 的确定性事实)", ""]

    out.append("## 维度 1:IV 水位")
    if iv.iv is not None:
        out.append(f"- 当前 IV **{iv.iv:.1%}**,一年区间 {iv.iv_low:.1%}–{iv.iv_high:.1%}"
                   f"({iv.n} 个交易日)")
        out.append(f"- **IV Rank {iv.iv_rank:.0f}** / **IV 百分位 {iv.iv_percentile:.0f}%**"
                   f" → {iv.verdict()}")
        if iv.iv_hv_ratio:
            cmp = "期权定价高于已实现波动(对卖方有利)" if iv.iv_hv_ratio > 1 else \
                  "期权定价低于已实现波动(对卖方不利)"
            out.append(f"- IV/HV = {iv.iv_hv_ratio:.2f}(HV {iv.hv:.1%})—— {cmp}")
        if (iv.iv_rank is not None and iv.iv_percentile is not None
                and abs(iv.iv_rank - iv.iv_percentile) > 15):
            out.append("- ⚠️ Rank 与百分位分歧较大:IV 分布被少数尖峰拉偏,"
                       "按百分位解读更贴近「今天贵不贵」")
    else:
        out.append("- 无数据")

    out += ["", "## 维度 4/5:阻力位与趋势"]
    if pr.spot is not None:
        out.append(f"- 现价 {pr.spot:.2f};52 周区间 **{pr.low_52w:.2f}–{pr.high_52w:.2f}**")
        out.append(f"- 距 52 周高点 **{pr.pct_from_high:+.1%}**"
                   f",区间位置 {pr.position_in_range:.0%}(0=最低 100%=最高)")
        rets = [(k, v) for k, v in (("1 月", pr.ret_1m), ("3 月", pr.ret_3m),
                                    ("6 月", pr.ret_6m)) if v is not None]
        if rets:
            out.append("- 涨幅:" + " · ".join(f"{k} {v:+.1%}" for k, v in rets))
        if pr.pct_from_high is not None and pr.pct_from_high > -0.03:
            out.append("- ⚠️ 现价贴着 52 周高点(3% 内):上方没有阻力墙可借,"
                       "维度 4/5 倾向 ↑strike + 减张")
    else:
        out.append("- 无数据")

    out += ["", "## 维度 6:分析师目标价"]
    if an.target_mean is not None:
        up = an.upside_from(pr.spot) if pr.spot else None
        line = f"- 一致目标 **{an.target_mean:.2f}**"
        if up is not None:
            line += f"(距现价 **{up:+.1%}**)"
        if an.target_high and an.target_low:
            line += f",区间 {an.target_low:.0f}–{an.target_high:.0f}"
        if an.n_analysts:
            line += f",{an.n_analysts} 家"
        if an.recommendation:
            line += f",评级 {an.recommendation}"
        out.append(line)
        if up is not None and up > 0.20:
            out.append("- ⚠️ 一致目标比现价高逾 20%:低于目标价的候选 strike 按维度 6 "
                       "规则应 ↑strike 或减张")
    else:
        out.append("- 无数据")

    if ctx.notes:
        out += ["", "## 数据缺口"] + [f"- {n}" for n in ctx.notes]
    out += ["", "注:以上数字全部来自 IBKR 历史数据与 yfinance 接口,可复算。"
            "WebSearch 只用于叙述性事实(异动原因、催化剂、核对财报日期),"
            "不提供进入决策阈值的数字。"]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="开仓研究维度 1/4/5/6 的确定性事实")
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    from src.brokers.ibkr import IBKRGatewayClient
    from src.config import load_config

    cfg = load_config()
    ib_cfg = dict(cfg.get("ibkr") or {})
    ib_cfg["client_id"] = int(ib_cfg.get("client_id", 11)) + 5
    client = IBKRGatewayClient(ib_cfg)
    try:
        contexts = [build(client, t) for t in args.tickers]
    finally:
        client.disconnect()

    if args.as_json:
        def dump(c):
            return {"ticker": c.ticker, "iv": c.iv.__dict__,
                    "iv_hv_ratio": c.iv.iv_hv_ratio, "verdict": c.iv.verdict(),
                    "price": {**c.price.__dict__, "pct_from_high": c.price.pct_from_high,
                              "position_in_range": c.price.position_in_range},
                    "analyst": c.analyst.__dict__, "notes": c.notes}
        print(json.dumps([dump(c) for c in contexts], ensure_ascii=False, indent=2))
    else:
        print("\n\n".join(render(c) for c in contexts))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
