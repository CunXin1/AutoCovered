"""SnapTrade 只读持仓源(Schwab 等外部券商)。

两种接入角色(settings.yaml 的 brokers 段):
- **secondary**:与 IBKR 主源并行,每个行情周期把外部券商(Schwab)持仓合入
  监控/告警/晨报。SnapTrade 的 price 是其后台同步快照(非实时,可能滞后
  数小时),watcher 会用主源(IBKR 行情)对这些持仓实时重定价 + 补 Greeks;
  持仓结构(股数/腿/成本)以 SnapTrade 同步为准。
- **fallback**:主源掉线时的降级持仓源(原设计保留;无 Greeks,价格滞后)。

账本与提案的作用域只有主账户:本模块产出的 Position 一律带 account 标记
(如 "schwab"),watcher 据此把它们挡在账本 diff 和 roll 提案之外。

端点:老的 get_user_holdings 已被 SnapTrade 平台下线(410 Gone,2026-07-13
实测),迁移到 get_user_account_positions(股票)+ options.list_option_holdings
(期权)—— SDK 虽标 deprecated 但实测可用;官方指定的最终替代端点明确后再迁。

依赖:pip install snaptrade-python-sdk。
凭证:.env / 环境变量优先于 settings.yaml snaptrade 段(src/config.py 统一合并;
Personal Key(PERS- 前缀)无独立 user,user_id/user_secret 传空串即可)。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from src.brokers.base import BrokerClient
from src.models import Position, ShortCall, StockHolding

log = logging.getLogger(__name__)


def parse_equity(p) -> Optional[tuple[str, StockHolding]]:
    """单条股票持仓 → (ticker, StockHolding)。无效/空头/零股返回 None。纯函数。"""
    sym = (p.get("symbol") or {}).get("symbol") or {}
    raw = sym.get("raw_symbol") or sym.get("symbol") or ""
    try:
        qty = float(p.get("units") or 0)
    except (TypeError, ValueError):
        return None
    if not raw or qty <= 0:
        return None
    return str(raw), StockHolding(
        qty=qty,
        avg_cost=float(p.get("average_purchase_price") or 0),
        price=float(p.get("price") or 0),
    )


def parse_short_call(op) -> Optional[tuple[str, ShortCall]]:
    """单条期权持仓 → (underlying, ShortCall)。只收空头 call,其余返回 None。纯函数。

    字段口径(2026-07-13 真实账户实测):average_purchase_price 为每张合约
    权利金(含 100 乘数)→ /100 得每股;price 已是每股。
    """
    sym = (op.get("symbol") or {}).get("option_symbol") or {}
    if (sym.get("option_type") or "").upper() != "CALL":
        return None
    try:
        qty = float(op.get("units") or 0)
    except (TypeError, ValueError):
        return None
    if qty >= 0:  # 只关心空头 call
        return None
    underlying = ((sym.get("underlying_symbol") or {}).get("symbol")
                  or sym.get("ticker") or "")
    if not underlying:
        return None
    expiry_raw = str(sym.get("expiration_date") or "")
    try:
        expiry = datetime.strptime(expiry_raw[:10], "%Y-%m-%d").date()
    except ValueError:
        log.warning("SnapTrade 期权到期日解析失败: %r", expiry_raw)
        return None
    return str(underlying), ShortCall(
        strike=float(sym.get("strike_price") or 0),
        expiry=expiry,
        contracts=int(abs(qty)),
        open_premium=float(op.get("average_purchase_price") or 0) / 100.0,
        mid=abs(float(op.get("price") or 0)),
        delta=None,  # SnapTrade 无 Greeks;secondary 模式下 watcher 用 IBKR 补
    )


def build_account_positions(
    equities: list,
    options: list,
    account_tag: str,
    lots: Optional[dict[str, date]] = None,
) -> list[Position]:
    """单账户原始持仓 → Position 列表(股票与空头 call 配对)。纯函数,pytest 覆盖。

    规则与 IBKR 侧一致:<100 股且无空头腿的散股不跟踪;有腿无正股(裸 call)
    属异常,记警告不产出(Position 必须有正股)。
    """
    lots = lots or {}
    stocks: dict[str, StockHolding] = {}
    calls: dict[str, list[ShortCall]] = {}

    for p in equities or []:
        parsed = parse_equity(p)
        if parsed is None:
            continue
        ticker, stock = parsed
        stock.acquired_date = lots.get(ticker.upper())
        if ticker in stocks:  # 同账户同标的多行(不常见):数量合并、成本加权
            prev = stocks[ticker]
            total = prev.qty + stock.qty
            stock.avg_cost = ((prev.avg_cost * prev.qty + stock.avg_cost * stock.qty)
                              / total if total else stock.avg_cost)
            stock.qty = total
            stock.price = stock.price or prev.price
        stocks[ticker] = stock

    for op in options or []:
        parsed = parse_short_call(op)
        if parsed is None:
            continue
        underlying, call = parsed
        calls.setdefault(underlying, []).append(call)

    positions: list[Position] = []
    for ticker, stock in stocks.items():
        sym_calls = calls.pop(ticker, [])
        if stock.qty < 100 and not sym_calls:
            continue
        if not sym_calls:
            positions.append(Position(ticker=ticker, stock=stock, account=account_tag))
        else:
            for call in sym_calls:
                positions.append(Position(
                    ticker=ticker, stock=stock, call=call, account=account_tag))
    for ticker in calls:
        log.warning("SnapTrade %s 有空头 call 但无对应正股,跳过(非 covered)", ticker)
    return positions


class SnapTradeClient(BrokerClient):
    name = "snaptrade"
    supports_greeks = False
    supports_trading = False

    def __init__(self, cfg: dict):
        self.cfg = dict(cfg or {})
        self._client = None

    def _ensure(self):
        if self._client is not None:
            return self._client
        missing = [k for k in ("client_id", "consumer_key") if not self.cfg.get(k)]
        if missing:
            raise RuntimeError(
                f"SnapTrade 未配置:缺少 {', '.join(missing)}(.env 或 settings.yaml)")
        from snaptrade_client import SnapTrade

        self._client = SnapTrade(
            client_id=self.cfg["client_id"],
            consumer_key=self.cfg["consumer_key"],
        )
        return self._client

    def _user_kwargs(self) -> dict:
        # Personal key 无独立 user;SDK 类型检查仍要求字段 → 空串(实测有效)
        return {
            "user_id": self.cfg.get("user_id") or "",
            "user_secret": self.cfg.get("user_secret") or "",
        }

    def fetch_positions(self, lots: Optional[dict[str, date]] = None) -> list[Position]:
        client = self._ensure()
        kwargs = self._user_kwargs()

        accounts = client.account_information.list_user_accounts(**kwargs).body or []
        info = {a.get("id"): a for a in accounts}
        account_ids = list(self.cfg.get("account_ids") or []) or list(info.keys())

        positions: list[Position] = []
        for acc_id in account_ids:
            inst = ((info.get(acc_id) or {}).get("institution_name")
                    or "snaptrade").strip().lower() or "snaptrade"
            equities = client.account_information.get_user_account_positions(
                account_id=acc_id, **kwargs).body or []
            options = client.options.list_option_holdings(
                account_id=acc_id, **kwargs).body or []
            positions.extend(build_account_positions(equities, options, inst, lots))
        return positions
