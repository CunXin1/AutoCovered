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

from src.config import ticker_qcc
from src.engine.roll import RollCandidate
from src.execution.proposals import (
    APPROVED,
    DRY_RUN_EXECUTED,
    FAILED,
    PENDING,
    REJECTED,
    SUBMITTED,
    Proposal,
    ProposalLeg,
    ProposalStore,
)
from src.models import Position
from src.notify.push import Notifier

log = logging.getLogger(__name__)

# 改价护栏:新限价不得低于原提案限价的一半(防 fat-finger,如 24.5 打成 2.45 的反向)
LIMIT_OVERRIDE_FLOOR = 0.5
# 执行前校验:现报 mid 低于提案限价七成 = 行情已明显劣化,拒执行请重新提案
MID_DETERIORATION_FLOOR = 0.7


class Executor:
    def __init__(self, cfg: dict, store: ProposalStore, notifier: Notifier, broker=None):
        self.cfg = cfg
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

    def handle_approve(self, pid: str, limit_override: float | None = None) -> None:
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

        if limit_override is not None:
            unit = "每股权利金" if p.kind == "OPEN_CALL" else "net credit(每股)"
            if limit_override <= 0 or limit_override < p.limit_net_credit * LIMIT_OVERRIDE_FLOOR:
                self.notifier.push(
                    "🛑 改价被拒(提案保持待批)",
                    f"{pid} 原限价 {p.limit_net_credit:.2f}({unit}),"
                    f"改价 {limit_override:.2f} 低于原价一半,疑似输入失误。\n"
                    f"确要低价请先 REJECT 再重新提案。", severity=3)
                return
            old = p.limit_net_credit
            p.limit_net_credit = round(limit_override, 2)
            self.store.save(p)
            self.notifier.push("✏️ 限价已改",
                               f"{pid} {old:.2f} → {p.limit_net_credit:.2f}({unit})",
                               severity=1)

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

        # 提案生成与批准之间可能隔了 TTL 长度的行情:提交前按新鲜报价重校验
        reason = self._precheck(p)
        if reason:
            p.status = FAILED
            p.result = f"执行前校验未通过: {reason}"
            self.store.save(p)
            self.notifier.push("🛑 执行前校验未通过", f"{p.summary()}\n{reason}", severity=3)
            return

        try:
            sell = next(l for l in p.legs if l.action == "SELL")
            if p.kind == "OPEN_CALL":
                result = self.broker.place_open_call(
                    ticker=p.ticker,
                    strike=sell.strike,
                    expiry=sell.expiry,
                    contracts=sell.contracts,
                    limit_price=p.limit_net_credit,
                    order_ref=p.id,
                )
            elif p.kind == "ROLL":
                buy = next(l for l in p.legs if l.action == "BUY")
                result = self.broker.place_roll(
                    ticker=p.ticker,
                    old_strike=buy.strike,
                    old_expiry=buy.expiry,
                    new_strike=sell.strike,
                    new_expiry=sell.expiry,
                    contracts=buy.contracts,
                    limit_credit=p.limit_net_credit,
                    order_ref=p.id,
                )
            else:
                raise ValueError(f"未知提案类型: {p.kind}")
            p.status = SUBMITTED   # 终态由 watcher 对账:FILLED/PARTIALLY_FILLED/CANCELLED
            p.result = result
            self.store.save(p)
            self._log_order(p, dry_run=False)
            self.notifier.push("📤 订单已提交(等待成交)",
                               f"{p.summary()}\n{result}\n成交/取消会另行推送", severity=3)
        except Exception as e:
            log.exception("下单失败 %s", p.id)
            p.status = FAILED
            p.result = str(e)
            self.store.save(p)
            self.notifier.push("🛑 下单失败", f"{p.summary()}\n{e}", severity=4)

    def _precheck(self, p: Proposal) -> Optional[str]:
        """提交前的确定性二次校验。返回失败原因,None = 通过。"""
        sell = next((l for l in p.legs if l.action == "SELL"), None)
        if sell is None:
            return "提案缺 SELL 腿"
        try:
            q = self.broker.quote_option(p.ticker, sell.strike, sell.expiry)
        except Exception as e:
            return f"执行前报价失败: {e}"
        sp = q.get("stock_price")
        if sp is not None and sp >= sell.strike:
            return (f"新腿已不再 OTM(现价 {sp:.2f} ≥ strike {sell.strike:g}),"
                    f"QCC 纪律禁止,请重新提案")
        if p.kind == "OPEN_CALL":
            mid = q.get("mid")
            if mid is not None and mid < p.limit_net_credit * MID_DETERIORATION_FLOOR:
                return (f"行情已劣化:现报 mid {mid:.2f} 不足限价 "
                        f"{p.limit_net_credit:.2f} 的 {MID_DETERIORATION_FLOOR:.0%},请重新提案")
            err = self._coverage_error(p.ticker, sell.contracts, exclude_id=p.id)
            if err:
                return err
        return None

    def _coverage_error(self, ticker: str, want: int,
                        exclude_id: str = "") -> Optional[str]:
        """覆盖率重查:允许张数 = floor(股数×coverage_ratio/100)
        − 该 ticker 全部现有空头(跨腿聚合)− 其他未过期待批 OPEN_CALL 张数。"""
        pos_path = self.store.path.parent / "positions.json"
        try:
            with open(pos_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            return "读不到 positions.json,无法核对覆盖率"
        qty, shorts = 0.0, 0
        for d in raw.get("positions", []):
            if d.get("ticker", "").upper() != ticker.upper():
                continue
            qty = max(qty, float((d.get("stock") or {}).get("qty", 0)))
            if d.get("call"):
                shorts += int(d["call"].get("contracts", 0))
        ratio = ticker_qcc(self.cfg, ticker).coverage_ratio
        pending = self.store.pending_open_contracts(ticker)
        if exclude_id:
            own = self.store.get(exclude_id)
            if own is not None and own.status == PENDING and not own.is_expired():
                pending -= sum(l.contracts for l in own.legs)
        allowed = math.floor(qty * ratio / 100) - shorts - max(pending, 0)
        if want > allowed:
            return (f"覆盖率不足:允许 {allowed} 张 = floor({qty:g}×{ratio:g}/100) "
                    f"− 现有空头 {shorts} − 其他待批 {max(pending, 0)},请求 {want} 张")
        return None

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
        # 与 _log_order 的 UTC 时间戳同口径;用本地日期会在跨本地午夜的
        # 美股盘中把"每日"限额中途清零(如 UTC+8 时区)
        today = datetime.now(timezone.utc).date().isoformat()
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
