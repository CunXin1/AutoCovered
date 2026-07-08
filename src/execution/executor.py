"""批准执行器 — 半自动交易的安全轨。

默认三重关闭:
1. execution.enabled = false  → 批准只会收到"当前为决策支持模式"
2. execution.dry_run = true   → 批准后模拟成交,不发真实订单
3. 手机批准(APPROVE <id>)是唯一入口,系统绝不自行下单

附加护栏:提案 TTL 过期拒执、每日下单上限、只下限价单。
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from src.engine.roll import RollCandidate
from src.execution.proposals import (
    APPROVED,
    DRY_RUN_EXECUTED,
    EXECUTED,
    FAILED,
    PENDING,
    REJECTED,
    Proposal,
    ProposalLeg,
    ProposalStore,
)
from src.models import Position
from src.notify.push import Notifier

log = logging.getLogger(__name__)


class Executor:
    def __init__(self, cfg: dict, store: ProposalStore, notifier: Notifier, broker=None):
        e = cfg.get("execution") or {}
        self.enabled = bool(e.get("enabled", False))
        self.dry_run = bool(e.get("dry_run", True))
        self.propose_rolls = bool(e.get("propose_rolls", True))
        self.ttl_minutes = int(e.get("proposal_ttl_minutes", 30))
        self.max_orders_per_day = int(e.get("max_orders_per_day", 5))
        self.store = store
        self.notifier = notifier
        self.broker = broker
        self.orders_log = store.path.parent / "orders.jsonl"

    # ------------------------------------------------------------ 提案生成

    def propose_roll(self, pos: Position, candidate: RollCandidate) -> Optional[Proposal]:
        """由 watcher 在 ROLL_WINDOW/BREACHED 时调用。同一持仓已有待批提案则跳过。"""
        if not self.propose_rolls or pos.call is None:
            return None
        if self.store.has_pending_for(pos.position_id):
            return None

        c = pos.call
        # 限价保守化:候选 net credit 向下取 0.05 一档
        limit = math.floor(max(candidate.net_credit, 0.05) * 20) / 20
        p = Proposal.new(
            kind="ROLL",
            ticker=pos.ticker,
            position_id=pos.position_id,
            legs=[
                ProposalLeg("BUY", c.strike, c.expiry, c.contracts),
                ProposalLeg("SELL", candidate.strike, candidate.expiry, c.contracts),
            ],
            limit_net_credit=limit,
            rationale=candidate.summary(),
            ttl_minutes=self.ttl_minutes,
        )
        self.store.save(p)
        mode = "❗真实下单" if (self.enabled and not self.dry_run) else (
            "🧪 dry-run 模拟" if self.enabled else "📋 决策支持(执行未启用)"
        )
        self.notifier.push(
            f"📋 Roll 提案 {pos.ticker}(批准后 {mode})",
            f"{p.summary()}\n依据: {candidate.summary()}\n"
            f"{self.ttl_minutes} 分钟内有效。也可回复 APPROVE {p.id} / REJECT {p.id}",
            severity=3,
            actions=self.notifier.approval_actions(p.id),
        )
        return p

    # ------------------------------------------------------------ 命令处理

    def handle_approve(self, pid: str) -> None:
        p = self.store.get(pid)
        if p is None:
            self.notifier.push("⚠️ 提案不存在", f"未找到提案 {pid}", severity=2)
            return
        if p.status != PENDING:
            self.notifier.push("⚠️ 提案已处理", f"{pid} 当前状态: {p.status}", severity=2)
            return
        if p.is_expired():
            p.status = "expired"
            self.store.save(p)
            self.notifier.push("⏰ 提案已过期", f"{pid} 已超过 {self.ttl_minutes} 分钟有效期,"
                               f"请等待下一轮重新报价", severity=2)
            return

        if not self.enabled:
            p.status = APPROVED
            p.result = "execution.enabled=false,未执行(决策支持模式)"
            self.store.save(p)
            self.notifier.push(
                "📋 已记录批准(未执行)",
                f"{p.summary()}\n当前为决策支持模式,请手动在券商 App 下单。\n"
                f"启用半自动执行:settings.yaml → execution.enabled: true",
                severity=2,
            )
            return

        if self._orders_today() >= self.max_orders_per_day:
            p.status = FAILED
            p.result = "超过每日下单上限"
            self.store.save(p)
            self.notifier.push("🛑 超过每日下单上限",
                               f"今日已 {self.max_orders_per_day} 单,{pid} 未执行", severity=3)
            return

        if self.dry_run:
            p.status = DRY_RUN_EXECUTED
            p.result = "dry-run 模拟成交"
            self.store.save(p)
            self._log_order(p, dry_run=True)
            self.notifier.push("🧪 模拟成交(dry-run)",
                               f"{p.summary()}\n切换真实下单:execution.dry_run: false",
                               severity=2)
            return

        self._execute(p)

    def handle_reject(self, pid: str) -> None:
        p = self.store.get(pid)
        if p is None or p.status != PENDING:
            return
        p.status = REJECTED
        self.store.save(p)
        self.notifier.push("❌ 提案已拒绝", p.summary(), severity=1)

    # ------------------------------------------------------------ 真实执行

    def _execute(self, p: Proposal) -> None:
        if self.broker is None or not getattr(self.broker, "supports_trading", False):
            p.status = FAILED
            p.result = "当前数据源不支持下单(需 ibkr_gateway)"
            self.store.save(p)
            self.notifier.push("🛑 执行失败", p.result, severity=3)
            return
        try:
            buy = next(l for l in p.legs if l.action == "BUY")
            sell = next(l for l in p.legs if l.action == "SELL")
            result = self.broker.place_roll(
                ticker=p.ticker,
                old_strike=buy.strike,
                old_expiry=buy.expiry,
                new_strike=sell.strike,
                new_expiry=sell.expiry,
                contracts=buy.contracts,
                limit_credit=p.limit_net_credit,
            )
            p.status = EXECUTED
            p.result = result
            self.store.save(p)
            self._log_order(p, dry_run=False)
            self.notifier.push("✅ 订单已提交", f"{p.summary()}\n{result}", severity=3)
        except Exception as e:
            log.exception("下单失败 %s", p.id)
            p.status = FAILED
            p.result = str(e)
            self.store.save(p)
            self.notifier.push("🛑 下单失败", f"{p.summary()}\n{e}", severity=4)

    # ------------------------------------------------------------ 记账

    def _log_order(self, p: Proposal, dry_run: bool) -> None:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "proposal_id": p.id,
            "ticker": p.ticker,
            "kind": p.kind,
            "dry_run": dry_run,
        }
        with open(self.orders_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _orders_today(self) -> int:
        if not self.orders_log.exists():
            return 0
        today = date.today().isoformat()
        n = 0
        with open(self.orders_log, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if rec.get("ts", "").startswith(today) and not rec.get("dry_run"):
                        n += 1
                except json.JSONDecodeError:
                    continue
        return n
