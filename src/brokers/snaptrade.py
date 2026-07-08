"""SnapTrade 备胎:两家券商(IBKR + Schwab)统一只读持仓快照。

定位(见 ibkr & schwab api.md):
- IBKR 侧只读(底层 Flex Query),数据非实时、无 Greeks → 不能支撑 5 分钟
  Greeks 循环,但在 Gateway 掉线时让状态机降级运行(价格类规则仍有效)。
- 免去 Schwab 7 天 token 和 Gateway 保活的运维负担。

依赖:pip install snaptrade-python-sdk;配置见 settings.yaml 的 snaptrade 段。
⚠️ 字段映射基于 SnapTrade API 文档编写,需真实账户联调验证。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from src.brokers.base import BrokerClient
from src.models import Position, ShortCall, StockHolding

log = logging.getLogger(__name__)


class SnapTradeClient(BrokerClient):
    name = "snaptrade"
    supports_greeks = False
    supports_trading = False

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._client = None

    def _ensure(self):
        if self._client is not None:
            return self._client
        missing = [k for k in ("client_id", "consumer_key", "user_id", "user_secret") if not self.cfg.get(k)]
        if missing:
            raise RuntimeError(f"SnapTrade 未配置:缺少 {', '.join(missing)}(见 settings.yaml)")
        from snaptrade_client import SnapTrade

        self._client = SnapTrade(
            client_id=self.cfg["client_id"],
            consumer_key=self.cfg["consumer_key"],
        )
        return self._client

    def fetch_positions(self, lots: Optional[dict[str, date]] = None) -> list[Position]:
        client = self._ensure()
        lots = lots or {}
        kwargs = dict(
            user_id=self.cfg["user_id"],
            user_secret=self.cfg["user_secret"],
        )
        account_ids = self.cfg.get("account_ids") or []
        if not account_ids:
            accounts = client.account_information.list_user_accounts(**kwargs).body
            account_ids = [a["id"] for a in accounts]

        stocks: dict[str, StockHolding] = {}
        calls: dict[str, list[ShortCall]] = {}
        for acc_id in account_ids:
            holdings = client.account_information.get_user_holdings(
                account_id=acc_id, **kwargs
            ).body

            for p in holdings.get("positions") or []:
                sym = (p.get("symbol") or {}).get("symbol") or {}
                raw = sym.get("raw_symbol") or sym.get("symbol") or ""
                qty = float(p.get("units") or 0)
                if not raw or qty <= 0:
                    continue
                price = float(p.get("price") or 0)
                avg = float(p.get("average_purchase_price") or 0)
                if raw in stocks:  # 多账户合并
                    prev = stocks[raw]
                    total = prev.qty + qty
                    avg = (prev.avg_cost * prev.qty + avg * qty) / total if total else avg
                    stocks[raw] = StockHolding(total, avg, price or prev.price,
                                               lots.get(raw.upper()))
                else:
                    stocks[raw] = StockHolding(qty, avg, price, lots.get(raw.upper()))

            for op in holdings.get("option_positions") or []:
                sym = (op.get("symbol") or {}).get("option_symbol") or {}
                if (sym.get("option_type") or "").upper() != "CALL":
                    continue
                qty = float(op.get("units") or 0)
                if qty >= 0:  # 只关心空头 call
                    continue
                underlying = ((sym.get("underlying_symbol") or {}).get("symbol")
                              or sym.get("ticker") or "")
                expiry_raw = sym.get("expiration_date") or ""
                try:
                    expiry = datetime.strptime(expiry_raw[:10], "%Y-%m-%d").date()
                except ValueError:
                    log.warning("SnapTrade 期权到期日解析失败: %r", expiry_raw)
                    continue
                calls.setdefault(underlying, []).append(ShortCall(
                    strike=float(sym.get("strike_price") or 0),
                    expiry=expiry,
                    contracts=int(abs(qty)),
                    open_premium=float(op.get("average_purchase_price") or 0) / 100.0,
                    mid=abs(float(op.get("price") or 0)),
                    delta=None,  # SnapTrade 无 Greeks → 状态机自动降级
                ))

        positions: list[Position] = []
        for ticker, stock in stocks.items():
            sym_calls = calls.pop(ticker, [])
            if stock.qty < 100 and not sym_calls:
                continue
            if not sym_calls:
                positions.append(Position(ticker=ticker, stock=stock))
            else:
                for call in sym_calls:
                    positions.append(Position(ticker=ticker, stock=stock, call=call))
        return positions
