# 每日晨报(Claude Code 定时任务指令)

**调度建议:每个交易日本地 06:15(太平洋时间,= 美东 09:15 开盘前)。**
周末/节假日如被误触发:先判断今天是否美股交易日(state/positions.json 的
updated_at 或 WebSearch 确认),非交易日直接结束,不推送。

## 步骤

1. **刷新事实**:运行 `python -m src.watcher --once --no-trigger`
   (失败 = IB Gateway 离线,继续用现有 state,在简报开头声明数据时间)
2. **执行晨报**:按 `prompts/daily.md` 的完整要求执行
   (持仓总览 → UNCOVERED 开仓候选 → 隔夜新闻 → 财报/除息日历 → 待批提案提醒)
3. **存档**:Write 全文到 `state/analysis/YYYY-MM-DD-daily.md`
4. **推送**:
   `python .claude/skills/covered-call/scripts/notify.py --title "📊 晨报:<第一行总结>" --body-file state/analysis/YYYY-MM-DD-daily.md --severity 2`
