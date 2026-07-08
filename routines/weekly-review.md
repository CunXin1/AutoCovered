# 周度复盘(Claude Code 定时任务指令)

**调度建议:每周日本地 18:00。**

## 步骤

1. **刷新事实**:运行 `python -m src.watcher --once --no-trigger`
   (周日休市,Gateway 大概率离线 — 失败属正常,用周五收盘的 state 即可,声明数据时间)
2. **执行周报**:按 `prompts/weekly.md` 的完整要求执行
   (本周回顾 → 下周 roll 计划 → 收益核算含"放弃的上涨"诚实指标 → QCC 审计
   → 事件日历 → 运维检查)
3. **存档**:Write 全文到 `state/analysis/YYYY-MM-DD-weekly.md`
4. **推送**:
   `python .claude/skills/covered-call/scripts/notify.py --title "📅 周报:<第一行总结>" --body-file state/analysis/YYYY-MM-DD-weekly.md --severity 2`
