"""历史财报涨幅 — 跨财报开仓定价的事实层(Claude 读数字,不自己估)。

为什么需要它:2026-10-02 起规则改为"财报季不必跳过,但 strike 要按该标的
历史财报涨幅往上提"。"往上提多少"必须是可计算的,否则就是凭感觉编数字 ——
本模块给出历史财报日的实际上行幅度分布,以及每个候选 strike 的**历史击穿率**
(过去 N 次财报里有几次会打穿它)。定价判断仍由 Claude 做。

口径:
- 财报日来自 yfinance 的 earnings_dates(带时点:16:00 = 盘后 amc,
  盘前则 hour < 12);**反应日** = 盘后财报的次个交易日,盘前财报的当日
- close_move = 反应日收盘 / 前一交易日收盘 − 1(实际吃到的 gap + 当日走势)
- max_move  = 反应日最高 / 前一交易日收盘 − 1(盘中最高触及,covered call
  更该看这个:strike 被触及即意味着当时已 ITM)
- 击穿率按 max_move 算(保守口径),同时并列 close_move 口径

日线来自 IBKR(确定性,与持仓同源);yfinance 只提供财报日期。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_LOOKBACK = 12


@dataclass
class EarningsMove:
    earnings_date: date
    reaction_date: date
    when: str            # amc | bmo
    prior_close: float
    reaction_close: float
    reaction_high: float

    @property
    def close_move(self) -> float:
        return self.reaction_close / self.prior_close - 1 if self.prior_close else 0.0

    @property
    def max_move(self) -> float:
        return self.reaction_high / self.prior_close - 1 if self.prior_close else 0.0


def past_earnings_dates(ticker: str, limit: int = 25,
                        today: Optional[date] = None) -> list[tuple[date, str]]:
    """历史财报日 [(日期, amc|bmo)],新到旧。拿不到返回空列表(调用方据此降级)。"""
    today = today or date.today()
    try:
        import yfinance as yf

        df = yf.Ticker(ticker).get_earnings_dates(limit=limit)
    except Exception as e:
        log.warning("%s 历史财报日获取失败: %s", ticker, e)
        return []
    out: list[tuple[date, str]] = []
    for ts in getattr(df, "index", []):
        try:
            naive = ts.tz_localize(None) if ts.tzinfo else ts
        except (AttributeError, TypeError):
            continue
        d = naive.date()
        if d >= today:
            continue
        out.append((d, "bmo" if naive.hour < 12 else "amc"))
    return sorted(out, reverse=True)


def compute_moves(bars: list[tuple], events: list[tuple[date, str]]) -> list[EarningsMove]:
    """纯函数:日线序列 + 财报日 → 每次财报的反应日涨幅。

    bars: [(date, high, low, close)] 升序;events: [(财报日, amc|bmo)]
    财报日或反应日不在序列内的事件跳过(不猜)。
    """
    by_date = {b[0]: b for b in bars}
    days = sorted(by_date)
    idx = {d: i for i, d in enumerate(days)}
    out: list[EarningsMove] = []
    for ed, when in events:
        # 反应日:盘后财报 → 财报日之后的第一个交易日;盘前 → 财报日当日(或之后第一个)
        after = [d for d in days if (d > ed if when == "amc" else d >= ed)]
        if not after:
            continue
        rd = after[0]
        i = idx[rd]
        if i == 0:
            continue
        prior = by_date[days[i - 1]]
        reaction = by_date[rd]
        if not (prior[3] and reaction[3] and reaction[1]):
            continue
        out.append(EarningsMove(earnings_date=ed, reaction_date=rd, when=when,
                                prior_close=prior[3], reaction_close=reaction[3],
                                reaction_high=reaction[1]))
    return sorted(out, key=lambda m: m.earnings_date, reverse=True)


def percentile(values: list[float], q: float) -> Optional[float]:
    """线性插值分位数(q∈[0,1])。样本不足返回 None 而不是假装有统计量。"""
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def summarize(moves: list[EarningsMove]) -> dict:
    """上行幅度分布。covered call 只怕涨,所以统计只取正向部分的分位。"""
    closes = [m.close_move for m in moves]
    maxes = [m.max_move for m in moves]
    up_closes = [x for x in closes if x > 0]
    up_maxes = [x for x in maxes if x > 0]
    return {
        "n": len(moves),
        "n_up": len(up_closes),
        "up_rate": round(len(up_closes) / len(moves), 4) if moves else None,
        "median_abs_close": percentile([abs(x) for x in closes], 0.5),
        "median_up_close": percentile(up_closes, 0.5),
        "p85_up_max": percentile(up_maxes, 0.85),
        "max_up_max": max(up_maxes) if up_maxes else None,
        "median_up_max": percentile(up_maxes, 0.5),
    }


def breach_rate(moves: list[EarningsMove], otm_pct: float,
                basis: str = "max") -> Optional[dict]:
    """历史击穿率:过去 N 次财报反应里,涨幅 ≥ otm_pct 的次数占比。

    basis="max" 用盘中最高(保守),"close" 用收盘(实际持有到收盘的结果)。
    """
    if not moves:
        return None
    vals = [(m.max_move if basis == "max" else m.close_move) for m in moves]
    hits = [v for v in vals if v >= otm_pct]
    return {"n": len(vals), "breached": len(hits),
            "rate": round(len(hits) / len(vals), 4),
            "worst": max(vals)}


# ---------------------------------------------------------------- CLI


def _table(moves: list[EarningsMove]) -> str:
    lines = ["| 财报日 | 时段 | 反应日 | 前收 | 反应日收 | 收-收 | 盘中最高涨幅 |",
             "|---|---|---|---|---|---|---|"]
    for m in moves:
        lines.append(f"| {m.earnings_date} | {m.when} | {m.reaction_date} "
                     f"| {m.prior_close:.2f} | {m.reaction_close:.2f} "
                     f"| {m.close_move:+.1%} | {m.max_move:+.1%} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(
        description="历史财报涨幅 + 候选 strike 的历史击穿率(跨财报开仓定价用)")
    ap.add_argument("ticker")
    ap.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK,
                    help=f"统计最近几次财报(默认 {DEFAULT_LOOKBACK})")
    ap.add_argument("--strike", type=float, action="append", default=[],
                    help="要评估的候选 strike(可重复)")
    ap.add_argument("--price", type=float, default=None,
                    help="参考现价(缺省取日线最后收盘)")
    args = ap.parse_args(argv)
    ticker = args.ticker.upper()

    from src.brokers.ibkr import IBKRGatewayClient
    from src.config import load_config

    cfg = load_config()
    ib_cfg = dict(cfg.get("ibkr") or {})
    ib_cfg["client_id"] = int(ib_cfg.get("client_id", 11)) + 4
    client = IBKRGatewayClient(ib_cfg)
    try:
        bars = client.daily_bars(ticker, "3 Y")
    except Exception as e:
        print(f"错误: 无法从 IB Gateway 取日线({e})")
        return 1
    finally:
        client.disconnect()
    if not bars:
        print(f"错误: {ticker} 没有日线数据")
        return 1

    events = past_earnings_dates(ticker)[: args.lookback]
    if not events:
        print(f"⚠️ 拿不到 {ticker} 的历史财报日(yfinance 失败或该标的无财报)。"
              f"跨财报定价缺少事实依据 —— 按规则应视为整个窗口有雷,倾向 SKIP。")
        return 1

    moves = compute_moves(bars, events)
    if not moves:
        print(f"错误: {ticker} 的财报日与日线区间无交集(日线 {bars[0][0]}→{bars[-1][0]})")
        return 1

    price = args.price or bars[-1][3]
    s = summarize(moves)
    print(f"# {ticker} 历史财报反应(最近 {s['n']} 次,现价参考 {price:.2f})\n")
    print(_table(moves))
    print(f"\n**上行幅度分布**(covered call 只怕涨,故只统计正向):"
          f"上涨 {s['n_up']}/{s['n']} 次({s['up_rate']:.0%})"
          f" · 收-收中位 {s['median_up_close']:+.1%}" if s["median_up_close"] is not None
          else "\n**上行幅度分布**:历史上没有上涨的财报反应")
    if s["p85_up_max"] is not None:
        print(f" · 盘中最高涨幅:中位 {s['median_up_max']:+.1%},"
              f"p85 {s['p85_up_max']:+.1%},最大 {s['max_up_max']:+.1%}")

    strikes = sorted(args.strike)
    if not strikes:
        # 没给候选就按历史分位反推一个 strike 阶梯,供快速判断
        base = [s["median_up_max"], s["p85_up_max"], s["max_up_max"]]
        strikes = [round(price * (1 + b), 0) for b in base if b is not None]
        print(f"\n(未指定 --strike,按历史中位/p85/最大涨幅反推出的参考价位)")
    print(f"\n**候选 strike 的历史击穿率**(过去 {len(moves)} 次财报里会被打穿几次)\n")
    print("| Strike | 距现价 | 盘中口径击穿 | 收盘口径击穿 |")
    print("|---|---|---|---|")
    for k in strikes:
        otm = k / price - 1
        bm = breach_rate(moves, otm, "max")
        bc = breach_rate(moves, otm, "close")
        print(f"| ${k:g} | {otm:+.1%} | {bm['breached']}/{bm['n']}"
              f"({bm['rate']:.0%}) | {bc['breached']}/{bc['n']}({bc['rate']:.0%}) |")
    print("\n注:历史分布不是概率保证 —— 样本只有几次,而且 IV 已经把市场的预期"
          "波动定价进了权利金。本表回答的是「这个 strike 在历史上扛不扛得住财报」,"
          "不回答「这次会不会破」。")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
