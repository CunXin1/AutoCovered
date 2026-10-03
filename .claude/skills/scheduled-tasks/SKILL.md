---
name: scheduled-tasks
description: 执行 covered call 的定时任务:每日晨报、盘中击穿/差价巡检(快击穿就给
  roll up & forward 方案)、周度复盘。当定时任务到点触发,或被要求"跑晨报/跑巡检/
  跑周报"、"看看有没有快击穿的仓"时使用。(交互式的持仓分析与开仓研究属于
  covered-call skill)
---

# 定时任务执行器

你是 covered call 系统的值班员。本 skill 管**按时间触发的三件事**;
用户在会话里要"分析持仓 / 该卖什么 call / 出报告"时走 covered-call skill,不走本文。

**全程遵守 covered-call skill 的约束**(数字只来自 state 与脚本、三选项纪律、
禁止自行估算)—— 本 skill 只定义各任务的流程与输出策略,不重复也不覆盖那份规则手册。
(English edition: `SKILL.en.md`,内容与本文对应,流程改动时两版同步。)

## 任务分派

| 任务 | 建议调度 | 指令文件 | 本文章节 |
|---|---|---|---|
| 每日晨报 | 交易日 06:15 本地(= 美东 09:15 盘前) | `routines/daily-briefing.md` | 一 |
| 盘中击穿/差价巡检 | 盘中每 2 小时 | `routines/breach-roll-check.md` | 二 |
| 周度复盘 | 周日 18:00 本地 | `routines/weekly-review.md` | 三 |

调度器只负责到点触发,**门禁在本文**:非交易日的晨报/巡检直接结束不推送。
`routines/*.md` 是给调度器的入口(一句话指向本文),内容细节以本文为准。

## 两种运行模式

- **定时(headless)模式**:无人看屏幕,所以"没事就别吵"——
  无风险静默结束;有结论才存档 + 推手机
- **交互模式**(用户在会话里说"跑一下巡检"):无论有无风险都把结果贴给用户,
  **存档但不推手机**(人就在眼前,推送是噪音)

## 通用第 0 步:刷新事实

每个任务开始前都跑 `python -m src.watcher --once --no-trigger`。

- 成功 → `state/positions.json` 已是最新
- 失败(IB Gateway 离线)→ 继续用现有 state,但检查 `updated_at`:
  - 盘中超过 15 分钟未更新:定时模式推一条 severity 3 的"⚠️ 监控数据过期"后结束;
    交互模式声明数据时间后继续
  - 盘外(周末/收盘后)→ 用最后快照属正常,声明数据时间即可

---

## 一、每日晨报

按 `prompts/daily.md` 的完整要求执行:持仓总览 → UNCOVERED 开仓候选 →
隔夜新闻 → 财报/除息日历 → 待批提案提醒。

1. 非交易日(用 `state/positions.json` 的 updated_at 或 WebSearch 确认)→ 直接结束,不推送
2. 执行 `prompts/daily.md`
3. **存档**:Write 全文到 `state/analysis/YYYY-MM-DD-daily.md`
4. **推送**(仅定时模式):
   `python .claude/skills/covered-call/scripts/notify.py --title "📊 晨报:<第一行总结>" --body-file state/analysis/YYYY-MM-DD-daily.md --severity 2`

晨报 ≤800 字,第一行必须是一句话总结(会被当推送标题)。
开仓候选只给候选与方向,**不做 9 维研究、不出最终 strike 结论** ——
那是 covered-call skill 的交互流程,晨报里提示用户"在会话里说『给 XX 开仓』
可走完整研究 + 提案流程"。

## 二、盘中击穿/差价巡检

核心任务:现价有没有逼近 call 的 strike,要不要 roll up & forward。

1. **全仓差价快照**(每轮必做):读 `state/positions.json`,
   对每个有 call 腿的持仓记一行:
   TICKER | 现价 | strike | 差价(metrics.distance_to_strike_pct)| delta | DTE | state。
   以带时间戳的小节**追加**到 `state/analysis/YYYY-MM-DD-intraday-gaps.md`。
   若同一文件已有当日更早的快照,逐仓标注差价是在**收窄还是扩大**
   (只对照引用两次快照里的数字,不做任何估算)
2. **筛风险仓**:找出 state 或 flags 含
   `BREACHED` / `ROLL_WINDOW` / `TESTED` / `OPTION_LOSS` 的持仓
   (TESTED = 差价 ≤ tested_distance_pct 或 delta ≥ tested_delta,
   当前阈值见 `config/settings.yaml` 的 alerts 段)
   - 一个都没有 → 定时模式什么都不推送直接结束(输出"全部 ON_TRACK"即可);
     交互模式给出快照表并说明全部安全
3. **今日去重**(仅定时模式):对每个风险仓,若 `state/analysis/` 已存在今天的
   `YYYY-MM-DD-<TICKER>-<STATE>*.md` → 跳过(该状态今天已分析过),
   除非状态比早间更严重(如 TESTED 升级为 BREACHED)或差价明显加速收窄
4. **逐仓分析**(每仓 ≤500 字,结论先行):
   - 运行 `python .claude/skills/covered-call/scripts/roll_candidates.py <TICKER>`
     — 输出即 roll up & forward(上移 strike + 延后到期)方向的确定性候选,
     net credit / 年化 / 新 delta 都在里面,禁止自己算
   - 新腿若跨财报:按 covered-call skill 的跨财报规则定价 ——
     跑 `python -m src.data.earnings_moves <TICKER> --strike <新 strike>`
     拿历史击穿率,不许凭感觉判断"财报扛不扛得住"
   - 结合第 1 步的差价趋势说明紧迫度:差价持续收窄 = 风险在升级,
     差价回稳/扩大 = 可以再观察一轮,不必急着动
   - WebSearch「<TICKER> stock news」解释异动原因(查不到就说明无明显消息面)
   - **必须给三个选项对比:① roll up & forward(用脚本候选数字)② 买回平仓
     ③ 让股票被叫走**,各附数字依据、税务影响(看 metrics.days_to_long_term)、
     反方观点;不要默认推荐 roll
   - 检查 `state/proposals.json`:如有该仓待批提案,评价其是否合理并提醒批准/拒绝
5. **存档 + 推送**(每个风险仓一条;交互模式只存档):
   - Write 分析到 `state/analysis/YYYY-MM-DD-<TICKER>-<STATE>-check.md`
   - 推送:`python .claude/skills/covered-call/scripts/notify.py --title "<emoji> <TICKER> <一句话结论>" --body-file <上面的文件> --severity <N>`
   - severity:BREACHED→4,ROLL_WINDOW/OPTION_LOSS→3,TESTED→3

**roll 没有提案通道**:propose CLI 只支持开仓。用户决定 roll 后要在 TWS 手动执行
(先买回旧腿再卖新腿,或用 combo 单,限价挂 mid 附近,绝不市价);
watcher 会自动对账入账,推断价格的记录会推手机请用户 `CONFIRM <trade_id> @<价>` 修正。

## 三、周度复盘

按 `prompts/weekly.md` 的完整要求执行:本周回顾 → 下周 roll 计划 →
收益核算(含"放弃的上涨"诚实指标)→ QCC 审计 → 事件日历 → 运维检查。

1. 周日休市,Gateway 大概率离线 —— 第 0 步失败属正常,用周五收盘的 state,声明数据时间
2. 执行 `prompts/weekly.md`;收益数字一律引用 `python -m src.stats`
   (注意它默认只算 `stats.since` 之后开仓的轮次,以及数据质量分层)
3. **存档**:Write 全文到 `state/analysis/YYYY-MM-DD-weekly.md`
4. **推送**(仅定时模式):
   `python .claude/skills/covered-call/scripts/notify.py --title "📅 周报:<第一行总结>" --body-file state/analysis/YYYY-MM-DD-weekly.md --severity 2`

周报可以比晨报长,第一行同样是一句话总结。

---

## 权限与边界

依赖 `.claude/settings.json` 白名单:`watcher --once --no-trigger`、
`roll_candidates.py`、`notify.py`、`Write(state/analysis/**)`。
改本文或 `routines/` 时不要引入白名单之外的命令,否则 headless 运行会被卡住。

- **不下单、不改提案**:执行只能由用户在手机上批准提案,或自己在券商操作
- **不做开仓的最终 strike 结论**:9 维研究是 covered-call skill 的交互流程
- 与 Python watcher 的分工:watcher(常驻 5 分钟)是第一道防线,
  秒级推送状态跃迁 + 生成带按钮的提案;本 skill 是第二道,
  带着新闻与税务上下文做叙述判断。watcher 没跑时,本 skill 自带的第 0 步
  也能独立刷新一轮数据
