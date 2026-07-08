#!/usr/bin/env python
"""手机推送 CLI — 供 Claude 会话(定时任务/窗口交互)把结果推到 ntfy/Bark。

用法(在仓库根目录):
    python .claude/skills/covered-call/scripts/notify.py --title "标题" --body "正文" [--severity 0-4]
    python .claude/skills/covered-call/scripts/notify.py --title "标题" --body-file state/analysis/xxx.md --severity 3

severity:0 静默 / 1 低 / 2 默认 / 3 高(🟠) / 4 紧急响铃(🔴)。正文超 1800 字自动截断。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from src.config import load_config       # noqa: E402
from src.notify.push import Notifier     # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--body", default="")
    ap.add_argument("--body-file", default="", help="从文件读正文(推荐:先把分析 Write 到 state/analysis/ 再引用)")
    ap.add_argument("--severity", type=int, default=2, choices=range(0, 5))
    args = ap.parse_args()

    body = args.body
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    elif not body and not sys.stdin.isatty():
        body = sys.stdin.buffer.read().decode("utf-8", errors="replace")

    ok = Notifier(load_config()).push(args.title, body[:1800], severity=args.severity)
    print("已推送" if ok else "推送失败(检查 settings.yaml 的 notify 配置)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
