# AutoCovered

Covered call 监控 + 半自动执行系统。架构:Python watcher 管数字,Claude 管叙述,
`state/` 目录是两层唯一接口。详见 README.md 与 `.claude/skills/covered-call/SKILL.md`。

## 铁律

- **所有数字来自 `state/positions.json` 或确定性脚本输出**;分析持仓时禁止自行
  计算/估算价格、delta、net credit、年化(用 roll_candidates.py 脚本拿数字)
- 改 `src/engine/*` 或 `src/execution/proposals.py` 必须跑 `python -m pytest`
  — 状态机是系统里唯一不许出错的部分
- `execution.enabled` 和 `execution.dry_run` 两个开关,未经用户明确要求不得改动
- `config/settings.yaml`、`config/lots.yaml`、`state/` 含个人数据,已 gitignore,
  不要提交;示例改动写到 `*.example.yaml`

## 常用入口

- `python -m pytest` — 引擎测试
- `python -m src.watcher --once` — 单轮冒烟(需 IB Gateway)
- `python -m src.run_task daily|weekly [--force]` — 晨报/周报
- `python .claude/skills/covered-call/scripts/roll_candidates.py TICKER [--mode open]`

## 结构速查

engine/(纯函数:状态机/pnl/qcc/roll)· brokers/(ibkr 主 + snaptrade 备,可插拔)·
notify/(ntfy 双向)· execution/(提案+批准执行)· prompts/(注入式模板)·
watcher.py(5s tick 命令 + 300s 行情周期)
