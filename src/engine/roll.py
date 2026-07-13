"""Roll / 开仓候选的确定性计算 — 纯函数。

net credit、年化全部在这里算好,Claude 只做方案叙述对比,不自己算数字。
期权链数据由 brokers 层提供(ChainQuote 列表)。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from src.models import QccConfig, RollConfig


@dataclass
class ChainQuote:
    expiry: date
    strike: float
    bid: float
    ask: float
    delta: Optional[float] = None

    @property
    def mid(self) -> float:
        """bid/ask 都有效时取中价;单边报价时取保守值(bid)。"""
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return max(self.bid, 0.0)


@dataclass
class RollCandidate:
    expiry: date
    strike: float
    dte: int
    delta: Optional[float]
    premium: float                  # 新腿 mid(每股)
    buyback_cost: float             # 旧腿买回成本(每股 mid);开仓模式为 0
    net_credit: float               # premium - buyback_cost
    net_credit_annualized_pct: float
    strike_improvement_pct: float   # 相对旧 strike 的改善;开仓模式为 0
    crosses_earnings: bool = False  # 到期跨财报(引擎只标记不拦截,分析层必须显式定价财报风险)

    def to_dict(self) -> dict:
        return {
            "expiry": self.expiry.isoformat(),
            "strike": self.strike,
            "dte": self.dte,
            "delta": self.delta,
            "premium": round(self.premium, 2),
            "buyback_cost": round(self.buyback_cost, 2),
            "net_credit": round(self.net_credit, 2),
            "net_credit_annualized_pct": round(self.net_credit_annualized_pct, 4),
            "strike_improvement_pct": round(self.strike_improvement_pct, 4),
            "crosses_earnings": self.crosses_earnings,
        }

    def summary(self) -> str:
        d = f"Δ{self.delta:.2f}" if self.delta is not None else "Δ--"
        return (
            f"{self.expiry.strftime('%m/%d')} ${self.strike:g}C ({self.dte}d {d})"
            f" net {'credit' if self.net_credit >= 0 else 'debit'} ${abs(self.net_credit):.2f}"
            f" 年化 {self.net_credit_annualized_pct:.1%}"
            + (" ⚠️跨财报" if self.crosses_earnings else "")
        )


def _annualized(credit: float, price: float, dte: int) -> float:
    if price <= 0 or dte <= 0:
        return 0.0
    return credit / price * 365 / dte


def find_roll_candidates(
    *,
    current_mid: float,
    current_strike: float,
    stock_price: float,
    chain: list[ChainQuote],
    today: date,
    cfg: RollConfig,
    earnings_date: Optional[date] = None,
    top_n: int = 5,
) -> list[RollCandidate]:
    """从期权链筛出合规 roll 候选,按 net credit 年化降序。

    硬过滤:DTE 在目标窗口、strike 必须 OTM(QCC)、delta 不超上限、
    默认只要 net credit(除非配置允许小 debit 换显著 strike 改善)。
    跨财报不拦截:候选照常入集并置 crosses_earnings 标记,
    财报 gap 风险与高 IV 定价由分析层显式权衡。
    """
    out: list[RollCandidate] = []
    for q in chain:
        dte = (q.expiry - today).days
        if not (cfg.target_dte_min <= dte <= cfg.target_dte_max):
            continue
        if q.strike <= stock_price:
            continue
        crosses = earnings_date is not None and today <= earnings_date <= q.expiry
        if q.delta is not None and q.delta > cfg.max_delta:
            continue

        net_credit = q.mid - current_mid
        improvement = (
            (q.strike - current_strike) / current_strike if current_strike > 0 else 0.0
        )
        if net_credit < cfg.min_net_credit:
            debit_ok = (
                cfg.allow_debit_for_improvement
                and net_credit >= -cfg.max_debit_per_share
                and improvement >= cfg.min_strike_improvement_pct
            )
            if not debit_ok:
                continue

        out.append(RollCandidate(
            expiry=q.expiry,
            strike=q.strike,
            dte=dte,
            delta=q.delta,
            premium=q.mid,
            buyback_cost=current_mid,
            net_credit=net_credit,
            net_credit_annualized_pct=_annualized(net_credit, stock_price, dte),
            strike_improvement_pct=improvement,
            crosses_earnings=crosses,
        ))

    out.sort(key=lambda c: c.net_credit_annualized_pct, reverse=True)
    return out[:top_n]


def find_open_candidates(
    *,
    stock_price: float,
    chain: list[ChainQuote],
    today: date,
    qcc: QccConfig,
    earnings_date: Optional[date] = None,
    top_n: int = 5,
) -> list[RollCandidate]:
    """UNCOVERED 持仓的开仓候选:QCC 合规(OTM、DTE>min)且 delta 在目标区间。

    跨财报候选不拦截:与常规候选走同一 delta 区间,置 crosses_earnings 标记,
    财报 gap 风险与高 IV 定价由分析层显式权衡。
    """
    out: list[RollCandidate] = []
    for q in chain:
        dte = (q.expiry - today).days
        if dte < qcc.min_open_dte:
            continue
        if q.strike <= stock_price:
            continue
        if q.delta is None:
            continue   # 开仓必须有 delta(跨不跨财报都一样)
        crosses = earnings_date is not None and today <= earnings_date <= q.expiry
        if not (qcc.target_delta_min <= q.delta <= qcc.target_delta_max):
            continue

        out.append(RollCandidate(
            expiry=q.expiry,
            strike=q.strike,
            dte=dte,
            delta=q.delta,
            premium=q.mid,
            buyback_cost=0.0,
            net_credit=q.mid,
            net_credit_annualized_pct=_annualized(q.mid, stock_price, dte),
            strike_improvement_pct=0.0,
            crosses_earnings=crosses,
        ))

    out.sort(key=lambda c: c.net_credit_annualized_pct, reverse=True)
    return out[:top_n]
