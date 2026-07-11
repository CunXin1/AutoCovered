"""Broker 抽象接口 — 可插拔设计。

Provider 分工(见 ibkr & schwab api.md 调研):
- ibkr_gateway(ib_async):主路线,实时 Greeks + 期权链 + 下单
- snaptrade:韧性备胎,两家券商统一只读持仓快照(无 Greeks,不能下单 IBKR)
- schwab(schwab-py):二期,每日只读快照(7 天 refresh token)
- ibind(IBKR Web API):留接口未实现,迁 headless Mac 后免 Gateway 的选项
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Optional

from src.engine.roll import ChainQuote
from src.models import Position


class BrokerClient(ABC):
    """所有 provider 的公共接口。不支持的能力抛 NotImplementedError,
    watcher 会据此降级(比如备源没有 Greeks 时跳过 delta 类规则)。"""

    name: str = "base"
    supports_greeks: bool = False
    supports_trading: bool = False

    def connect(self) -> None:  # 默认无长连接
        pass

    def disconnect(self) -> None:
        pass

    @abstractmethod
    def fetch_positions(self, lots: Optional[dict[str, date]] = None) -> list[Position]:
        """拉取持仓并配对成 covered call 单元。lots 提供建仓日期(税务)。"""

    def fetch_chain(
        self, ticker: str, min_dte: int, max_dte: int
    ) -> tuple[float, list[ChainQuote]]:
        """返回 (现价, 期权链报价列表)。只有实时 provider 支持。"""
        raise NotImplementedError(f"{self.name} 不支持期权链查询")

    def fetch_executions(self) -> list:
        """当日期权成交回报(账本对账主源)。不支持的 provider 返回空列表,
        watcher 会自动降级为纯持仓 diff 推断。"""
        return []

    def fetch_daily_close(self, ticker: str, d: date) -> Optional[float]:
        """d 当日官方收盘价(到期判定用)。不支持返回 None → 到期记 unknown。"""
        return None

    def quote_option(self, ticker: str, strike: float, expiry: date) -> dict:
        """单合约实时报价(批准执行前二次校验)。只有实时 provider 支持。"""
        raise NotImplementedError(f"{self.name} 不支持期权报价")

    def fetch_open_order_refs(self) -> set[str]:
        """在途订单 orderRef 集合(提案终态跟踪)。不支持返回空集合。"""
        return set()

    def place_open_call(
        self,
        ticker: str,
        strike: float,
        expiry: date,
        contracts: int,
        limit_price: float,
        order_ref: str = "",
    ) -> str:
        """卖出开仓 covered call 限价单。只有交易 provider 支持。"""
        raise NotImplementedError(f"{self.name} 不支持下单")

    def place_roll(
        self,
        ticker: str,
        old_strike: float,
        old_expiry: date,
        new_strike: float,
        new_expiry: date,
        contracts: int,
        limit_credit: float,
        order_ref: str = "",
    ) -> str:
        """提交 roll 组合限价单,返回订单状态描述。只有交易 provider 支持。"""
        raise NotImplementedError(f"{self.name} 不支持下单")


def build_broker(cfg: dict, kind: str) -> BrokerClient:
    """按配置构造 provider。kind: ibkr_gateway | snaptrade | schwab"""
    if kind == "ibkr_gateway":
        from src.brokers.ibkr import IBKRGatewayClient

        return IBKRGatewayClient(cfg.get("ibkr") or {})
    if kind == "snaptrade":
        from src.brokers.snaptrade import SnapTradeClient

        return SnapTradeClient(cfg.get("snaptrade") or {})
    if kind == "schwab":
        from src.brokers.schwab import SchwabClient

        return SchwabClient(cfg.get("schwab") or {})
    raise ValueError(f"未知 broker provider: {kind}")
