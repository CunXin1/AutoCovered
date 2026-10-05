"""定时任务入口:python -m src.run_task daily|weekly|patrol [--force]

- daily 自带交易日门禁、patrol 自带盘中门禁(调度器可以无脑触发)
- daily/weekly:输出全文存 state/analysis/,摘要推手机
- patrol:执行 scheduled-tasks skill 盘中巡检,存档与"有风险才推送"由 skill 自己做
- OS 调度器(Task Scheduler/launchd)和 Claude Code CronCreate 都调这个入口
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from src.claude_runner import DEFAULT_TOOLS, run_claude
from src.config import PROMPTS_DIR, load_config
from src.notify.push import Notifier
from src.state_store import StateStore

log = logging.getLogger(__name__)

# 巡检要自己刷数据、存档、按需推送(与 .claude/settings.json 白名单一致)
PATROL_TOOLS = (
    DEFAULT_TOOLS + ",Write(state/analysis/**),"
    "Bash(python -m src.watcher --once --no-trigger),"
    "Bash(python .claude/skills/covered-call/scripts/notify.py:*)"
)

# gate: None 不设门禁 / "day" 交易日 / "open" 盘中
# push: True = run_task 推摘要;False = 推送由 Claude 按 skill 自行决定(没事不吵)
TASKS = {
    "daily": {"prompt": "daily.md", "title": "📊 每日晨报", "timeout": 900,
              "gate": "day", "push": True, "tools": DEFAULT_TOOLS},
    "weekly": {"prompt": "weekly.md", "title": "📅 周度复盘", "timeout": 1200,
               "gate": None, "push": True, "tools": DEFAULT_TOOLS},
    "patrol": {"prompt": "patrol.md", "title": "🛡️ 盘中巡检", "timeout": 900,
               "gate": "open", "push": False, "tools": PATROL_TOOLS},
}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("task", choices=sorted(TASKS))
    ap.add_argument("--force", action="store_true", help="跳过交易日门禁")
    args = ap.parse_args(argv)

    spec = TASKS[args.task]
    if spec["gate"] and not args.force:
        from src.market_hours import is_market_open_now, is_trading_day

        if spec["gate"] == "day" and not is_trading_day():
            log.info("今天不是交易日,跳过 %s", args.task)
            return 0
        if spec["gate"] == "open" and not is_market_open_now():
            log.info("当前不在盘中,跳过 %s", args.task)
            return 0

    cfg = load_config()
    notifier = Notifier(cfg)
    store = StateStore()

    prompt = (PROMPTS_DIR / spec["prompt"]).read_text(encoding="utf-8")
    try:
        output = run_claude(prompt, allowed_tools=spec["tools"], timeout=spec["timeout"],
                            binary=(cfg.get("claude") or {}).get("binary", "claude"))
    except Exception as e:
        log.exception("%s 失败", args.task)
        notifier.push(f"⚠️ {spec['title']}失败", str(e)[:300], severity=2)
        return 1

    if not spec["push"]:
        log.info("%s 完成: %s", args.task, output.strip()[:200])
        return 0

    path = store.save_analysis(f"{date.today().isoformat()}-{args.task}.md", output)
    first_line = output.strip().splitlines()[0][:80] if output.strip() else spec["title"]
    notifier.push(f"{spec['title']}:{first_line}",
                  output[:1800] + f"\n\n(全文: {path.name})", severity=2)
    log.info("%s 完成,已存 %s", args.task, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
