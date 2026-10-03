"""IBKR 主路线:ib_async + TWS/IB Gateway。

- 持仓 + 实时 Greeks(用户有高级行情订阅,market_data_type=1)
- 期权链(roll/开仓候选的数据源)
- 下单:roll 用 BAG combo 限价单(需 paper 账户实测符号约定!)

已知运维特性:Gateway 每日自动重启、每周需重新登录 → watcher 侧有
掉线自检与 fallback 切换,本模块只需诚实抛异常。
"""
from __future__ import annotations

import asyncio
import functools
import logging
import math
import sys
from collections import defaultdict
from datetime import date, datetime
from typing import Optional

from src.brokers.base import BrokerClient
from src.config import lot_date
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


# 连接类故障:socket 断开、API 不响应(有界超时触发)。ib_async 在未连接时
# 发请求抛 ConnectionError("Not connected");僵尸连接表现为请求超时。
_CONN_ERRORS = (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError)


def _heals_connection(fn):
    """请求级僵尸自愈:连接类故障时拆掉连接重建,并重试一次。

    僵尸态("socket 在但 API 不响应")只能由真实请求暴露 —— 探针式健康检查
    在 ib_async 上会误报(见 _alive 的注释),所以自愈点放在这里而不是探针里。

    ⚠️ 只用于只读请求。下单方法绝不加:连接故障时重试 = 可能重复下单。
    """

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except _CONN_ERRORS as e:
            log.warning("%s 连接类故障(%s: %s),重建连接后重试一次",
                        fn.__name__, type(e).__name__, e or "(无消息)")
            self._teardown()
            return fn(self, *args, **kwargs)

    return wrapper


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
    #
    # Gateway 每日自动重启后,连接可能进入"socket 在但 API 不响应"的僵尸态。
    # 应对:
    # 1. 复用前只做零往返的本地状态检查(_alive);不发探针请求
    # 2. 真僵尸由只读请求的 @_heals_connection 兜底:故障 → 拆重建 → 重试一次
    # 3. 撞 client id(旧会话未释放,IB 错误 326)→ +100 轮换重试,
    #    不会落在 11-14 的保留段上

    CONNECT_ATTEMPTS = 3

    def connect(self) -> None:
        from ib_async import IB

        if self.ib is not None:
            if self._alive():
                return
            log.warning("IBKR 连接僵尸/已断,拆除重建")
            self._teardown()

        last_err: Exception = RuntimeError("IBKR 连接失败")
        for attempt in range(self.CONNECT_ATTEMPTS):
            cid = self.client_id + attempt * 100
            ib = IB()
            ib.RequestTimeout = 20   # 有界等待,防僵尸连接把请求挂死
            try:
                ib.connect(self.host, self.port, clientId=cid, timeout=15)
                ib.reqMarketDataType(self.market_data_type)
                self.ib = ib
                log.info("IBKR 已连接 %s:%s clientId=%s", self.host, self.port, cid)
                return
            except Exception as e:
                last_err = e
                try:
                    ib.disconnect()
                except Exception:
                    pass
                log.warning("IBKR 连接失败(clientId=%s,第 %d/%d 次): %s",
                            cid, attempt + 1, self.CONNECT_ATTEMPTS, e)
        raise last_err

    def _alive(self) -> bool:
        """本地状态检查,零往返。

        这里曾经发 reqCurrentTime() 做"API 真在响应"的探测 —— 2026-10 实测
        证明那是错的:健康连接上连发该请求会自相竞争 currentTime future,
        间歇抛 TimeoutError(isConnected() 全程 True)。由于每个 broker 方法
        都经 _ensure() → connect() → _alive(),误报会把好连接拆掉重建,重连
        换 clientId(+100 轮换)而旧会话尚未释放 → 撞 id → 判"连接失败"。
        2026-07-16 那次 clientId=214 的 3/3 连接失败级联就是这么来的。
        """
        return self.ib is not None and self.ib.isConnected()

    def _teardown(self) -> None:
        if self.ib is not None:
            try:
                self.ib.disconnect()
            except Exception:
                pass
        self.ib = None

    def disconnect(self) -> None:
        self._teardown()

    def _ensure(self):
        self.connect()
        return self.ib

    def _last_daily_close(self, contract) -> Optional[float]:
        """最近一根日线收盘价。

        盘后/frozen 行情下 reqTickers 的快照经常在等待窗口内只给 NaN(TWS 不报错,
        且每轮失败的标的随机),而日线历史数据稳定秒回。收盘后"现价"取官方日线
        收盘价本身也是正确口径,所以这是取价链的最后一级,而不是放弃。
        """
        try:
            bars = self.ib.reqHistoricalData(
                contract, endDateTime="", durationStr="5 D",
                barSizeSetting="1 day", whatToShow="TRADES", useRTH=True)
        except Exception as e:
            log.warning("%s 日线兜底取价失败: %s", getattr(contract, "symbol", "?"), e)
            return None
        return _safe(bars[-1].close) if bars else None

    # ------------------------------------------------------------ 持仓

    @_heals_connection
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

        # 批量拿股票现价(positions() 不带 marketPrice)
        stock_contracts = []
        for sit in stocks.values():
            # positions() 合约的 exchange 是上市所(NASDAQ/NYSE),对其直接请求行情返回
            # NaN;必须用 SMART 聚合器。conId 已锁定标的身份,改 exchange 不影响识别。
            sit.contract.exchange = "SMART"
            stock_contracts.append(sit.contract)
        stock_px: dict[int, float] = {}
        if stock_contracts:
            qualified = [c for c in ib.qualifyContracts(*stock_contracts) if c]
            for t in ib.reqTickers(*qualified):
                px = _safe(t.marketPrice()) or _safe(t.last) or _safe(t.close)
                if px:
                    stock_px[t.contract.conId] = px
            # 批量 reqTickers 在盘后/frozen 行情下常有几个标的拿不到快照(实测
            # 2026-10-02 盘后 7 个标的里 4 个全 NaN,单独请求则都有)。没有现价的
            # 持仓会被整条跳过 —— 宁可多走一趟,也不要静默少监控一个仓位。
            for c in [c for c in qualified if c.conId not in stock_px]:
                px = None
                try:
                    [t] = ib.reqTickers(c)
                    px = _safe(t.marketPrice()) or _safe(t.last) or _safe(t.close)
                except Exception as e:
                    log.warning("%s 单独取价失败: %s", c.symbol, e)
                if px:
                    stock_px[c.conId] = px
                    log.info("%s 批量取价为空,单独快照拿到 %.2f", c.symbol, px)
                    continue
                px = self._last_daily_close(c)
                if px:
                    stock_px[c.conId] = px
                    log.info("%s 快照无数据,取官方日线收盘价 %.2f", c.symbol, px)

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
                acquired_date=lot_date(lots, sym, acct),
            )
            if not sym_calls:
                positions.append(Position(ticker=sym, stock=stock, account=acct))
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
                    account=acct,
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

    @_heals_connection
    def fetch_chain(
        self, ticker: str, min_dte: int, max_dte: int,
        max_strikes_per_expiry: int = 80
    ) -> tuple[float, list[ChainQuote]]:
        from ib_async import Option, Stock

        ib = self._ensure()
        stock = Stock(ticker, "SMART", "USD")
        ib.qualifyContracts(stock)
        [st] = ib.reqTickers(stock)
        # 与 fetch_positions 同一套兜底:盘后快照常只给 NaN(且每轮失败的标的随机),
        # 没有现价整个期权链就拉不出来 —— 收盘后用官方日线收盘价才是对的口径
        price = _safe(st.marketPrice()) or _safe(st.last) or _safe(st.close)
        if not price:
            price = self._last_daily_close(stock)
            if price:
                log.info("%s 快照无现价,期权链改用官方日线收盘价 %.2f", ticker, price)
        if not price:
            raise RuntimeError(f"{ticker} 无法获取现价")

        params = ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)
        # SMART 下可能有多个 tradingClass(公司行动会产生 2MSFT 这类调整合约,
        # strike 稀疏且多半 qualify 失败);标准合约的 tradingClass == ticker,优先取
        smart = [x for x in params if x.exchange == "SMART"]
        p = next((x for x in smart if x.tradingClass == ticker),
                 smart[0] if smart else params[0])

        today = date.today()
        expiries = []
        for e in sorted(p.expirations):
            d = datetime.strptime(e, "%Y%m%d").date()
            if min_dte <= (d - today).days <= max_dte:
                expiries.append(e)
        # 全部 OTM strike,不设距离上限。这里曾经砍在 +25%,那是把策略判断写进了
        # 数据层:高 IV 标的上 delta 0.08–0.15 的合法 strike 本来就在 25% 之外,
        # 结果 NOW/CRWV 返回"没有候选",其实是"没扫到"(实测 2026-10-02)。
        # 筛选交给引擎的 delta 区间,数据层只负责把链取全。
        # max_strikes_per_expiry 纯粹是请求量上限(靠近现价优先),不含策略含义。
        otm = [k for k in sorted(p.strikes) if k >= price]
        strikes = sorted(otm[:max_strikes_per_expiry])

        contracts = [
            Option(ticker, e, k, "C", "SMART", tradingClass=p.tradingClass)
            for e in expiries
            for k in strikes
        ]
        quotes: list[ChainQuote] = []
        CHUNK = 50
        for i in range(0, len(contracts), CHUNK):
            # qualifyContracts 对不存在的 strike/expiry 组合返回 None(会打 "Unknown
            # contract" 日志);必须滤掉,否则 None 传进 reqTickers → reqMktData 崩溃。
            batch = [c for c in ib.qualifyContracts(*contracts[i : i + CHUNK]) if c]
            if not batch:
                continue
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

    # ------------------------------------------------------------ 成交与历史

    @_heals_connection
    def fetch_executions(self):
        """当日全账户期权成交(所有 client + TWS 手动单),含精确佣金。

        orderRef = 本系统提案 id(下单时写入),空 = 手动单。
        covered call 账户按 side 映射方向:SLD=开仓卖出,BOT=买回平仓。
        """
        from ib_async import ExecutionFilter

        from src.engine.lifecycle import ExecutionRecord

        ib = self._ensure()
        out: list[ExecutionRecord] = []
        for f in ib.reqExecutions(ExecutionFilter()):
            c = f.contract
            if c.secType != "OPT" or c.right not in ("C", "CALL"):
                continue   # BAG 汇总行/正股/put 不入期权账本
            ex = f.execution
            if self.accounts and str(ex.acctNumber).upper() not in self.accounts:
                continue
            commission = 0.0
            if f.commissionReport is not None:
                commission = _safe(f.commissionReport.commission) or 0.0
            ts = ex.time.isoformat() if hasattr(ex.time, "isoformat") else str(ex.time)
            out.append(ExecutionRecord(
                exec_id=ex.execId,
                ts=ts,
                ticker=c.symbol,
                action="SELL_TO_OPEN" if ex.side == "SLD" else "BUY_TO_CLOSE",
                strike=float(c.strike),
                expiry=datetime.strptime(
                    c.lastTradeDateOrContractMonth[:8], "%Y%m%d").date(),
                contracts=int(ex.shares),
                price=float(ex.price),
                fees=commission,
                order_ref=ex.orderRef or "",
                account=str(ex.acctNumber or ""),
            ))
        return out

    @_heals_connection
    def fetch_daily_close(self, ticker: str, d: date) -> Optional[float]:
        """d 当日官方日线收盘价(到期 expired/assigned 判定用;当日无 bar 返回 None)。"""
        from ib_async import Stock

        ib = self._ensure()
        stock = Stock(ticker, "SMART", "USD")
        ib.qualifyContracts(stock)
        bars = ib.reqHistoricalData(
            stock,
            endDateTime=f"{d.strftime('%Y%m%d')} 23:59:59 US/Eastern",
            durationStr="5 D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
        )
        for bar in reversed(bars or []):
            bar_date = bar.date if isinstance(bar.date, date) else bar.date.date()
            if bar_date == d:
                return _safe(bar.close)
        return None

    @_heals_connection
    def quote_option(self, ticker: str, strike: float, expiry: date) -> dict:
        """单合约实时报价(批准执行前的二次校验用)。

        返回 {bid, ask, mid, delta, stock_price},拿不到的字段为 None。
        """
        from ib_async import Option, Stock

        ib = self._ensure()
        stock = Stock(ticker, "SMART", "USD")
        opt = Option(ticker, expiry.strftime("%Y%m%d"), strike, "C", "SMART")
        ib.qualifyContracts(stock, opt)
        st, ot = ib.reqTickers(stock, opt)
        bid, ask = _safe(ot.bid), _safe(ot.ask)
        mid = (bid + ask) / 2 if (bid and ask and bid > 0 and ask > 0) else None
        mg = ot.modelGreeks
        return {
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "delta": _safe(mg.delta) if mg else None,
            "iv": _safe(mg.impliedVol) if mg else None,
            "stock_price": _safe(st.marketPrice()) or _safe(st.close),
        }

    @_heals_connection
    def vol_history(self, ticker: str, what: str = "OPTION_IMPLIED_VOLATILITY",
                    duration: str = "1 Y") -> list[tuple]:
        """正股的波动率日线 [(date, close)]。

        what="OPTION_IMPLIED_VOLATILITY" 给期权隐含波动率序列(IV Rank/百分位的原料),
        "HISTORICAL_VOLATILITY" 给已实现波动率(算 IV/HV 比)。
        注意:这两个 whatToShow 只对**正股**合约有效 —— 对单个期权合约请求会返回
        Error 162(实测 2026-10-02),所以 IV 分位只能在标的层面算。
        """
        from ib_async import Stock

        ib = self._ensure()
        stock = Stock(ticker, "SMART", "USD")
        if not [c for c in ib.qualifyContracts(stock) if c]:
            return []
        bars = ib.reqHistoricalData(
            stock, endDateTime="", durationStr=duration, barSizeSetting="1 day",
            whatToShow=what, useRTH=True)
        out = []
        for b in bars or []:
            d = b.date if isinstance(b.date, date) else b.date.date()
            v = _safe(b.close)
            if v:
                out.append((d, v))
        return out

    @_heals_connection
    def daily_bars(self, ticker: str, duration: str = "3 Y") -> list[tuple]:
        """正股日线序列 [(date, high, low, close)],一次请求覆盖多年。

        逐个事件去拉短区间会撞 IBKR 历史数据限速(约 60 次/10 分钟),
        所以一次取长序列,事件窗口在本地切。
        """
        from ib_async import Stock

        ib = self._ensure()
        stock = Stock(ticker, "SMART", "USD")
        if not [c for c in ib.qualifyContracts(stock) if c]:
            return []
        bars = ib.reqHistoricalData(
            stock, endDateTime="", durationStr=duration, barSizeSetting="1 day",
            whatToShow="TRADES", useRTH=True)
        out = []
        for b in bars or []:
            d = b.date if isinstance(b.date, date) else b.date.date()
            out.append((d, _safe(b.high), _safe(b.low), _safe(b.close)))
        return out

    @_heals_connection
    def quote_option_rth(self, ticker: str, strike: float, expiry: date, *,
                         window_min: int = 60,
                         exclude_last_min: int = 5) -> Optional[dict]:
        """用 RTH 内收盘前一段窗口的报价给出可信 bid/ask(盘后研究的正确口径)。

        盘后 16:00 ET 之后没有做市商报价,frozen 快照是收盘残留的簿子状态,
        点差可能虚高到 90%(实测 2026-10-02 盘后 IBM/AAPL 多个合约),拿它判断
        流动性会把本来好的合约误杀。历史 BID_ASK 分钟线则是真实交易时段的报价。

        BID_ASK 线字段语义(2026-10-02 实测):
        open = 窗口内时间加权平均 bid,close = 平均 ask,low = 最低 bid,high = 最高 ask。

        取窗口中位数而不是最后一根:收盘前几分钟点差会拉宽,还混着收盘竞价噪音
        (同一合约末根 3.35/3.45,而收盘前 60 分钟窗口中位 3.46/3.55)。
        """
        import statistics
        from datetime import timedelta

        from ib_async import Option

        ib = self._ensure()
        opt = Option(ticker, expiry.strftime("%Y%m%d"), strike, "C", "SMART")
        if not [c for c in ib.qualifyContracts(opt) if c]:
            return None
        bars = ib.reqHistoricalData(
            opt, endDateTime="", durationStr="1 D", barSizeSetting="5 mins",
            whatToShow="BID_ASK", useRTH=True)
        if not bars:
            return None
        last = bars[-1].date
        lo = last - timedelta(minutes=window_min)
        hi = last - timedelta(minutes=exclude_last_min)
        sel = [b for b in bars if lo <= b.date <= hi] or bars[-1:]
        bid = statistics.median(b.open for b in sel)
        ask = statistics.median(b.close for b in sel)
        if not (bid > 0 and ask > 0):
            return None
        mid = (bid + ask) / 2
        return {
            "bid": round(bid, 4),
            "ask": round(ask, 4),
            "mid": round(mid, 4),
            "spread_pct": round((ask - bid) / mid, 4) if mid else None,
            "bars": len(sel),
            "window": f"{sel[0].date:%H:%M}–{sel[-1].date:%H:%M} ET {sel[0].date:%Y-%m-%d}",
        }

    @_heals_connection
    def quote_stock(self, ticker: str) -> Optional[float]:
        """正股实时现价(次级持仓源无腿标的的重定价)。"""
        from ib_async import Stock

        ib = self._ensure()
        stock = Stock(ticker, "SMART", "USD")
        ib.qualifyContracts(stock)
        [t] = ib.reqTickers(stock)
        px = _safe(t.marketPrice()) or _safe(t.last) or _safe(t.close)
        return px if px else self._last_daily_close(stock)

    # ------------------------------------------------------------ 下单

    def _require_order_account(self, account: str) -> None:
        """多账户登录下不许省略下单账户。

        TWS 在多账户会话里收到不带 account 的单会自己挑一个(或直接报错 321)。
        挑错账户 = 那个账户没有对应正股 = 裸卖空头 call,理论无限风险。
        """
        if account:
            return
        managed = [a for a in (self.ib.managedAccounts() if self.ib else []) if a]
        if len(managed) > 1:
            raise RuntimeError(
                f"多账户登录({len(managed)} 个)下必须指定下单账户:"
                f"不指定则 TWS 自行挑选,可能下到无正股的账户变成裸卖。"
                f"提案需带 account 字段。")

    def place_open_call(
        self,
        ticker: str,
        strike: float,
        expiry: date,
        contracts: int,
        limit_price: float,
        order_ref: str = "",
        account: str = "",
    ) -> str:
        """卖出开仓单腿 covered call:SELL 限价单,DAY 有效(禁 GTC,
        订单不许活得比提案治理长)。orderRef=提案 id,成交回报自动归因。

        ⚠️ 与 place_roll 同理:live 使用前必须 paper 账户实测。
        """
        from ib_async import LimitOrder, Option

        ib = self._ensure()
        self._require_order_account(account)
        opt = Option(ticker, expiry.strftime("%Y%m%d"), strike, "C", "SMART")
        ib.qualifyContracts(opt)
        order = LimitOrder("SELL", contracts, round(limit_price, 2),
                           tif="DAY", orderRef=order_ref, account=account)
        trade = ib.placeOrder(opt, order)
        ib.sleep(3)
        status = trade.orderStatus.status
        log.info("开仓订单已提交 %s: %s", ticker, status)
        return (
            f"订单已提交(状态 {status}):SELL {contracts}x "
            f"{expiry:%m/%d} ${strike:g}C 限价 ${limit_price:.2f}(DAY)"
        )

    @_heals_connection
    def fetch_open_order_refs(self) -> set[str]:
        """当前在途订单的 orderRef 集合(提案终态跟踪:不在途且未全成交 = 已取消)。"""
        ib = self._ensure()
        ib.reqAllOpenOrders()
        ib.sleep(1)
        return {t.order.orderRef for t in ib.openTrades() if t.order.orderRef}

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
        account: str = "",
    ) -> str:
        """Roll = BAG combo:BUY 旧 call(平仓)+ SELL 新 call(开仓),net credit 限价。

        ⚠️ IBKR combo 的限价符号约定(credit 为负限价)必须在 paper 账户实测确认,
        切勿未经 paper 验证直接用于 live。
        """
        from ib_async import ComboLeg, Contract, LimitOrder, Option

        ib = self._ensure()
        self._require_order_account(account)
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
        order = LimitOrder("BUY", contracts, -abs(limit_credit),
                           tif="DAY", orderRef=order_ref, account=account)
        trade = ib.placeOrder(combo, order)
        ib.sleep(3)
        status = trade.orderStatus.status
        log.info("roll 订单已提交 %s: %s", ticker, status)
        return (
            f"订单已提交(状态 {status}):BUY {contracts}x combo "
            f"[平 {old_expiry:%m/%d} ${old_strike:g}C / 开 {new_expiry:%m/%d} ${new_strike:g}C] "
            f"限价 net credit ${abs(limit_credit):.2f}"
        )
