#!/usr/bin/env python
"""Roll/开仓候选计算器 — 确定性计算,Claude 只读输出不自己算。

用法(在仓库根目录):
    python .claude/skills/covered-call/scripts/roll_candidates.py NVDA              # roll 现有空头腿
    python .claude/skills/covered-call/scripts/roll_candidates.py NVDA --mode open  # 开新仓候选

数据:现价+期权链实时来自 IB Gateway(client_id+1,不抢 watcher 连接);
当前空头腿读 state/positions.json。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from src.brokers.ibkr import IBKRGatewayClient          # noqa: E402
from src.config import STATE_DIR, load_config, ticker_qcc  # noqa: E402
from src.engine.roll import find_open_candidates, find_roll_candidates  # noqa: E402
from src.models import RollConfig, parse_date            # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--mode", choices=["roll", "open"], default="roll")
    ap.add_argument("--style", default=None,
                    help="开仓风格预设(settings.yaml styles 段,如 conservative/aggressive;"
                         "per-ticker 覆盖仍是硬上限)")
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()
    ticker = args.ticker.upper()

    cfg = load_config()
    roll_cfg = RollConfig.from_dict(cfg.get("roll"))
    try:
        qcc_cfg = ticker_qcc(cfg, ticker, style=args.style)
    except ValueError as e:
        print(f"错误: {e}")
        return 1

    # 读当前持仓(空头腿 + 财报日)
    entry = None
    try:
        with open(STATE_DIR / "positions.json", "r", encoding="utf-8") as f:
            for d in json.load(f).get("positions", []):
                if d.get("ticker", "").upper() == ticker:
                    entry = d
                    break
    except (OSError, json.JSONDecodeError):
        pass
    earnings = parse_date(((entry or {}).get("events") or {}).get("earnings"))

    # 拉实时期权链(独立 client_id,不抢 watcher 连接)
    ib_cfg = dict(cfg.get("ibkr") or {})
    ib_cfg["client_id"] = int(ib_cfg.get("client_id", 11)) + 1
    client = IBKRGatewayClient(ib_cfg)
    try:
        if args.mode == "roll":
            lo, hi = roll_cfg.target_dte_min, roll_cfg.target_dte_max
        else:
            lo, hi = qcc_cfg.min_open_dte, qcc_cfg.min_open_dte + 30
        price, chain = client.fetch_chain(ticker, lo, hi)
    except Exception as e:
        print(f"错误: 无法从 IB Gateway 获取期权链({e})。请确认 Gateway 在线且已登录。")
        return 1
    finally:
        client.disconnect()

    today = date.today()
    if args.mode == "roll":
        call = (entry or {}).get("call")
        if not call:
            print(f"{ticker} 当前没有空头 call 腿(positions.json)。如要开新仓请用 --mode open。")
            return 1
        cands = find_roll_candidates(
            current_mid=call["mid"],
            current_strike=call["strike"],
            stock_price=price,
            chain=chain,
            today=today,
            cfg=roll_cfg,
            earnings_date=earnings,
            top_n=args.top,
        )
        print(f"# {ticker} roll 候选(现价 {price:.2f},现腿 {call['expiry']} "
              f"${call['strike']:g}C 买回成本 {call['mid']:.2f}/股)\n")
    else:
        cands = find_open_candidates(
            stock_price=price,
            chain=chain,
            today=today,
            qcc=qcc_cfg,
            earnings_date=earnings,
            top_n=args.top,
        )
        style_txt = f",风格 {args.style}" if args.style else ""
        print(f"# {ticker} 开仓候选(现价 {price:.2f},目标 delta "
              f"{qcc_cfg.target_delta_min:g}–{qcc_cfg.target_delta_max:g},"
              f"覆盖比例 {qcc_cfg.coverage_ratio:g}{style_txt})\n")

    if not cands:
        print("没有满足过滤条件的候选(net credit / DTE 窗口 / OTM / 不跨财报 / delta 上限)。")
        return 0

    quote_map = {(q.strike, q.expiry): q for q in chain}
    print("| 到期日 | Strike | 距现价 | DTE | Delta | bid/ask | 点差% | 权利金(mid) "
          "| Net Credit | 年化 | Strike改善 | 跨财报 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for c in cands:
        d = f"{c.delta:.2f}" if c.delta is not None else "--"
        q = quote_map.get((c.strike, c.expiry))
        ba = f"{q.bid:.2f}/{q.ask:.2f}" if q else "--"
        spread = (f"{(q.ask - q.bid) / q.mid:.0%}"
                  if q and q.mid > 0 and q.ask > 0 else "--")
        otm = (c.strike - price) / price if price > 0 else 0
        ew = "⚠️" if c.crosses_earnings else "—"
        print(f"| {c.expiry} | ${c.strike:g} | {otm:+.1%} | {c.dte} | {d} | {ba} | {spread} "
              f"| {c.premium:.2f} | {c.net_credit:+.2f} "
              f"| {c.net_credit_annualized_pct:.1%} | {c.strike_improvement_pct:+.1%} | {ew} |")
    if earnings:
        ecfg = roll_cfg if args.mode == "roll" else qcc_cfg
        print(f"\n注:财报日 {earnings}。跨财报候选(⚠️)按收紧规则放行:"
              f"delta ≤ {ecfg.earnings_max_delta:g} 且距现价 ≥ "
              f"{ecfg.earnings_min_otm_pct:.0%}。")
    print("注:net credit = 新腿 mid − 旧腿买回 mid;年化 = net_credit/现价 × 365/DTE;"
          "点差% =(ask−bid)/mid,>10% 视为流动性差。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
