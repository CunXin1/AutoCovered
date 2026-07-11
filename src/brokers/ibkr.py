"""IBKR 主路线:ib_async + TWS/IB Gateway。

- 持仓 + 实时 Greeks(用户有高级行情订阅,market_data_type=1)
- 期权链(roll/开仓候选的数据源)
- 下单:roll 用 BAG combo 限价单(需 paper 账户实测符号约定!)

已知运维特性:Gateway 每日自动重启、每周需重新登录 → watcher 侧有
掉线自检与 fallback 切换,本模块只需诚实抛异常。
"""
from __future__ import annotations

import asyncio
import logging
import math
import sys
from collections import defaultdict
from datetime import date, datetime
from typing import Optional

from src.brokers.base import BrokerClient
from src.engine.roll import ChainQuote
from src.models import Position, ShortCall, StockHolding

log = logging.getLogger(__name__)

if sys.platform == "win32":
    # ib_async 在 Windows 上需要 Selector 事件循环
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _safe(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


class IBKRGatewayClient(BrokerClient):
    name = "ibkr_gateway"
    supports_greeks = True
    supports_trading = True

    def __init__(self, cfg: dict):
        self.host = cfg.get("host", "127.0.0.1")
        self.port = int(cfg.get("port", 7497))
        self.client_id = int(cfg.get("client_id", 11))
        self.account = cfg.get("account", "") or ""
        # 跟踪的账户集合(大写)。留空 = 跟踪所有 managed accounts。
        # 支持 accounts: [U123, U456] 或单个 account: U123。
        accts = list(cfg.get("accounts") or [])
        if self.account:
            accts.append(self.account)
        self.accounts = {str(a).strip().upper() for a in accts if str(a).strip()}
        self.market_data_type = int(cfg.get("market_data_type", 1))
        self.ib = None

    # ------------------------------------------------------------ 连接

    def connect(self) -> None:
        from ib_async import IB

        if self.ib is not None and self.ib.isConnected():
            return
        self.ib = IB()
        self.ib.connect(self.host, self.port, clientId=self.client_id, timeout=15)
        self.ib.reqMarketDataType(self.market_data_type)
        log.info("IBKR 已连接 %s:%s clientId=%s", self.host, self.port, self.client_id)

    def disconnect(self) -> None:
        if self.ib is not None and self.ib.isConnected():
            self.ib.disconnect()

    def _ensure(self):
        self.connect()
        return self.ib

    # ------------------------------------------------------------ 持仓

    def fetch_positions(self, lots: Optional[dict[str, date]] = None) -> list[Position]:
        ib = self._ensure()
        lots = lots or {}

        # ib.positions() 跨所有 managed accounts 返回;portfolio() 在多账户下为空,
        # 故这里用 positions() 并按 (账户, 标的) 分组。现价 positions() 不带,后面 reqTickers 取。
        stocks: dict[tuple[str, str], object] = {}
        calls: dict[tuple[str, str], list] = defaultdict(list)
        for p in ib.positions():
            if self.accounts and str(p.account).upper() not in self.accounts:
                continue
            c = p.contract
            key = (p.account, c.symbol)
            if c.secType == "STK" and p.position > 0:
                stocks[key] = p
            elif c.secType == "OPT" and c.right in ("C", "CALL") and p.position < 0:
                calls[key].append(p)

        # 同一标的出现在多个跟踪账户时,position_id 会撞 key(models.Position 不含账户维度),
        # 这里显式告警,提醒用配置把跟踪范围收敛到单账户。
        seen_syms: dict[str, str] = {}
        for acct, sym in list(stocks.keys()) + list(calls.keys()):
            if sym in seen_syms and seen_syms[sym] != acct:
                log.warning(
                    "标的 %s 同时存在于账户 %s 和 %s;当前 state key 不区分账户,"
                    "建议用 ibkr.accounts 只跟踪其一", sym, seen_syms[sym], acct
                )
            seen_syms.setdefault(sym, acct)

        # 批量拿股票现价(positions() 不带 marketPrice)
        stock_contracts = []
        for sit in stocks.values():
            # positions() 合约的 exchange 是上市所(NASDAQ/NYSE),对其直接请求行情返回
            # NaN;必须用 SMART 聚合器。conId 已锁定标的身份,改 exchange 不影响识别。
            sit.contract.exchange = "SMART"
            stock_contracts.append(sit.contract)
        stock_px: dict[int, float] = {}
        if stock_contracts:
            for t in ib.reqTickers(*ib.qualifyContracts(*stock_contracts)):
                px = _safe(t.marketPrice()) or _safe(t.last) or _safe(t.close)
                if px:
                    stock_px[t.contract.conId] = px

        # 批量拿期权实时数据(delta/iv/mid)
        option_contracts = []
        for items in calls.values():
            for it in items:
                it.contract.exchange = "SMART"  # 同理:用 SMART 聚合器取 greeks/mid
                option_contracts.append(it.contract)
        greeks: dict[int, dict] = {}
        if option_contracts:
            qualified = ib.qualifyContracts(*option_contracts)
            for t in ib.reqTickers(*qualified):
                mg = t.modelGreeks
                bid, ask = _safe(t.bid), _safe(t.ask)
                mid = None
                if bid and ask and bid > 0 and ask > 0:
                    mid = (bid + ask) / 2
                greeks[t.contract.conId] = {
                    "delta": _safe(mg.delta) if mg else None,
                    "iv": _safe(mg.impliedVol) if mg else None,
                    "mid": mid,
                }

        positions: list[Position] = []
        for (acct, sym), sit in stocks.items():
            sym_calls = calls.get((acct, sym), [])
            if sit.position < 100 and not sym_calls:
                continue  # 不足一张 call 的散股且无空头腿,不跟踪

            price = stock_px.get(sit.contract.conId)
            if price is None:
                log.warning("%s(%s)无法获取现价,跳过", sym, acct)
                continue

            stock = StockHolding(
                qty=float(sit.position),
                avg_cost=float(sit.avgCost),
                price=float(price),
                acquired_date=lots.get(sym.upper()),
            )
            if not sym_calls:
                positions.append(Position(ticker=sym, stock=stock))
                continue

            for cit in sym_calls:
                c = cit.contract
                g = greeks.get(c.conId, {})
                # 空头期权的 avgCost 为每张合约的权利金基础(含乘数 100)
                open_premium = float(cit.avgCost) / 100.0
                mid = g.get("mid")
                if mid is None:
                    mid = open_premium  # positions() 无 marketPrice,退回开仓权利金
                positions.append(Position(
                    ticker=sym,
                    stock=stock,
                    call=ShortCall(
                        strike=float(c.strike),
                        expiry=datetime.strptime(
                            c.lastTradeDateOrContractMonth[:8], "%Y%m%d"
                        ).date(),
                        contracts=int(abs(cit.position)),
                        open_premium=open_premium,
                        mid=float(mid),
                        delta=g.get("delta"),
                        iv=g.get("iv"),
                    ),
                ))
        return positions

    # ------------------------------------------------------------ 期权链

    def fetch_chain(
        self, ticker: str, min_dte: int, max_dte: int
    ) -> tuple[float, list[ChainQuote]]:
        from ib_async import Option, Stock

        ib = self._ensure()
        stock = Stock(ticker, "SMART", "USD")
        ib.qualifyContracts(stock)
        [st] = ib.reqTickers(stock)
        price = _safe(st.marketPrice()) or _safe(st.close)
        if not price:
            raise RuntimeError(f"{ticker} 无法获取现价")

        params = ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)
        p = next((x for x in params if x.exchange == "SMART"), params[0])

        today = date.today()
        expiries = []
        for e in sorted(p.expirations):
            d = datetime.strptime(e, "%Y%m%d").date()
            if min_dte <= (d - today).days <= max_dte:
                expiries.append(e)
        # 只看 OTM 到 +25% 的 strike(covered call 只关心这段)
        strikes = [k for k in sorted(p.strikes) if price <= k <= price * 1.25]

        contracts = [
            Option(ticker, e, k, "C", "SMART", tradingClass=p.tradingClass)
            for e in expiries
            for k in strikes
        ]
        quotes: list[ChainQuote] = []
        CHUNK = 50
        for i in range(0, len(contracts), CHUNK):
            batch = ib.qualifyContracts(*contracts[i : i + CHUNK])
            for t in ib.reqTickers(*batch):
                bid, ask = _safe(t.bid) or 0.0, _safe(t.ask) or 0.0
                if bid <= 0 and ask <= 0:
                    continue
                mg = t.modelGreeks
                quotes.append(ChainQuote(
                    expiry=datetime.strptime(
                        t.contract.lastTradeDateOrContractMonth[:8], "%Y%m%d"
                    ).date(),
                    strike=float(t.contract.strike),
                    bid=bid,
                    ask=ask,
                    delta=_safe(mg.delta) if mg else None,
                ))
        return price, quotes

    # ------------------------------------------------------------ 下单

    def place_roll(
        self,
        ticker: str,
        old_strike: float,
        old_expiry: date,
        new_strike: float,
        new_expiry: date,
        contracts: int,
        limit_credit: float,
    ) -> str:
        """Roll = BAG combo:BUY 旧 call(平仓)+ SELL 新 call(开仓),net credit 限价。

        ⚠️ IBKR combo 的限价符号约定(credit 为负限价)必须在 paper 账户实测确认,
        切勿未经 paper 验证直接用于 live。
        """
        from ib_async import ComboLeg, Contract, LimitOrder, Option

        ib = self._ensure()
        old = Option(ticker, old_expiry.strftime("%Y%m%d"), old_strike, "C", "SMART")
        new = Option(ticker, new_expiry.strftime("%Y%m%d"), new_strike, "C", "SMART")
        ib.qualifyContracts(old, new)

        combo = Contract(
            symbol=ticker,
            secType="BAG",
            currency="USD",
            exchange="SMART",
            comboLegs=[
                ComboLeg(conId=old.conId, ratio=1, action="BUY", exchange="SMART"),
                ComboLeg(conId=new.conId, ratio=1, action="SELL", exchange="SMART"),
            ],
        )
        order = LimitOrder("BUY", contracts, -abs(limit_credit))
        trade = ib.placeOrder(combo, order)
        ib.sleep(3)
        status = trade.orderStatus.status
        log.info("roll 订单已提交 %s: %s", ticker, status)
        return (
            f"订单已提交(状态 {status}):BUY {contracts}x combo "
            f"[平 {old_expiry:%m/%d} ${old_strike:g}C / 开 {new_expiry:%m/%d} ${new_strike:g}C] "
            f"限价 net credit ${abs(limit_credit):.2f}"
        )
