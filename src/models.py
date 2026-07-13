"""共享数据模型。

state/positions.json 是 Python 层(算数字)和 Claude 层(做叙述)之间的唯一接口:
只有本仓库的 Python 代码可以写它,Claude 会话只读。
所有日期序列化为 ISO 字符串(YYYY-MM-DD)。

本模块只依赖标准库,保证 engine 层可以在最小环境里跑 pytest。
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date
from enum import Enum
from typing import Optional


class PositionState(str, Enum):
    UNCOVERED = "UNCOVERED"      # 持股 >=100 且无 call
    ON_TRACK = "ON_TRACK"        # 正常,静默
    PROFIT_TAKE = "PROFIT_TAKE"  # 权利金已赚回 >= profit_take_pct,可平仓锁定
    MANAGE_DTE = "MANAGE_DTE"    # DTE <= manage_dte(21),进入管理窗口(躲 gamma)
    EXPIRING = "EXPIRING"        # DTE <= expiring_dte 且未平,每日提醒
    OPTION_LOSS = "OPTION_LOSS"  # call 腿 mid >= N x 开仓权利金("权利金翻倍止损"线)
    TESTED = "TESTED"            # 距 strike <= tested_distance_pct 或 delta >= tested_delta
    EVENT_RISK = "EVENT_RISK"    # 到期日横跨财报,或除息前 delta 过高(提前指派风险)
    ROLL_WINDOW = "ROLL_WINDOW"  # delta >= roll_delta 且 DTE 在 roll 窗口内
    BREACHED = "BREACHED"        # 现价 > strike


SEVERITY: dict[PositionState, int] = {
    PositionState.UNCOVERED: 0,
    PositionState.ON_TRACK: 0,
    PositionState.PROFIT_TAKE: 1,
    PositionState.MANAGE_DTE: 1,
    PositionState.EXPIRING: 2,
    PositionState.OPTION_LOSS: 2,
    PositionState.TESTED: 2,
    PositionState.EVENT_RISK: 3,
    PositionState.ROLL_WINDOW: 3,
    PositionState.BREACHED: 4,
}

EMOJI: dict[PositionState, str] = {
    PositionState.UNCOVERED: "⚪",
    PositionState.ON_TRACK: "⚪",
    PositionState.PROFIT_TAKE: "🟢",
    PositionState.MANAGE_DTE: "🟡",
    PositionState.EXPIRING: "🟡",
    PositionState.OPTION_LOSS: "🟡",
    PositionState.TESTED: "🟡",
    PositionState.EVENT_RISK: "🟠",
    PositionState.ROLL_WINDOW: "🟠",
    PositionState.BREACHED: "🔴",
}


# ---------------------------------------------------------------- 阈值配置


def _filtered_kwargs(cls, d: Optional[dict]) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in (d or {}).items() if k in names}


@dataclass(frozen=True)
class AlertConfig:
    profit_take_pct: float = 0.50
    manage_dte: int = 21
    tested_distance_pct: float = 0.03
    tested_delta: float = 0.45
    option_loss_multiple: float = 2.0
    roll_delta: float = 0.60
    roll_dte_min: int = 14
    roll_dte_max: int = 30
    expiring_dte: int = 7
    event_warn_days: int = 3
    exdiv_delta: float = 0.40

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "AlertConfig":
        return cls(**_filtered_kwargs(cls, d))


@dataclass(frozen=True)
class QccConfig:
    """开仓硬规则(简化版内部纪律;完整 IRS QCC 定义见 IRC §1092(c)(4))。

    对"只卖 OTM、开仓 DTE>30"的打法,简化规则是 qualified 的充分条件。
    """

    min_open_dte: int = 31
    target_delta_min: float = 0.20
    target_delta_max: float = 0.30
    coverage_ratio: float = 1.0   # 高波动股部分覆盖(如 NVDA 0.5 = 只卖一半仓位的 call)
    # 跨财报候选不拦截:引擎只置 crosses_earnings 标记,财报风险由分析层定价

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "QccConfig":
        return cls(**_filtered_kwargs(cls, d))


@dataclass(frozen=True)
class RollConfig:
    target_dte_min: int = 30
    target_dte_max: int = 60
    max_delta: float = 0.35
    min_net_credit: float = 0.0
    allow_debit_for_improvement: bool = False
    min_strike_improvement_pct: float = 0.03
    max_debit_per_share: float = 0.50
    # 跨财报候选不拦截(同 QccConfig):只标记,财报风险由分析层定价

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "RollConfig":
        return cls(**_filtered_kwargs(cls, d))


# ---------------------------------------------------------------- 持仓快照


def _iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def parse_date(s) -> Optional[date]:
    if s is None or s == "":
        return None
    if isinstance(s, date):
        return s
    return date.fromisoformat(str(s))


@dataclass
class StockHolding:
    qty: float
    avg_cost: float
    price: float
    acquired_date: Optional[date] = None  # broker API 通常不返回;从 config/lots.yaml 补充

    def to_dict(self) -> dict:
        return {
            "qty": self.qty,
            "avg_cost": self.avg_cost,
            "price": self.price,
            "acquired_date": _iso(self.acquired_date),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StockHolding":
        return cls(
            qty=d["qty"],
            avg_cost=d["avg_cost"],
            price=d["price"],
            acquired_date=parse_date(d.get("acquired_date")),
        )


@dataclass
class ShortCall:
    strike: float
    expiry: date
    contracts: int              # 正数张数
    open_premium: float         # 开仓时每股收到的权利金
    mid: float                  # 当前买回成本(每股 mid)
    delta: Optional[float] = None
    iv: Optional[float] = None
    open_date: Optional[date] = None

    def to_dict(self) -> dict:
        return {
            "strike": self.strike,
            "expiry": _iso(self.expiry),
            "contracts": self.contracts,
            "open_premium": self.open_premium,
            "mid": self.mid,
            "delta": self.delta,
            "iv": self.iv,
            "open_date": _iso(self.open_date),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ShortCall":
        return cls(
            strike=d["strike"],
            expiry=parse_date(d["expiry"]),
            contracts=d["contracts"],
            open_premium=d["open_premium"],
            mid=d["mid"],
            delta=d.get("delta"),
            iv=d.get("iv"),
            open_date=parse_date(d.get("open_date")),
        )


@dataclass
class EventDates:
    earnings: Optional[date] = None
    ex_div: Optional[date] = None

    def to_dict(self) -> dict:
        return {"earnings": _iso(self.earnings), "ex_div": _iso(self.ex_div)}

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "EventDates":
        d = d or {}
        return cls(earnings=parse_date(d.get("earnings")), ex_div=parse_date(d.get("ex_div")))


@dataclass
class Metrics:
    dte: Optional[int] = None
    option_pnl_per_share: Optional[float] = None
    pct_max_profit: Optional[float] = None
    distance_to_strike_pct: Optional[float] = None
    breakeven: Optional[float] = None
    combined_pnl: Optional[float] = None
    annualized_premium_pct: Optional[float] = None
    days_held: Optional[int] = None
    days_to_long_term: Optional[int] = None
    is_long_term: Optional[bool] = None

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Metrics":
        return cls(**_filtered_kwargs(cls, d))


@dataclass
class Position:
    """一个 covered call 单元(正股 + 可选的一条 short call 腿)。"""

    ticker: str
    stock: StockHolding
    call: Optional[ShortCall] = None
    events: EventDates = field(default_factory=EventDates)
    metrics: Metrics = field(default_factory=Metrics)

    @property
    def position_id(self) -> str:
        """状态跟踪用的稳定 key;同一标的多条 call 腿各算一个持仓。"""
        if not self.call:
            return self.ticker
        return f"{self.ticker} {self.call.expiry.strftime('%y%m%d')}C{self.call.strike:g}"

    def to_dict(self) -> dict:
        return {
            "id": self.position_id,
            "ticker": self.ticker,
            "stock": self.stock.to_dict(),
            "call": self.call.to_dict() if self.call else None,
            "events": self.events.to_dict(),
            "metrics": self.metrics.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        return cls(
            ticker=d["ticker"],
            stock=StockHolding.from_dict(d["stock"]),
            call=ShortCall.from_dict(d["call"]) if d.get("call") else None,
            events=EventDates.from_dict(d.get("events")),
            metrics=Metrics.from_dict(d.get("metrics")),
        )


@dataclass
class StateResult:
    state: PositionState
    flags: list[PositionState]
    reasons: list[str]

    @property
    def severity(self) -> int:
        return SEVERITY[self.state]

    @property
    def emoji(self) -> str:
        return EMOJI[self.state]
