"""Covered call 收益统计(模块 3)— 账本的唯一 Claude 可读出口。

    python -m src.stats [--ticker NVDA] [--json]

口径(全部由账本派生,不落库):
- round 现金流 = Σ(SELL_TO_OPEN: +price×100×contracts − fees;
  BUY_TO_CLOSE: −price×100×contracts − fees;EXPIRE/ASSIGN: −fees)
- round 级:每条 call 生命周期独立算胜负
- 链级:沿 rolled_from_round_id 聚合(roll 平旧腿常锁单轮亏损而链条整体
  盈利,两套口径并列才诚实)
- 数据质量分层:含 inferred/backfill 价格的 round 单独标注 —
  推断价不冒充真实成交(CONFIRM <trade_id> @<价> 可修正)
- assigned 附 upside_forgone =(到期收盘 − strike)×100×张数,
  只展示不计入盈亏(被叫走本来就是策略设计的一部分)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

from src.config import STATE_DIR

CLOSED_OUTCOMES = ("expired", "assigned", "bought_back", "rolled", "unknown")
INEXACT_QUALITIES = ("inferred", "backfill")


def _round_cash(trades: list[sqlite3.Row]) -> float:
    cash = 0.0
    for t in trades:
        gross = t["price"] * 100 * t["contracts"]
        if t["action"] == "SELL_TO_OPEN":
            cash += gross - t["fees"]
        elif t["action"] == "BUY_TO_CLOSE":
            cash += -gross - t["fees"]
        else:   # EXPIRE / ASSIGN(price=0)
            cash += -t["fees"]
    return round(cash, 2)


def compute_stats(conn: sqlite3.Connection, ticker: str | None = None) -> dict:
    conn.row_factory = sqlite3.Row
    where, args = "", []
    if ticker:
        where, args = " WHERE ticker=?", [ticker.upper()]
    rounds = conn.execute(f"SELECT * FROM rounds{where} ORDER BY id", args).fetchall()
    trades_by_round: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for t in conn.execute(f"SELECT * FROM trades{where} ORDER BY id", args):
        if t["round_id"] is not None:
            trades_by_round[t["round_id"]].append(t)

    # ---- round 级
    per_round: dict[int, dict] = {}
    for r in rounds:
        trades = trades_by_round.get(r["id"], [])
        cash = _round_cash(trades)
        inexact = any(t["price_quality"] in INEXACT_QUALITIES for t in trades)
        upside = 0.0
        for t in trades:
            if t["action"] == "ASSIGN" and t["aux_price"] is not None:
                upside += max(0.0, (t["aux_price"] - t["strike"]) * 100 * t["contracts"])
        per_round[r["id"]] = {
            "id": r["id"], "ticker": r["ticker"], "strike": r["strike"],
            "expiry": r["expiry"], "outcome": r["outcome"],
            "rolled_from": r["rolled_from_round_id"],
            "cash": cash, "inexact": inexact, "upside_forgone": round(upside, 2),
            "confirmable": sum(1 for t in trades if t["price_quality"] == "inferred"),
        }

    # ---- 链级(沿 rolled_from 归并到根)
    children = {v["rolled_from"]: v["id"] for v in per_round.values()
                if v["rolled_from"] in per_round}
    root_of: dict[int, int] = {}
    for rid, v in per_round.items():
        r = rid
        seen = set()
        while per_round[r]["rolled_from"] in per_round and r not in seen:
            seen.add(r)
            r = per_round[r]["rolled_from"]
        root_of[rid] = r
    chains: dict[int, list[dict]] = defaultdict(list)
    for rid, v in per_round.items():
        chains[root_of[rid]].append(v)
    chain_stats = []
    for root, members in chains.items():
        terminal = members[-1]
        closed = (all(m["outcome"] != "open" for m in members)
                  and terminal["outcome"] != "rolled")
        chain_stats.append({
            "root": root, "tickers": members[0]["ticker"],
            "rounds": [m["id"] for m in members],
            "cash": round(sum(m["cash"] for m in members), 2),
            "closed": closed,
            "inexact": any(m["inexact"] for m in members),
        })

    # ---- ticker 级汇总
    tickers: dict[str, dict] = {}
    for v in per_round.values():
        t = tickers.setdefault(v["ticker"], {
            "realized": 0.0, "open_cash": 0.0, "closed": 0, "open": 0,
            "wins": 0, "outcomes": defaultdict(int), "inexact_realized": 0.0,
            "confirmable": 0, "upside_forgone": 0.0,
        })
        t["confirmable"] += v["confirmable"]
        t["upside_forgone"] = round(t["upside_forgone"] + v["upside_forgone"], 2)
        if v["outcome"] == "open":
            t["open"] += 1
            t["open_cash"] = round(t["open_cash"] + v["cash"], 2)
        else:
            t["closed"] += 1
            t["realized"] = round(t["realized"] + v["cash"], 2)
            t["outcomes"][v["outcome"]] += 1
            if v["cash"] > 0:
                t["wins"] += 1
            if v["inexact"]:
                t["inexact_realized"] = round(t["inexact_realized"] + v["cash"], 2)
    for t in tickers.values():
        t["outcomes"] = dict(t["outcomes"])
        t["win_rate"] = round(t["wins"] / t["closed"], 4) if t["closed"] else None

    closed_chains = [c for c in chain_stats if c["closed"]]
    total = {
        "realized": round(sum(t["realized"] for t in tickers.values()), 2),
        "open_cash": round(sum(t["open_cash"] for t in tickers.values()), 2),
        "closed_rounds": sum(t["closed"] for t in tickers.values()),
        "open_rounds": sum(t["open"] for t in tickers.values()),
        "inexact_realized": round(sum(t["inexact_realized"] for t in tickers.values()), 2),
        "confirmable_trades": sum(t["confirmable"] for t in tickers.values()),
        "closed_chains": len(closed_chains),
        "chain_wins": sum(1 for c in closed_chains if c["cash"] > 0),
        "upside_forgone": round(sum(t["upside_forgone"] for t in tickers.values()), 2),
    }
    return {"tickers": tickers, "chains": chain_stats, "total": total}


def render_markdown(stats: dict) -> str:
    tk, total = stats["tickers"], stats["total"]
    out = ["# Covered Call 收益统计(来源:state/ledger.db,确定性计算)", ""]
    if not tk:
        out.append("账本为空 — 还没有任何入账交易。")
        return "\n".join(out)
    out += ["| Ticker | 已了结轮 | 实现盈亏 | 胜率(轮) | 进行中 | 进行中净现金 | 结局分布 |",
            "|---|---|---|---|---|---|---|"]
    for name in sorted(tk):
        t = tk[name]
        wr = f"{t['win_rate']:.0%}" if t["win_rate"] is not None else "--"
        oc = " ".join(f"{k}×{v}" for k, v in sorted(t["outcomes"].items())) or "--"
        out.append(f"| {name} | {t['closed']} | ${t['realized']:,.2f} | {wr} "
                   f"| {t['open']} | ${t['open_cash']:,.2f} | {oc} |")
    out += ["",
            f"**合计**:实现盈亏 ${total['realized']:,.2f}"
            f"(已了结 {total['closed_rounds']} 轮)"
            f" · 进行中 {total['open_rounds']} 轮净现金 ${total['open_cash']:,.2f}"]
    if total["closed_chains"]:
        out.append(f"**Roll 链口径**:已了结 {total['closed_chains']} 条链,"
                   f"链胜率 {total['chain_wins']}/{total['closed_chains']}"
                   f"(roll 平旧腿常锁单轮亏损,链口径才反映真实结果)")
    if total["upside_forgone"]:
        out.append(f"**被叫走的放弃上涨**(不计入盈亏):${total['upside_forgone']:,.2f}")
    if total["inexact_realized"] or total["confirmable_trades"]:
        out.append(f"⚠️ **数据质量**:实现盈亏中 ${total['inexact_realized']:,.2f} "
                   f"依赖推断/补录价格;{total['confirmable_trades']} 笔可用 "
                   f"`CONFIRM <trade_id> @<价>` 修正")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Covered call 收益统计(读 state/ledger.db)")
    ap.add_argument("--ticker", default=None)
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--db", default=None, help="账本路径(默认 state/ledger.db)")
    args = ap.parse_args(argv)

    db = Path(args.db) if args.db else STATE_DIR / "ledger.db"
    if not db.exists():
        print("账本不存在(state/ledger.db)。watcher 首轮成功运行后自动创建。")
        return 1
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)   # 只读打开
    try:
        stats = compute_stats(conn, ticker=args.ticker)
    finally:
        conn.close()
    print(json.dumps(stats, ensure_ascii=False, indent=2) if args.as_json
          else render_markdown(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
