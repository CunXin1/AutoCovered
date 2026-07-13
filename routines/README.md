# routines/ — 面向 Claude 的定时任务指令

这个目录是"给 Claude 看的程序":每个 .md 是一份自然语言任务指令,由 Claude Code
的定时任务(headless Claude 会话)到点执行。它们能读 state/、用 covered-call skill、
跑白名单脚本、WebSearch,最后用 notify.py 推手机 — 全程受 `.claude/settings.json`
权限约束,无需人工干预。

| 指令文件 | 建议调度 | 干什么 |
|---|---|---|
| `daily-briefing.md` | 交易日 06:15 | 晨报:持仓总览+开仓建议+新闻+事件日历 |
| `breach-roll-check.md` | 盘中每 2 小时 | **击穿防线/差价/该不该 roll 巡检**(执行 breach-watch skill),有风险才推送 |
| `weekly-review.md` | 周日 18:00 | 周报:roll 计划+收益核算+QCC 审计 |

## 怎么注册(三选一)

### A. Claude Code Desktop(推荐,在窗口里管理)

侧边栏 → **Routines** → **New routine** → 选 **Local**(必须选 Local,
云端 routine 读不到本地 state 文件!)→ 指令栏填:

> 在 <AutoCovered 仓库的绝对路径> 目录下,阅读并严格执行
> routines/daily-briefing.md 的指令。

设好时间后先点 **Run now** 测试一遍。三个任务各建一条。

### B. 对着 Claude Code 会话直接说

在本仓库的会话里说:"帮我建一个定时任务,每个交易日早上 6:15 执行
routines/daily-briefing.md 的指令" — Claude 会用内置 Cron 工具创建。

### C. OS 调度器(可靠性备份)

`deploy/` 里的 Task Scheduler/launchd 脚本走 `python -m src.run_task`,
和本目录相互独立,别对同一任务两边都开(会推送两遍)。

## 权限说明

这些任务依赖 `.claude/settings.json` 白名单里的:
`watcher --once --no-trigger`(刷新数据)、`roll_candidates.py`(候选计算)、
`notify.py`(推送)、`Write(state/analysis/**)`(存档)。改指令文件时不要
引入白名单之外的命令,否则 headless 运行会被卡住。

## 与 Python watcher 的关系

Python watcher(常驻,5 分钟)是第一道防线:秒级推送状态跃迁+生成带按钮的提案。
本目录的巡检是第二道:Claude 带着新闻和税务上下文做叙述判断。两者互补 —
watcher 没跑起来时,盘中巡检任务也能独立工作(它自己会刷新一轮数据)。
