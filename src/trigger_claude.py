"""事件触发 Claude 深度分析 — 防失控三保险:

1. 同一持仓+状态组合 1 小时内去重
2. 每日调用上限(默认 10 次),超限降级为纯规则告警
3. subprocess 超时强杀(claude_runner)

触发上下文(ticker、状态、关键数字)直接注入 prompt,
不让 Claude 自己去 state 文件里翻 — 省 token 也更稳。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from src.claude_runner import run_claude
from src.config import PROMPTS_DIR, STATE_DIR
from src.models import Position, StateResult
from src.notify.push import Notifier
from src.state_store import StateStore

log = logging.getLogger(__name__)


def _render(template: str, mapping: dict[str, str]) -> str:
    """安全的占位符替换(不用 str.format,避免 JSON 花括号冲突)。"""
    for k, v in mapping.items():
        template = template.replace("{" + k + "}", v)
    return template


class ClaudeTrigger:
    def __init__(self, cfg: dict, notifier: Notifier, store: StateStore):
        c = cfg.get("claude") or {}
        self.binary = c.get("binary", "claude")
        self.daily_limit = int(c.get("daily_call_limit", 10))
        self.dedup_minutes = int(c.get("dedup_window_minutes", 60))
        self.timeout = int(c.get("timeout_seconds", 300))
        self.trigger_states = set(c.get("trigger_states") or ["TESTED", "ROLL_WINDOW", "BREACHED"])
        self.notifier = notifier
        self.store = store
        self.budget_path = STATE_DIR / "claude_trigger.json"

    # ------------------------------------------------------------ 记账

    def _load(self) -> dict:
        try:
            with open(self.budget_path, "r", encoding="utf-8") as f:
                s = json.load(f)
        except (OSError, json.JSONDecodeError):
            s = {}
        if s.get("date") != date.today().isoformat():
            s = {"date": date.today().isoformat(), "calls": 0, "last": {}}
        s.setdefault("calls", 0)
        s.setdefault("last", {})
        return s

    def _save(self, s: dict) -> None:
        with open(self.budget_path, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False)

    def _try_reserve(self, key: str) -> bool:
        """去重 + 预算检查;通过则先记账(防崩溃导致重复触发)。"""
        s = self._load()
        if s["calls"] >= self.daily_limit:
            log.warning("Claude 日调用已达上限 %d,降级为纯规则告警", self.daily_limit)
            return False
        last = s["last"].get(key)
        if last:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
            if elapsed < self.dedup_minutes * 60:
                log.info("去重跳过 %s(%.0f 分钟前已分析)", key, elapsed / 60)
                return False
        s["calls"] += 1
        s["last"][key] = datetime.now(timezone.utc).isoformat()
        self._save(s)
        return True

    # ------------------------------------------------------------ 触发

    def maybe_trigger(self, pos: Position, result: StateResult) -> bool:
        if result.state.value not in self.trigger_states:
            return False
        if not self._try_reserve(f"{pos.position_id}:{result.state.value}"):
            return False
        return self._run_analysis(pos.to_dict(), result.state.value, result.emoji,
                                  "; ".join(result.reasons))

    def analyze_ticker(self, ticker: str) -> bool:
        """手机 ANALYZE <ticker> 命令入口:绕过去重(手动意图明确),仍占预算。"""
        entries = [d for d in self.store.load_positions_raw().get("positions", [])
                   if d.get("ticker", "").upper() == ticker.upper()]
        if not entries:
            self.notifier.push("⚠️ 未找到持仓", f"positions.json 中没有 {ticker.upper()}", severity=1)
            return False
        s = self._load()
        if s["calls"] >= self.daily_limit:
            self.notifier.push("🛑 Claude 日调用已达上限", f"今日 {self.daily_limit} 次已用完", severity=2)
            return False
        s["calls"] += 1
        self._save(s)
        d = entries[0]
        return self._run_analysis(d, d.get("state", "ON_TRACK"), "🔍",
                                  "; ".join(d.get("reasons", []) or ["用户手动请求分析"]))

    # ------------------------------------------------------------ 执行

    def _run_analysis(self, pos_dict: dict, state: str, emoji: str, reasons: str) -> bool:
        ticker = pos_dict["ticker"]
        try:
            template = (PROMPTS_DIR / "alert.md").read_text(encoding="utf-8")
            prompt = _render(template, {
                "ticker": ticker,
                "state": state,
                "emoji": emoji,
                "reasons": reasons,
                "context_json": json.dumps(pos_dict, ensure_ascii=False, indent=2),
            })
            output = run_claude(prompt, timeout=self.timeout, binary=self.binary)
        except Exception as e:
            log.exception("Claude 分析失败 %s", ticker)
            self.notifier.push(f"⚠️ {ticker} 深度分析失败", str(e)[:300], severity=2)
            return False

        name = f"{date.today().isoformat()}-{ticker}-{state}.md"
        self.store.save_analysis(name, output)
        first_line = output.strip().splitlines()[0][:80] if output.strip() else state
        self.notifier.push(
            f"{emoji} {ticker} 深度分析:{first_line}",
            output[:1800],
            severity=3,
        )
        return True
