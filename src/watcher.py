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
import sys
import time
from datetime import date

from src.config import STATE_DIR, load_config, load_lots
from src.data.events import get_events
from src.engine.pnl import compute_metrics
from src.engine.roll import find_roll_candidates
from src.engine.state_machine import evaluate, should_notify
from src.execution.executor import Executor
from src.execution.proposals import ProposalStore
from src.models import AlertConfig, PositionState, RollConfig
from src.notify.listener import CommandListener
from src.notify.push import Notifier
from src.state_store import StateStore
from src.trigger_claude import ClaudeTrigger

log = logging.getLogger(__name__)

PROPOSAL_STATES = {PositionState.ROLL_WINDOW, PositionState.BREACHED}


class Watcher:
    def __init__(self, cfg: dict, enable_trigger: bool = True):
        self.cfg = cfg
        self.enable_trigger = enable_trigger  # False = 供 Claude 定时任务内部刷新,避免嵌套调用 claude
        w = cfg.get("watcher") or {}
        self.interval = int(w.get("interval_seconds", 300))
        self.tick = int(w.get("tick_seconds", 5))
        self.run_when_closed = bool(w.get("run_when_closed", False))
        self.calendar = w.get("calendar", "NYSE")

        self.alert_cfg = AlertConfig.from_dict(cfg.get("alerts"))
        self.roll_cfg = RollConfig.from_dict(cfg.get("roll"))

        self.store = StateStore()
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

        prev_states = self.store.load_prev_states()
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

    # ---------------------------------------------------------------- 命令分发

    def handle_command(self, text: str) -> None:
        parts = text.strip().split()
        if not parts:
            return
        cmd = parts[0].upper()
        arg = parts[1] if len(parts) > 1 else ""
        if cmd == "APPROVE" and arg:
            self.executor.handle_approve(arg)
        elif cmd == "REJECT" and arg:
            self.executor.handle_reject(arg)
        elif cmd == "STATUS":
            self._push_status()
        elif cmd == "ANALYZE" and arg:
            self.trigger.analyze_ticker(arg)
        else:
            self.notifier.push("❓ 未识别命令",
                               f"收到: {text}\n可用: APPROVE <id> / REJECT <id> / STATUS / ANALYZE <ticker>",
                               severity=1)

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
    Watcher(cfg, enable_trigger=not args.no_trigger).loop(once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
