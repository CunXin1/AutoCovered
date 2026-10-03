"""IBKR 报表对账补录 —— 把券商侧的历史成交事实写进账本。

为什么需要它:TWS API 的 reqExecutions 按设计只返回当日成交。watcher 停机
期间(或账本启用前)发生的开仓/买回/到期/指派,API 事后一律拿不到,只能从
IBKR 报表侧取。本模块读 Flex 报表,转成账本事件,幂等写入。

数据质量:Flex 行带精确成交价与佣金 → price_quality=exact,
与持仓 diff 推断的 inferred 价格在 stats 里分层展示,不混为一谈。

分层纪律(会算错钱的那条):Trades 段里同一笔成交按 levelOfDetail 在
ASSET_CLASS / ORDER / EXECUTION / CLOSED_LOT 各出现一次。入账只认 EXECUTION,
叠加统计就是把成交和盈亏重复计算数倍。

写者纪律:账本的常规写者只有 daemon watcher 与 executor(见 CLAUDE.md)。
本模块是按需的第三写者,因此:
- 默认只打印计划,必须显式 --apply 才落库
- 运行前检测 daemon watcher 是否在跑,在跑则拒绝(避免并发写同一 round)

用法:
    python -m src.reconcile --flex                      # 用 .env 里的 token/query
    python -m src.reconcile --file ~/Downloads/flex.xml # 已下载的 Flex 报表
    python -m src.reconcile --file flex.xml --apply     # 确认计划后落库
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from src.config import STATE_DIR, env_value
from src.engine.lifecycle import (
    ASSIGN,
    BUY_TO_CLOSE,
    EXPIRE,
    OUT_ASSIGNED,
    OUT_EXPIRED,
    OUT_ROLLED,
    SELL_TO_OPEN,
    CallKey,
    TradeEvent,
)
from src.ledger import Ledger

log = logging.getLogger(__name__)

SOURCE = "flex"

# Flex notes/code 字段:Ep=到期作废,A=被指派,Ex=行权。
# 对空头 call 而言 A/Ex 都意味着股票被叫走。
CODE_EXPIRE = "Ep"
CODES_ASSIGN = ("A", "Ex")


@dataclass
class FlexRow:
    """Flex Trades 段的一行,归一化后的样子(字段名沿用 Flex 的语义)。"""

    account: str
    ticker: str            # underlyingSymbol
    strike: float
    expiry: Optional[date]   # 正股行没有;classify() 据此排除
    buy_sell: str          # BUY | SELL
    open_close: str        # O | C | ""
    quantity: float        # 有符号:卖出为负
    price: float           # 每股
    commission: float      # Flex 给负数(成本)
    trade_id: str
    trade_date: Optional[date]
    ts: str                # UTC ISO
    codes: tuple[str, ...]
    put_call: str
    asset_category: str
    level_of_detail: str   # ASSET_CLASS | ORDER | EXECUTION | CLOSED_LOT | ""


# ---------------------------------------------------------------- 解析


def _f(v, default=0.0) -> float:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _parse_date(v) -> Optional[date]:
    s = (str(v or "")).strip()
    if not s:
        return None
    s = s.split(";")[0].split(" ")[0]
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_ts(date_time, trade_date) -> str:
    """Flex dateTime 可能是 '20260814;153000' / '2026-08-14 15:30:00' / 空。

    拿不到时点就用交易日 20:00 UTC(与 lifecycle 对到期类事件的约定一致)。
    """
    raw = str(date_time or "").strip()
    m = re.match(r"^(\d{8}|\d{4}-\d{2}-\d{2})[;, ]+(\d{2}):?(\d{2}):?(\d{2})$", raw)
    if m:
        d = _parse_date(m.group(1))
        if d:
            return (datetime(d.year, d.month, d.day, int(m.group(2)),
                             int(m.group(3)), int(m.group(4)),
                             tzinfo=timezone.utc).isoformat(timespec="seconds"))
    d = _parse_date(raw) or trade_date
    if d:
        return f"{d.isoformat()}T20:00:00+00:00"
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _codes(d: dict) -> tuple[str, ...]:
    raw = str(d.get("notes") or d.get("code") or d.get("Code") or "")
    return tuple(c.strip() for c in re.split(r"[;,|/]", raw) if c.strip())


def _row_from_mapping(d: dict, stmt_account: str = "") -> FlexRow:
    acct = str(d.get("accountId") or d.get("ClientAccountID") or stmt_account or "")
    expiry = _parse_date(d.get("expiry") or d.get("Expiry"))
    trade_date = _parse_date(d.get("tradeDate") or d.get("TradeDate"))
    buy_sell = str(d.get("buySell") or d.get("Buy/Sell") or "").upper()
    # 撤改单会写成 "BUY (Ca.)" 之类,取首词
    buy_sell = buy_sell.split()[0] if buy_sell else ""
    return FlexRow(
        account=acct,
        ticker=str(d.get("underlyingSymbol") or d.get("UnderlyingSymbol")
                   or d.get("symbol") or "").upper().split()[0],
        strike=_f(d.get("strike") or d.get("Strike")),
        expiry=expiry,
        buy_sell=buy_sell,
        open_close=str(d.get("openCloseIndicator") or d.get("Open/CloseIndicator")
                       or "").upper().strip(),
        quantity=_f(d.get("quantity") or d.get("Quantity")),
        price=_f(d.get("tradePrice") or d.get("TradePrice")),
        commission=_f(d.get("ibCommission") or d.get("IBCommission")),
        trade_id=str(d.get("tradeID") or d.get("TradeID") or "").strip(),
        trade_date=trade_date,
        ts=_parse_ts(d.get("dateTime") or d.get("DateTime"), trade_date),
        codes=_codes(d),
        put_call=str(d.get("putCall") or d.get("Put/Call") or "").upper().strip(),
        asset_category=str(d.get("assetCategory") or d.get("AssetClass")
                           or "").upper().strip(),
        level_of_detail=str(d.get("levelOfDetail") or d.get("LevelOfDetail")
                            or "").upper().strip(),
    )


def rows_from_root(root: ET.Element) -> list[FlexRow]:
    out: list[FlexRow] = []
    for stmt in root.iter("FlexStatement"):
        acct = stmt.get("accountId", "")
        out.extend(_row_from_mapping(dict(tr.attrib), acct)
                   for tr in stmt.iter("Trade"))
    if not out:   # 有些 query 不带 FlexStatement 层级
        out.extend(_row_from_mapping(dict(tr.attrib))
                   for tr in root.iter("Trade"))
    return out


def parse_flex_xml(text: str) -> list[FlexRow]:
    return rows_from_root(ET.fromstring(text))


def parse_flex_csv(text: str) -> list[FlexRow]:
    """Flex 的扁平 CSV(首行即表头,列名与 XML 属性同名)。"""
    first = text.lstrip().split("\n", 1)[0]
    if re.match(r"^\s*\w[\w ]*,(Header|Data),", first):
        raise ValueError(
            "这是分段式 Activity Statement CSV(每段自带 Header 行),不是 Flex 扁平 CSV。"
            "请用 Flex Query 导出 XML,或把文件给我以便按实际格式解析。")
    return [_row_from_mapping({k.strip(): v for k, v in d.items() if k})
            for d in csv.DictReader(io.StringIO(text))]


def load_roots(*, path: Optional[Path] = None, token: str = "",
               query_id: str = "", fd: str = "", td: str = "",
               start: Optional[date] = None,
               end: Optional[date] = None) -> list[ET.Element]:
    """拿到一个或多个报表根节点(跨度 >365 天会分段,故返回列表)。"""
    if path is not None:
        text = Path(path).read_text(encoding="utf-8-sig")
        if not text.lstrip().startswith("<"):
            return []   # CSV 走 parse_flex_csv,不经这里
        return [ET.fromstring(text)]
    if not (token and query_id):
        raise ValueError("缺少 Flex 凭证:请在 .env 设 IBKR_FLEX_TOKEN / IBKR_FLEX_QUERY_ID")
    from src.brokers import ibkr_flex

    if start and end:
        return ibkr_flex.fetch_range(token, query_id, start, end)
    return [ibkr_flex.fetch_statement(token, query_id, fd=fd, td=td)]


def load_rows(*, path: Optional[Path] = None, token: str = "",
              query_id: str = "", **kw) -> list[FlexRow]:
    if path is not None:
        text = Path(path).read_text(encoding="utf-8-sig")
        if not text.lstrip().startswith("<"):
            return parse_flex_csv(text)
    rows: list[FlexRow] = []
    for root in load_roots(path=path, token=token, query_id=query_id, **kw):
        rows.extend(rows_from_root(root))
    return rows


# ---------------------------------------------------------------- 行 → 账本事件


def classify(row: FlexRow) -> Optional[str]:
    """判定这一行对"空头 call 账本"意味着什么;不相关返回 None。

    必须靠 buySell + openCloseIndicator 组合,因为同一账户里长期多头 call
    (LEAPS)的买入开仓/卖出平仓也在 Trades 段里 —— 它们不是 covered call,
    误入账会凭空造出轮次。
    """
    # 只认逐笔成交层:ORDER 是订单级汇总、CLOSED_LOT 是已实现盈亏核对、
    # ASSET_CLASS 是类别汇总 —— 同一笔成交在它们里各出现一次,全收会重复入账。
    # 空层级 = Trade Confirmation 报表(本身就是逐笔),放行。
    if row.level_of_detail not in ("EXECUTION", ""):
        return None
    if row.expiry is None or row.asset_category not in ("OPT", "OPTION", ""):
        return None
    if row.put_call not in ("C", "CALL"):
        return None
    if row.buy_sell == "SELL" and row.quantity < 0 and row.open_close == "O":
        return SELL_TO_OPEN
    if row.buy_sell == "BUY" and row.quantity > 0 and row.open_close in ("C", ""):
        if CODE_EXPIRE in row.codes:
            return EXPIRE
        if any(c in CODES_ASSIGN for c in row.codes):
            return ASSIGN
        if row.price == 0:
            # BookTrade 且价格为 0:实际上就是到期注销,只是报表没给 code
            return EXPIRE
        return BUY_TO_CLOSE
    return None   # 多头腿(BUY+O / SELL+C)、正股、put


def to_events(rows: list[FlexRow], *, tickers: Optional[set[str]] = None,
              accounts: Optional[set[str]] = None,
              since: Optional[date] = None) -> list[TradeEvent]:
    events: list[TradeEvent] = []
    for row in rows:
        action = classify(row)
        if action is None:
            continue
        if tickers and row.ticker not in tickers:
            continue
        if accounts and row.account not in accounts:
            continue
        if since and row.trade_date and row.trade_date < since:
            continue
        contracts = int(round(abs(row.quantity)))
        if contracts <= 0:
            continue
        key = f"{row.account}:{row.ticker}:{row.strike:g}:{row.expiry.isoformat()}"
        exec_id = (f"flex:{row.trade_id}" if row.trade_id
                   else f"flex:{key}:{action}:{row.ts}")
        price = 0.0 if action in (EXPIRE, ASSIGN) else abs(row.price)
        events.append(TradeEvent(
            exec_id=exec_id,
            ts=row.ts,
            ticker=row.ticker,
            action=action,
            strike=row.strike,
            expiry=row.expiry,
            contracts=contracts,
            price=round(price, 4),
            fees=round(abs(row.commission), 4),
            source=SOURCE,
            price_quality="exact",
            account=row.account,
            outcome={EXPIRE: OUT_EXPIRED, ASSIGN: OUT_ASSIGNED}.get(action, ""),
            note=("IBKR Flex 报表对账补录"
                  + (f",code={'/'.join(row.codes)}" if row.codes else "")),
        ))
    _pair_rolls(events)
    return events


def _pair_rolls(events: list[TradeEvent]) -> None:
    """同账户同标的同一天"一平一开" → 标成 roll 链(与持仓 diff 的配对规则一致)。"""
    by_day: dict[tuple[str, str, str], dict[str, TradeEvent]] = {}
    for ev in events:
        day = ev.ts[:10]
        slot = by_day.setdefault((ev.account, ev.ticker, day), {})
        if ev.action == BUY_TO_CLOSE:
            slot.setdefault("close", ev)
        elif ev.action == SELL_TO_OPEN:
            slot.setdefault("open", ev)
    for slot in by_day.values():
        close_ev, open_ev = slot.get("close"), slot.get("open")
        if close_ev is None or open_ev is None:
            continue
        close_ev.outcome = OUT_ROLLED
        open_ev.rolled_from = CallKey(close_ev.ticker, close_ev.strike,
                                      close_ev.expiry, close_ev.account)
        for e in (close_ev, open_ev):
            e.note += ",形态为同日 roll(一平一开)"


# ---------------------------------------------------------------- CLI


def _watcher_running() -> bool:
    """daemon watcher 在跑时拒绝写账本(单写者纪律)。--once 是只读,不算。"""
    try:
        out = subprocess.run(["pgrep", "-fl", "src.watcher"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    for line in out.splitlines():
        if "src.watcher" in line and "--once" not in line:
            return True
    return False


def _plan_table(events: list[TradeEvent]) -> str:
    lines = ["| 账户 | 标的 | 动作 | 合约 | 张 | 每股价 | 佣金 | 时间 | 判定 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for e in sorted(events, key=lambda x: (x.ts, x.ticker)):
        lines.append(
            f"| {e.account} | {e.ticker} | {e.action} | "
            f"{e.expiry:%m/%d} ${e.strike:g}C | {e.contracts} | "
            f"{e.price:.2f} | {e.fees:.2f} | {e.ts[:16]} | {e.outcome or '-'} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="从 IBKR Flex 报表对账补录账本")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", type=Path, help="已下载的 Flex 报表(XML 或扁平 CSV)")
    src.add_argument("--flex", action="store_true",
                     help="用 .env 的 IBKR_FLEX_TOKEN / IBKR_FLEX_QUERY_ID 直接拉取")
    ap.add_argument("--ticker", action="append", default=[],
                    help="只补这些标的(可重复);缺省全部")
    ap.add_argument("--account", action="append", default=[],
                    help="只补这些账户(可重复);缺省全部")
    ap.add_argument("--since", default=None, help="只补该日期之后的成交(YYYY-MM-DD)")
    ap.add_argument("--from", dest="date_from", default=None,
                    help="报表区间起(YYYY-MM-DD);缺省用 query 自带的滚动周期")
    ap.add_argument("--to", dest="date_to", default=None, help="报表区间止(YYYY-MM-DD)")
    ap.add_argument("--apply", action="store_true", help="真正写入账本(缺省只打印计划)")
    ap.add_argument("--save", type=Path, default=None,
                    help="把下载到的报表原文存一份(留痕 + 免得反复打 API)")
    ap.add_argument("--db", default=None, help="账本路径(默认 state/ledger.db)")
    args = ap.parse_args(argv)

    start = date.fromisoformat(args.date_from) if args.date_from else None
    end = date.fromisoformat(args.date_to) if args.date_to else None
    try:
        roots = load_roots(path=args.file,
                           token=env_value("IBKR_FLEX_TOKEN"),
                           query_id=env_value("IBKR_FLEX_QUERY_ID"),
                           start=start, end=end)
        if roots:
            rows = [r for root in roots for r in rows_from_root(root)]
        else:   # CSV
            rows = load_rows(path=args.file)
    except Exception as e:
        print(f"错误: 读取 Flex 报表失败 — {e}")
        return 1

    if args.save and roots:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        for i, root in enumerate(roots):
            out = (args.save if len(roots) == 1
                   else args.save.with_name(f"{args.save.stem}-{i + 1}{args.save.suffix}"))
            out.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
            print(f"报表原文已存:{out}")

    # ---- 先交代报表身份:哪个账户、覆盖哪段、有哪些章节、Trades 分几层
    from src.brokers.ibkr_flex import level_counts, section_inventory, statement_infos

    for root in roots:
        for info in statement_infos(root):
            print(f"报表 账户 {info.account_id} | 覆盖 {info.coverage}"
                  + (f" | 周期 {info.period}" if info.period else "")
                  + (f" | 生成于 {info.when_generated}" if info.when_generated else ""))
        inv = section_inventory(root)
        if inv:
            print("  章节: " + ", ".join(f"{k}={v}" for k, v in sorted(inv.items())))
        lvl = level_counts(root)
        if lvl:
            print("  Trades 分层: " + ", ".join(f"{k}={v}" for k, v in sorted(lvl.items()))
                  + "  → 入账只取 EXECUTION(其余层级是同一批成交的汇总视图)")
    print(f"Trade 行合计 {len(rows)} 行")

    events = to_events(
        rows,
        tickers={t.upper() for t in args.ticker} or None,
        accounts=set(args.account) or None,
        since=date.fromisoformat(args.since) if args.since else None)
    if not events:
        print("没有与空头 call 账本相关的行(长期多头 call / 正股 / put 已被过滤)。")
        return 0

    print(f"\n识别出 {len(events)} 条账本事件:\n{_plan_table(events)}")
    if not args.apply:
        print("\n这是计划预览,未写入。确认无误后加 --apply 落库。")
        return 0

    if _watcher_running():
        print("\n错误: 检测到 daemon watcher 正在运行。账本是单写者设计,"
              "请先停掉 watcher 再 --apply。")
        return 1

    lg = Ledger(Path(args.db) if args.db else STATE_DIR / "ledger.db")
    try:
        applied = lg.apply(events)
    finally:
        lg.close()
    print(f"\n已写入 {len(applied)} 条(重复的 {len(events) - len(applied)} 条按 "
          f"exec_id 幂等跳过)。")
    for a in applied:
        print(f"  #{a.trade_id} round={a.round_id} {a.event.ticker} "
              f"{a.event.action} {a.event.contracts}x")
    print("\n核对收益:python -m src.stats")
    return 0


if __name__ == "__main__":
    sys.exit(main())
