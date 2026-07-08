# 盘中巡检:击穿防线 / Roll 判定(Claude Code 定时任务指令)

**调度建议:交易日盘中每 2 小时(如本地 07:00 / 09:00 / 11:00 / 13:00)。**
你是 covered call 巡检员,全程遵守 covered-call skill 的约束(数字只来自 state 与脚本)。

## 步骤

1. **刷新事实**:运行 `python -m src.watcher --once --no-trigger`
   - 成功 → state/positions.json 已是最新
   - 失败(IB Gateway 离线)→ 继续用现有 state,但检查 updated_at:
     盘中超过 15 分钟未更新,推送一条 severity 3 的"⚠️ 监控数据过期"提醒后结束
2. **筛风险仓**:读 `state/positions.json`,找出 state 或 flags 含
   `BREACHED` / `ROLL_WINDOW` / `TESTED` / `OPTION_LOSS` 的持仓
   - **一个都没有 → 什么都不推送,直接结束**(输出"全部 ON_TRACK"即可,不要打扰用户)
3. **今日去重**:对每个风险仓,若 `state/analysis/` 已存在今天的
   `YYYY-MM-DD-<TICKER>-<STATE>*.md` 文件 → 跳过(该状态今天已分析过,
   除非状态比早间更严重,如 TESTED 升级为 BREACHED)
4. **逐仓分析**(每仓 ≤500 字,结论先行):
   - 运行 `python .claude/skills/covered-call/scripts/roll_candidates.py <TICKER>` 拿 roll 候选
   - WebSearch「<TICKER> stock news」解释异动原因(查不到就说明无明显消息面)
   - **必须给三个选项对比:① roll(用脚本候选数字)② 买回平仓 ③ 让股票被叫走**,
     各附数字依据、税务影响(看 metrics.days_to_long_term)、反方观点;不要默认推荐 roll
   - 检查 `state/proposals.json`:如有该仓待批提案,评价其是否合理并提醒批准/拒绝
5. **存档 + 推送**(每个风险仓一条):
   - Write 分析到 `state/analysis/YYYY-MM-DD-<TICKER>-<STATE>-check.md`
   - 推送:`python .claude/skills/covered-call/scripts/notify.py --title "<emoji> <TICKER> <一句话结论>" --body-file <上面的文件> --severity <N>`
   - severity:BREACHED→4,ROLL_WINDOW/OPTION_LOSS→3,TESTED→3
