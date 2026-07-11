"""Watcher 主循环 — Layer 1(纯 Python,无 LLM)。

结构:
- 5 秒 tick:处理手机命令队列(APPROVE/REJECT/STATUS/ANALYZE),响应及时
- 300 秒行情周期:持仓+Greeks → 状态机 → 写 state → 推送 → 提案/触发 Claude
- 主源(ibkr_gateway)连续失败 N 轮 → 自动切备源(snaptrade)并推降级通知
- 连续失败 3 轮 → 推"系统掉线"自检告警

用法:
    python -m src.watcher            # 常驻(deploy 脚本注册开机自启)
    python -m src.watcher --once     # 单轮冒烟测试
"""
from __future__ import annotations

import argparse
import logging
import queue
import re
import sys
import time
from datetime import date

from datetime import datetime, timezone

from src.config import STATE_DIR, load_config, load_lots
from src.data.events import get_events
from src.engine.lifecycle import (
    CallKey,
    backfill_events,
    diff_positions,
    events_from_executions,
)
from src.engine.pnl import compute_metrics
from src.engine.roll import find_roll_candidates
from src.engine.state_machine import evaluate, should_notify
from src.execution.executor import Executor
from src.execution.proposals import (
    APPROVED,
    CANCELLED,
    FILLED,
    PARTIALLY_FILLED,
    SUBMITTED,
    ProposalStore,
)
from src.ledger import Ledger
from src.models import AlertConfig, Position, PositionState, RollConfig
from src.notify.listener import CommandListener
from src.notify.push import Notifier
from src.state_store import StateStore
from src.trigger_claude import ClaudeTrigger

log = logging.getLogger(__name__)

PROPOSAL_STATES = {PositionState.ROLL_WINDOW, PositionState.BREACHED}

# 手机命令严格解析。严格性是安全要求:若 "APPROVE X @2.45" 被宽松解析吞掉
# @2.45,等于按用户没同意的价格下单 — 带任何多余 token 一律拒识并回推帮助。
_CMD_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("APPROVE", re.compile(r"^APPROVE\s+(\S+)(?:\s+@(\d+(?:\.\d{1,2})?))?$", re.I)),
    ("REJECT", re.compile(r"^REJECT\s+(\S+)$", re.I)),
    ("STATUS", re.compile(r"^STATUS$", re.I)),
    ("ANALYZE", re.compile(r"^ANALYZE\s+([A-Za-z][A-Za-z0-9.\-]{0,9})$", re.I)),
    ("CONFIRM", re.compile(r"^CONFIRM\s+(\d+)\s+@(\d+(?:\.\d{1,2})?)$", re.I)),
]

HELP_TEXT = ("可用: APPROVE <id> [@<价>] / REJECT <id> / STATUS / "
             "ANALYZE <ticker> / CONFIRM <trade_id> @<价>")


def parse_command(text: str):
    """返回 (命令名, 捕获组) 或 None(未识别/格式不严格)。纯函数,pytest 覆盖。"""
    t = " ".join((text or "").strip().split())
    for name, pat in _CMD_PATTERNS:
        m = pat.match(t)
        if m:
            return name, m.groups()
    return None


class Watcher:
    def __init__(self, cfg: dict, enable_trigger: bool = True, ledger_writes: bool = True):
        self.cfg = cfg
        self.enable_trigger = enable_trigger  # False = 供 Claude 定时任务内部刷新,避免嵌套调用 claude
        self.ledger_writes = ledger_writes    # False = --once 只读模式(单写者纪律:仅 daemon 记账)
        w = cfg.get("watcher") or {}
        self.interval = int(w.get("interval_seconds", 300))
        self.tick = int(w.get("tick_seconds", 5))
        self.run_when_closed = bool(w.get("run_when_closed", False))
        self.calendar = w.get("calendar", "NYSE")

        self.alert_cfg = AlertConfig.from_dict(cfg.get("alerts"))
        self.roll_cfg = RollConfig.from_dict(cfg.get("roll"))

        self.store = StateStore()
        self.ledger = Ledger(STATE_DIR / "ledger.db")
        self.notifier = Notifier(cfg)
        self.trigger = ClaudeTrigger(cfg, self.notifier, self.store)
        self.executor = Executor(
            cfg, ProposalStore(STATE_DIR / "proposals.json"), self.notifier
        )

        b = cfg.get("brokers") or {}
        self.primary_kind = b.get("primary", "ibkr_gateway")
        self.fallback_kind = b.get("fallback", "") or None
        self.fallback_after = int(b.get("fallback_after_failures", 3))

        from src.brokers.base import build_broker

        self.primary = build_broker(cfg, self.primary_kind)
        self.fallback = build_broker(cfg, self.fallback_kind) if self.fallback_kind else None
        self.executor.broker = self.primary if self.primary.supports_trading else None

        self.cmd_queue: "queue.Queue[str]" = queue.Queue()
        self.listener = None
        if (cfg.get("notify") or {}).get("provider") == "ntfy":
            self.listener = CommandListener(cfg, self.cmd_queue)

        self.consecutive_failures = 0
        self.offline_notified = False

    # ---------------------------------------------------------------- 行情周期

    def run_cycle(self) -> None:
        today = date.today()
        lots = load_lots()

        positions, source = self._fetch_positions(lots)

        for pos in positions:
            pos.events = get_events(pos.ticker)
            pos.metrics = compute_metrics(pos, today)

        prev_raw = self.store.load_positions_raw()
        prev_states: dict[str, PositionState] = {}
        for d in prev_raw.get("positions", []):
            try:
                prev_states[d["id"]] = PositionState(d["state"])
            except (KeyError, ValueError):
                continue
        notify_log = self.store.load_notify_log()
        results = {}

        for pos in positions:
            res = evaluate(pos, self.alert_cfg, today)
            results[pos.position_id] = res
            prev = prev_states.get(pos.position_id)

            if should_notify(prev, res, notify_log.get(pos.position_id), today):
                self._notify_state(pos, res, prev)
                notify_log[pos.position_id] = today
                self.store.append_alert({
                    "position_id": pos.position_id,
                    "ticker": pos.ticker,
                    "prev_state": prev.value if prev else None,
                    "state": res.state.value,
                    "reasons": res.reasons,
                    "metrics": pos.metrics.to_dict(),
                })

            if res.state in PROPOSAL_STATES:
                self._maybe_propose_roll(pos, today)

            if self.enable_trigger:
                self.trigger.maybe_trigger(pos, res)

        # 账本先于 positions.json 落盘:中途 crash 时下轮 diff 依然成立,
        # 合成事件的确定性 exec_id 保证重放幂等
        self._update_ledger(prev_raw, positions, source, today)
        self._reconcile_orders(source)

        self.store.write_positions(positions, results, data_source=source)
        self.store.save_notify_log(notify_log)
        self.executor.store.expire_stale()
        log.info("周期完成: %d 个持仓, 数据源 %s", len(positions), source)

    def _fetch_positions(self, lots):
        try:
            positions = self.primary.fetch_positions(lots)
            self.consecutive_failures = 0
            if self.offline_notified:
                self.offline_notified = False
                self.notifier.push("✅ 数据源恢复", f"{self.primary.name} 已恢复", severity=1)
            return positions, self.primary.name
        except Exception as e:
            self.consecutive_failures += 1
            log.warning("主源 %s 失败(第 %d 次): %s",
                        self.primary.name, self.consecutive_failures, e)
            if self.fallback and self.consecutive_failures >= self.fallback_after:
                try:
                    positions = self.fallback.fetch_positions(lots)
                    if not self.offline_notified:
                        self.offline_notified = True
                        self.notifier.push(
                            "🟠 已切换备用数据源",
                            f"{self.primary.name} 连续失败 {self.consecutive_failures} 次,"
                            f"降级到 {self.fallback.name}(无 Greeks,delta 类规则暂停)",
                            severity=3,
                        )
                    return positions, self.fallback.name
                except Exception as e2:
                    log.warning("备源 %s 也失败: %s", self.fallback.name, e2)
            if self.consecutive_failures == 3 and not self.offline_notified:
                self.offline_notified = True
                self.notifier.push(
                    "🔴 监控系统掉线",
                    f"{self.primary.name} 连续 3 轮拉取失败,最近错误: {str(e)[:200]}\n"
                    f"请检查 IB Gateway 是否在线/是否需要重新登录",
                    severity=4,
                )
            raise

    def _notify_state(self, pos, res, prev) -> None:
        prev_txt = f"(由 {prev.value} 变为)" if prev else ""
        m = pos.metrics
        lines = list(res.reasons) or ["状态更新"]
        if m.combined_pnl is not None:
            lines.append(f"组合浮盈亏 ${m.combined_pnl:,.0f}")
        if m.days_to_long_term:
            lines.append(f"距长期资本利得还有 {m.days_to_long_term} 天")
        self.notifier.push(
            f"{res.emoji} {pos.position_id} → {res.state.value}{prev_txt}",
            "\n".join(lines),
            severity=res.severity,
        )

    def _maybe_propose_roll(self, pos, today) -> None:
        if pos.call is None or not self.executor.propose_rolls:
            return
        if self.executor.store.has_pending_for(pos.position_id):
            return
        try:
            price, chain = self.primary.fetch_chain(
                pos.ticker, self.roll_cfg.target_dte_min, self.roll_cfg.target_dte_max
            )
        except Exception as e:
            log.warning("拉取 %s 期权链失败,跳过提案: %s", pos.ticker, e)
            return
        candidates = find_roll_candidates(
            current_mid=pos.call.mid,
            current_strike=pos.call.strike,
            stock_price=price,
            chain=chain,
            today=today,
            cfg=self.roll_cfg,
            earnings_date=pos.events.earnings,
            top_n=1,
        )
        if candidates:
            self.executor.propose_roll(pos, candidates[0])

    # ---------------------------------------------------------------- 账本

    def _update_ledger(self, prev_raw: dict, positions, source: str, today) -> None:
        """executions 对账 + 持仓 diff 推断 → 账本。失败绝不影响监控主流程。"""
        if not self.ledger_writes:
            return
        try:
            now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

            # 首次启动:补录现有空头腿,当轮不做 diff
            if self.ledger.is_empty():
                applied = self.ledger.apply(backfill_events(positions, now_iso))
                if applied:
                    self.notifier.push(
                        "📒 账本已初始化",
                        f"补录现有空头腿 {len(applied)} 条(premium 取 IBKR 均价,"
                        f"真实开仓日期不可考)", severity=1)
                return

            # 成交回报(对账主源;备源/失败时自动退化为纯 diff 推断)
            executions = []
            if source == self.primary.name:
                try:
                    executions = self.primary.fetch_executions()
                except Exception as e:
                    log.warning("拉取成交回报失败: %s", e)

            # 提案索引:orderRef 归因 + 决策支持模式的手动 roll 配对
            kinds: dict[str, str] = {}
            roll_old: dict[str, CallKey] = {}
            approved_rolls: list[tuple[str, CallKey, CallKey]] = []
            for p in self.executor.store.load_all():
                kinds[p.id] = p.kind
                if p.kind != "ROLL":
                    continue
                buy = next((l for l in p.legs if l.action == "BUY"), None)
                sell = next((l for l in p.legs if l.action == "SELL"), None)
                if buy is None or sell is None:
                    continue
                old_key = CallKey(p.ticker, buy.strike, buy.expiry)
                roll_old[p.id] = old_key
                if p.status == APPROVED:   # 批准但未执行 → 用户可能手动做了
                    approved_rolls.append(
                        (p.id, old_key, CallKey(p.ticker, sell.strike, sell.expiry)))

            exec_events = events_from_executions(executions, kinds, roll_old)

            prev_positions = [Position.from_dict(d)
                              for d in prev_raw.get("positions", [])]
            prev_source = prev_raw.get("data_source", "")

            # 预取"消失且已到期"腿的官方收盘价(expired/assigned 判定)
            curr_keys = {(p.ticker, p.call.strike, p.call.expiry)
                         for p in positions if p.call}
            closes: dict[tuple[str, object], float] = {}
            for pp in prev_positions:
                if pp.call is None or pp.call.expiry > today:
                    continue
                k = (pp.ticker, pp.call.strike, pp.call.expiry)
                ck = (pp.ticker, pp.call.expiry)
                if k not in curr_keys and ck not in closes:
                    try:
                        c = self.primary.fetch_daily_close(pp.ticker, pp.call.expiry)
                        if c is not None:
                            closes[ck] = c
                    except Exception as e:
                        log.warning("取 %s 到期收盘价失败: %s", pp.ticker, e)

            diff = diff_positions(
                prev=prev_positions, curr=positions,
                prev_source=prev_source, curr_source=source,
                executions=executions, expiry_closes=closes,
                approved_rolls=approved_rolls, today=today, now_iso=now_iso)
            if diff.skip_reason:
                log.warning("账本 diff 跳过: %s", diff.skip_reason)

            applied = self.ledger.apply(exec_events + diff.events)
            self._notify_ledger(applied, diff.notices)
        except Exception:
            log.exception("账本更新失败(不影响监控主流程)")

    def _notify_ledger(self, applied, notices) -> None:
        for a in applied:
            ev = a.event
            if ev.source == "backfill":
                continue
            body = (f"{ev.ticker} {ev.action} {ev.contracts}x "
                    f"{ev.expiry:%m/%d} ${ev.strike:g}C @{ev.price:.2f}")
            if ev.fees:
                body += f"(费 ${ev.fees:.2f})"
            if ev.outcome:
                body += f"\n判定: {ev.outcome}"
            if ev.note:
                body += f"\n{ev.note}"
            if ev.source == "executor":
                title, sev = f"✅ 成交入账 #{a.trade_id}", 2
            elif ev.needs_confirm:
                title, sev = f"📒 已记账(待确认)#{a.trade_id}", 2
                body += f"\n如不准确,回复: CONFIRM {a.trade_id} @<每股价>"
            else:
                title, sev = f"📒 已记账 #{a.trade_id}", 1
            self.notifier.push(title, body, severity=sev)
        for n in notices:
            self.notifier.push("📒 账本提醒", n, severity=1)

    def _reconcile_orders(self, source: str) -> None:
        """SUBMITTED 提案的终态跟踪:成交(账本 orderRef 归因)/取消(DAY 单收盘死亡)。"""
        if not self.ledger_writes or source != self.primary.name:
            return
        subs = self.executor.store.by_status(SUBMITTED)
        if not subs:
            return
        try:
            open_refs = self.primary.fetch_open_order_refs()
        except Exception as e:
            log.warning("拉取在途订单失败,跳过订单对账: %s", e)
            return
        for p in subs:
            sell = next((l for l in p.legs if l.action == "SELL"), None)
            target = sell.contracts if sell else 0
            filled = self.ledger.filled_contracts(p.id)
            if target and filled >= target:
                p.status = FILLED
                self.executor.store.save(p)
                self.notifier.push("✅ 提案全部成交", p.summary(), severity=3)
            elif p.id not in open_refs:
                if filled > 0:
                    p.status = PARTIALLY_FILLED
                    p.result = f"部分成交 {filled}/{target} 后订单结束"
                    self.notifier.push("⚠️ 部分成交后订单已结束",
                                       f"{p.summary()}\n成交 {filled}/{target} 张", severity=3)
                else:
                    p.status = CANCELLED
                    p.result = "限价单未成交(DAY 单收盘取消/被撤)"
                    self.notifier.push("⏰ 限价单未成交已取消",
                                       f"{p.summary()}\n如仍要执行请重新提案", severity=2)
                self.executor.store.save(p)

    def _confirm_trade(self, tid_s: str, price_s: str) -> None:
        try:
            tid = int(tid_s)
            price = float(price_s.lstrip("@"))
            if price <= 0:
                raise ValueError
        except ValueError:
            self.notifier.push("❓ CONFIRM 格式错误",
                               "用法: CONFIRM <trade_id> @<每股价>", severity=1)
            return
        row = self.ledger.confirm_trade_price(tid, price)
        if row is None:
            self.notifier.push("⚠️ 未找到账本记录", f"trade #{tid} 不存在", severity=1)
        else:
            self.notifier.push(
                "✅ 价格已确认",
                f"#{tid} {row['ticker']} {row['action']} @{row['price']:.2f}"
                f"(price_quality=user_confirmed)", severity=1)

    # ---------------------------------------------------------------- 命令分发

    def handle_command(self, text: str) -> None:
        if not (text or "").strip():
            return
        parsed = parse_command(text)
        if parsed is None:
            self.notifier.push("❓ 未识别命令", f"收到: {text}\n{HELP_TEXT}", severity=1)
            return
        cmd, g = parsed
        if cmd == "APPROVE":
            self.executor.handle_approve(g[0], float(g[1]) if g[1] else None)
        elif cmd == "REJECT":
            self.executor.handle_reject(g[0])
        elif cmd == "STATUS":
            self._push_status()
        elif cmd == "ANALYZE":
            self.trigger.analyze_ticker(g[0])
        elif cmd == "CONFIRM":
            self._confirm_trade(g[0], g[1])

    def _push_status(self) -> None:
        raw = self.store.load_positions_raw()
        entries = raw.get("positions", [])
        if not entries:
            self.notifier.push("📭 暂无持仓数据", "watcher 尚未完成首轮拉取", severity=1)
            return
        from src.models import EMOJI, PositionState as PS

        lines = []
        for d in entries:
            state = d.get("state", "ON_TRACK")
            emoji = EMOJI.get(PS(state), "⚪") if state in PS._value2member_map_ else "⚪"
            m = d.get("metrics") or {}
            dte = m.get("dte")
            pct = m.get("pct_max_profit")
            extra = []
            if dte is not None:
                extra.append(f"{dte}d")
            if pct is not None:
                extra.append(f"利润 {pct:.0%}")
            lines.append(f"{emoji} {d['id']} {state} " + " ".join(extra))
        lines.append(f"\n更新于 {raw.get('updated_at', '?')} ({raw.get('data_source', '?')})")
        self.notifier.push("📊 持仓状态", "\n".join(lines), severity=1)

    # ---------------------------------------------------------------- 主循环

    def loop(self, once: bool = False) -> None:
        if self.listener:
            self.listener.start()
        last_cycle = 0.0
        while True:
            # ① 命令队列(每 tick 都处理,批准响应及时)
            try:
                while True:
                    self.handle_command(self.cmd_queue.get_nowait())
            except queue.Empty:
                pass

            # ② 行情周期
            now = time.monotonic()
            if now - last_cycle >= self.interval or once:
                if once or self.run_when_closed or self._market_open():
                    last_cycle = now
                    try:
                        self.run_cycle()
                    except Exception:
                        log.exception("行情周期失败")
                else:
                    last_cycle = now  # 收盘期间也按周期节流
                    log.debug("休市,跳过")
            if once:
                return
            time.sleep(self.tick)

    def _market_open(self) -> bool:
        try:
            from src.market_hours import is_market_open_now

            return is_market_open_now(self.calendar)
        except Exception:
            log.exception("交易日历判断失败,按开市处理")
            return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="单轮后退出(冒烟测试/Claude 定时任务刷新)")
    ap.add_argument("--no-trigger", action="store_true",
                    help="跳过 Claude 深度分析触发(Claude 会话内刷新数据时用,避免嵌套)")
    args = ap.parse_args(argv)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(STATE_DIR / "watcher.log", encoding="utf-8"),
        ],
    )
    cfg = load_config()
    if args.once:
        # --once 供 Claude/routines 刷数据:专用 client_id 不与常驻 watcher 冲突,
        # 且不写账本(单写者纪律)
        ib_cfg = cfg.setdefault("ibkr", {})
        ib_cfg["client_id"] = int(ib_cfg.get("client_id_once", 14))
    Watcher(cfg, enable_trigger=not args.no_trigger,
            ledger_writes=not args.once).loop(once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
