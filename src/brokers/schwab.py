"""Schwab(二期 stub):schwab-py 每日只读快照。

⚠️ 7 天 refresh token 限制:官方不提供延长方式,每周需手动浏览器重新授权
(周报里有续期提醒)。绝不使用 headless 浏览器模拟登录的库(违反 ToS)。
低维护替代:通过 SnapTrade 连 Schwab(见 snaptrade.py)。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from src.brokers.base import BrokerClient
from src.models import Position


class SchwabClient(BrokerClient):
    name = "schwab"
    supports_greeks = False
    supports_trading = False

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def fetch_positions(self, lots: Optional[dict[str, date]] = None) -> list[Position]:
        raise NotImplementedError(
            "Schwab 直连为二期功能(等 developer.schwab.com 应用审批)。"
            "当前请用 SnapTrade 连接 Schwab 账户,或仅监控 IBKR。"
        )
