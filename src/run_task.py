"""定时任务入口:python -m src.run_task daily|weekly [--force]

- daily 自带交易日门禁(非交易日直接跳过,调度器可以无脑触发)
- 输出全文存 state/analysis/,摘要推手机
- OS 调度器(Task Scheduler/launchd)和 Claude Code CronCreate 都调这个入口
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from src.claude_runner import run_claude
from src.config import PROMPTS_DIR, load_config
from src.notify.push import Notifier
from src.state_store import StateStore

log = logging.getLogger(__name__)

TASKS = {
    "daily": {"prompt": "daily.md", "title": "📊 每日晨报", "timeout": 900, "gate": True},
    "weekly": {"prompt": "weekly.md", "title": "📅 周度复盘", "timeout": 1200, "gate": False},
}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("task", choices=sorted(TASKS))
    ap.add_argument("--force", action="store_true", help="跳过交易日门禁")
    args = ap.parse_args(argv)

    spec = TASKS[args.task]
    if spec["gate"] and not args.force:
        from src.market_hours import is_trading_day

        if not is_trading_day():
            log.info("今天不是交易日,跳过 %s", args.task)
            return 0

    cfg = load_config()
    notifier = Notifier(cfg)
    store = StateStore()

    prompt = (PROMPTS_DIR / spec["prompt"]).read_text(encoding="utf-8")
    try:
        output = run_claude(prompt, timeout=spec["timeout"],
                            binary=(cfg.get("claude") or {}).get("binary", "claude"))
    except Exception as e:
        log.exception("%s 失败", args.task)
        notifier.push(f"⚠️ {spec['title']}失败", str(e)[:300], severity=2)
        return 1

    path = store.save_analysis(f"{date.today().isoformat()}-{args.task}.md", output)
    first_line = output.strip().splitlines()[0][:80] if output.strip() else spec["title"]
    notifier.push(f"{spec['title']}:{first_line}",
                  output[:1800] + f"\n\n(全文: {path.name})", severity=2)
    log.info("%s 完成,已存 %s", args.task, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
