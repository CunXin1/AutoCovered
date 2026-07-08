持仓告警深度分析。使用 covered-call skill 的全部约束与格式要求。

触发上下文(规则引擎实时判定,以下数字为事实,勿重算):

- 标的: {ticker}
- 状态: {emoji} {state}
- 触发原因: {reasons}
- 持仓快照(含全部 metrics):

```json
{context_json}
```

任务:

1. WebSearch「{ticker} stock news」最新消息,用一两句话解释价格异动原因(如查不到就说明无明显消息面)。
2. 若状态为 ROLL_WINDOW / BREACHED / TESTED:运行
   `python .claude/skills/covered-call/scripts/roll_candidates.py {ticker}`
   获取 roll 候选(脚本报错则如实说明,不编造)。
3. **给出三个选项的对比:① roll(用脚本候选)② 买回平仓 ③ 让股票被叫走**。
   每个选项附:数字依据、税务影响(注意 metrics.days_to_long_term 与被叫走触发的
   资本利得)、反方观点。不要默认推荐 roll。
4. 如 state/proposals.json 中已有该持仓的待批提案,评价该提案是否合理。

输出 ≤500 字中文分析,结论先行,**第一行是一句话总结**(用作推送标题)。
你是决策支持,不下指令;最终由用户批准提案或手动在券商执行。
