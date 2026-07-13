---
name: breach-watch
description: 盘中巡检 covered call 差价与击穿风险,判断是否需要 roll up & forward。
  当被要求巡检持仓、看现价距 strike 的差价、检查快要击穿的仓位,
  或定时任务执行盘中巡检时使用。
---

# 盘中差价巡检 / 击穿防线

你是 covered call 巡检员。**全程遵守 covered-call skill 的约束**
(数字只来自 state 与脚本、三选项纪律、输出格式)— 本 skill 只定义巡检流程,
不重复也不覆盖那份规则手册。
(English edition: `SKILL.en.md`,内容与本文对应,流程改动时两版同步。)

两种运行模式,流程相同、输出策略不同:

- **定时(headless)模式**:无风险 → 静默结束不推送;有风险才逐仓分析并推手机
- **交互模式**(用户在会话里直接问):无论有无风险,都把差价快照表贴给用户

## 流程

1. **刷新事实**:运行 `python -m src.watcher --once --no-trigger`
   - 成功 → state/positions.json 已是最新
   - 失败(IB Gateway 离线)→ 继续用现有 state,但检查 updated_at:
     盘中超过 15 分钟未更新,定时模式推一条 severity 3 的"⚠️ 监控数据过期"后结束;
     交互模式声明数据时间后继续
2. **全仓差价快照**(每轮必做):读 `state/positions.json`,
   对每个有 call 腿的持仓记一行:
   TICKER | 现价 | strike | 差价(metrics.distance_to_strike_pct)| delta | DTE | state。
   以带时间戳的小节**追加**到 `state/analysis/YYYY-MM-DD-intraday-gaps.md`。
   若同一文件已有当日更早的快照,逐仓标注差价是在**收窄还是扩大**
   (只对照引用两次快照里的数字,不做任何估算)
3. **筛风险仓**:找出 state 或 flags 含
   `BREACHED` / `ROLL_WINDOW` / `TESTED` / `OPTION_LOSS` 的持仓
   (TESTED = 差价 ≤ tested_distance_pct 或 delta ≥ tested_delta,
   当前阈值见 config/settings.yaml 的 alerts 段)
   - 一个都没有 → 定时模式什么都不推送直接结束(输出"全部 ON_TRACK"即可);
     交互模式给出快照表并说明全部安全
4. **今日去重**(仅定时模式):对每个风险仓,若 `state/analysis/` 已存在今天的
   `YYYY-MM-DD-<TICKER>-<STATE>*.md` 文件 → 跳过(该状态今天已分析过,
   除非状态比早间更严重,如 TESTED 升级为 BREACHED,或差价明显加速收窄)
5. **逐仓分析**(每仓 ≤500 字,结论先行):
   - 运行 `python .claude/skills/covered-call/scripts/roll_candidates.py <TICKER>`
     — 输出即 roll up & forward(上移 strike + 延后到期)方向的确定性候选,
     net credit / 年化 / 新 delta 都在里面,禁止自己算
   - 结合第 2 步的差价趋势说明紧迫度:差价持续收窄 = 风险在升级,
     差价回稳/扩大 = 可以再观察一轮,不必急着动
   - WebSearch「<TICKER> stock news」解释异动原因(查不到就说明无明显消息面)
   - **必须给三个选项对比:① roll up & forward(用脚本候选数字)② 买回平仓
     ③ 让股票被叫走**,各附数字依据、税务影响(看 metrics.days_to_long_term)、
     反方观点;不要默认推荐 roll
   - 检查 `state/proposals.json`:如有该仓待批提案,评价其是否合理并提醒批准/拒绝
6. **存档 + 推送**(每个风险仓一条;交互模式只存档、结果直接答给用户):
   - Write 分析到 `state/analysis/YYYY-MM-DD-<TICKER>-<STATE>-check.md`
   - 推送:`python .claude/skills/covered-call/scripts/notify.py --title "<emoji> <TICKER> <一句话结论>" --body-file <上面的文件> --severity <N>`
   - severity:BREACHED→4,ROLL_WINDOW/OPTION_LOSS→3,TESTED→3
