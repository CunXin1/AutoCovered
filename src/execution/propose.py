"""开仓提案 CLI — Claude 创建 OPEN_CALL 提案的唯一入口(机械护栏)。

    python -m src.execution.propose NVDA --strike 190 --expiry 2026-08-21 \
        --contracts 1 [--style conservative] [--limit 2.45] [--rationale "..."] [--ttl 120]

护栏设计(候选集成员资格制,不是逐项检查):
- (strike, expiry) 必须命中 find_open_candidates 用 style 合并配置算出的
  确定性候选集(自带 OTM / DTE≥min / delta 区间过滤;跨财报只标记不拦截)——
  Claude 机械上不可能把编造的合约送上手机
- 覆盖率:允许张数 = floor(股数×coverage_ratio/100) − 现有空头(跨腿聚合)
  − 未过期待批 OPEN_CALL 张数(两条各自合规的提案不许联合超卖)
- 限价必须落在新鲜报价的 [bid×0.9, ask×1.1] 内;推送正文的所有数字
  由本 CLI 从校验报价渲染,不转印调用方文本
- 校验通过 → 存提案 + ntfy 推送(✅/❌ 按钮),批准后仍有 executor 执行前二次校验
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import STATE_DIR, load_config, ticker_qcc          # noqa: E402
from src.engine.roll import ChainQuote, RollCandidate, find_open_candidates  # noqa: E402
from src.execution.proposals import Proposal, ProposalLeg, ProposalStore  # noqa: E402
from src.models import parse_date                                   # noqa: E402
from src.notify.push import Notifier                                # noqa: E402

# 限价相对新鲜报价的合法区间(SELL:过低=贱卖,过高=挂单空转)
LIMIT_BID_FLOOR = 0.9
LIMIT_ASK_CEIL = 1.1
DEFAULT_TTL_MINUTES = 120   # 开仓不像 roll 告警那样紧急,给用户更长决策窗


@dataclass
class OpenCheck:
    ok: bool
    errors: list[str]
    candidate: Optional[RollCandidate]
    quote: Optional[ChainQuote]


def validate_open(
    *,
    strike: float,
    expiry: date,
    contracts: int,
    candidates: list[RollCandidate],
    chain: list[ChainQuote],
    stock_qty: float,
    coverage_ratio: float,
    existing_short_contracts: int,
    pending_open_contracts: int,
) -> OpenCheck:
    """纯函数校验(pytest 覆盖)。所有规则不过 = 拒绝创建提案。"""
    errors: list[str] = []
    cand = next((c for c in candidates
                 if c.strike == strike and c.expiry == expiry), None)
    if cand is None:
        errors.append("(strike, expiry) 不在确定性候选集内"
                      "(OTM / DTE / delta 区间 过滤后)")
    quote = next((q for q in chain
                  if q.strike == strike and q.expiry == expiry), None)
    if quote is None and cand is not None:
        errors.append("期权链里找不到该合约的报价")

    if contracts <= 0:
        errors.append("contracts 必须 > 0")
    allowed = (math.floor(stock_qty * coverage_ratio / 100)
               - existing_short_contracts - pending_open_contracts)
    if contracts > allowed:
        errors.append(
            f"覆盖率不足:允许 {allowed} 张 = floor({stock_qty:g}×{coverage_ratio:g}/100)"
            f" − 现有空头 {existing_short_contracts} − 待批 {pending_open_contracts},"
            f"请求 {contracts} 张")
    return OpenCheck(not errors, errors, cand, quote)


def default_limit(quote: ChainQuote) -> float:
    """默认限价:mid 向下取 0.05 一档,但不低于 bid(保守可成交)。"""
    return round(max(quote.bid, math.floor(quote.mid * 20) / 20), 2)


def validate_limit(limit: float, quote: ChainQuote) -> Optional[str]:
    if limit <= 0:
        return "限价必须 > 0"
    if quote.bid > 0 and limit < quote.bid * LIMIT_BID_FLOOR:
        return f"限价 {limit:.2f} 低于新鲜 bid {quote.bid:.2f} 的 {LIMIT_BID_FLOOR:.0%},疑似贱卖"
    if quote.ask > 0 and limit > quote.ask * LIMIT_ASK_CEIL:
        return f"限价 {limit:.2f} 高于新鲜 ask {quote.ask:.2f} 的 {LIMIT_ASK_CEIL:.0%},不可能成交"
    return None


def _candidates_table(cands: list[RollCandidate], top: int = 10) -> str:
    lines = ["| 到期日 | Strike | DTE | Delta | 权利金(mid) | 年化 |", "|---|---|---|---|---|---|"]
    for c in cands[:top]:
        d = f"{c.delta:.2f}" if c.delta is not None else "--"
        lines.append(f"| {c.expiry} | ${c.strike:g} | {c.dte} | {d} "
                     f"| {c.premium:.2f} | {c.net_credit_annualized_pct:.1%} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="创建 OPEN_CALL 开仓提案(推手机批准)")
    ap.add_argument("ticker")
    ap.add_argument("--strike", type=float, required=True)
    ap.add_argument("--expiry", required=True, help="YYYY-MM-DD")
    ap.add_argument("--contracts", type=int, required=True)
    ap.add_argument("--style", default=None,
                    help="风格预设(settings.yaml styles 段,如 conservative/aggressive)")
    ap.add_argument("--limit", type=float, default=None, help="每股限价,缺省取 mid 向下 0.05 档")
    ap.add_argument("--rationale", default="", help="研究依据(叙述,不用于任何数字)")
    ap.add_argument("--ttl", type=int, default=DEFAULT_TTL_MINUTES)
    args = ap.parse_args(argv)
    ticker = args.ticker.upper()
    expiry = date.fromisoformat(args.expiry)

    cfg = load_config()
    try:
        qcc = ticker_qcc(cfg, ticker, style=args.style)
    except ValueError as e:
        print(f"错误: {e}")
        return 1

    # ---- 持仓事实(positions.json,跨腿聚合)
    try:
        with open(STATE_DIR / "positions.json", "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        print("错误: 读不到 state/positions.json,请先跑 python -m src.watcher --once")
        return 1
    qty, shorts, earnings = 0.0, 0, None
    for d in raw.get("positions", []):
        if d.get("ticker", "").upper() != ticker:
            continue
        qty = max(qty, float((d.get("stock") or {}).get("qty", 0)))
        if d.get("call"):
            shorts += int(d["call"].get("contracts", 0))
        earnings = earnings or parse_date(((d.get("events") or {}).get("earnings")))
    if qty <= 0:
        print(f"错误: positions.json 中没有 {ticker} 的正股持仓")
        return 1

    store = ProposalStore(STATE_DIR / "proposals.json")
    pending = store.pending_open_contracts(ticker)

    # ---- 实时候选集(独立 client_id,不抢 watcher/候选脚本连接)
    from src.brokers.ibkr import IBKRGatewayClient

    ib_cfg = dict(cfg.get("ibkr") or {})
    ib_cfg["client_id"] = int(ib_cfg.get("client_id", 11)) + 2
    client = IBKRGatewayClient(ib_cfg)
    try:
        price, chain = client.fetch_chain(ticker, qcc.min_open_dte, qcc.min_open_dte + 30)
    except Exception as e:
        print(f"错误: 无法从 IB Gateway 获取期权链({e})")
        return 1
    finally:
        client.disconnect()

    today = date.today()
    candidates = find_open_candidates(
        stock_price=price, chain=chain, today=today, qcc=qcc,
        earnings_date=earnings, top_n=500)

    check = validate_open(
        strike=args.strike, expiry=expiry, contracts=args.contracts,
        candidates=candidates, chain=chain, stock_qty=qty,
        coverage_ratio=qcc.coverage_ratio,
        existing_short_contracts=shorts, pending_open_contracts=pending)
    if not check.ok:
        print(f"❌ 提案被拒({ticker} 现价 {price:.2f}):")
        for e in check.errors:
            print(f"  - {e}")
        if candidates:
            print(f"\n合法候选(style={args.style or '默认'},delta "
                  f"{qcc.target_delta_min:g}–{qcc.target_delta_max:g}):\n"
                  + _candidates_table(candidates))
        else:
            print("\n当前过滤条件下没有任何合法候选。")
        return 1

    cand, quote = check.candidate, check.quote
    limit = round(args.limit, 2) if args.limit is not None else default_limit(quote)
    err = validate_limit(limit, quote)
    if err:
        print(f"❌ 提案被拒: {err}")
        return 1

    p = Proposal.new(
        kind="OPEN_CALL", ticker=ticker, position_id=ticker,
        legs=[ProposalLeg("SELL", args.strike, expiry, args.contracts)],
        limit_net_credit=limit,
        rationale=(args.rationale or "").strip()[:400],
        ttl_minutes=args.ttl)
    store.save(p)

    # ---- 推送(数字全部来自校验用报价/候选,不转印 rationale 里的数字)
    e = cfg.get("execution") or {}
    mode = ("❗真实下单" if (e.get("enabled") and not e.get("dry_run", True))
            else "🧪 dry-run 模拟" if e.get("enabled")
            else "📋 决策支持(执行未启用)")
    d = f"{cand.delta:.2f}" if cand.delta is not None else "--"
    body = (
        f"[{p.id}] SELL {args.contracts}x {expiry:%m/%d} ${args.strike:g}C,"
        f"限价 ≥{limit:.2f}/股\n"
        f"现价 {price:.2f} · Δ{d} · {cand.dte}d · bid/ask {quote.bid:.2f}/{quote.ask:.2f} "
        f"· 年化 {cand.net_credit_annualized_pct:.1%}\n"
        f"覆盖率: 持股 {qty:g} × {qcc.coverage_ratio:g} − 空头 {shorts} − 待批 {pending}\n"
    )
    if cand.crosses_earnings:
        body += ("⚠️ 到期跨财报:财报 gap 可一夜吃掉大段 OTM 距离,"
                 "权利金含事件溢价,批准前请确认已权衡财报风险\n")
    if p.rationale:
        body += f"依据: {p.rationale}\n"
    body += (f"{args.ttl} 分钟内有效。批准后 {mode}。\n"
             f"也可回复 APPROVE {p.id} @<价> 改限价(每股权利金)/ REJECT {p.id}")
    notifier = Notifier(cfg)
    notifier.push(f"📋 开仓提案 {ticker} {expiry:%m/%d} ${args.strike:g}C",
                  body, severity=3, actions=notifier.approval_actions(p.id))

    print(f"✅ 提案已创建并推送手机: {p.summary()}")
    print(f"   Δ{d} DTE {cand.dte} bid/ask {quote.bid:.2f}/{quote.ask:.2f} "
          f"年化 {cand.net_credit_annualized_pct:.1%} TTL {args.ttl}min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
