# AutoCovered

Covered call 监控 + 半自动执行系统。架构:Python watcher 管数字,Claude 管叙述,
`state/` 目录是两层唯一接口。详见 README.md 与 `.claude/skills/covered-call/SKILL.md`。

## 账户模型(2026-10-02 起)

正股分散在 IBKR 的三个账户,**全部**在 covered call 作用域内。因此:

- `Position.account` = 账户身份(IBKR 账户号;外部券商为标签如 `schwab`),
  `position_id` 带 `@account` 后缀 —— 同标的跨账户不撞 key
- `Position.external` = 是否外部只读账户,**作用域判断只看这个,不看 account 是否为空**
  (旧代码用 `not p.account` 判"主账户",多账户下会把两个 IBKR 账户全漏掉)
- 覆盖率、指派推断、账本 round、下单账户全部按账户隔离:A 账户的股票
  覆盖不了 B 账户的 call。开仓提案跨账户时必须 `--account`
- 下单必带 account:多账户会话里不指定,TWS 会自己挑,挑错 = 裸卖

## 铁律

- **所有数字来自 `state/positions.json` 或确定性脚本输出**;分析持仓时禁止自行
  计算/估算价格、delta、net credit、年化(用 roll_candidates.py 脚本拿数字)
- 账本 `state/ledger.db` 只能经 `python -m src.stats` 读取(禁止直接 SQL);
  历史收益必须引用它的输出,含数据质量分层
- OPEN_CALL 提案只能经 `python -m src.execution.propose` 创建(候选集护栏),
  禁止手改 `state/proposals.json` 或绕过提案直接调 broker 下单
- 改 `src/engine/*`、`src/execution/*`、`src/ledger.py`、`src/stats.py`
  必须跑 `python -m pytest` — 状态机与账本是系统里不许出错的部分
- `execution.enabled` 和 `execution.dry_run` 两个开关,未经用户明确要求不得改动
- `config/settings.yaml`、`config/lots.yaml`、`state/` 含个人数据,已 gitignore,
  不要提交;示例改动写到 `*.example.yaml`

## 常用入口

- `python -m pytest` — 引擎测试
- `python -m src.watcher --once` — 单轮冒烟/刷数据(需 IB Gateway;只读,不写账本)
- `python -m src.run_task daily|weekly [--force]` — 晨报/周报
- `python .claude/skills/covered-call/scripts/roll_candidates.py TICKER [--mode open] [--style conservative|aggressive]`
- `python -m src.execution.propose TICKER --strike K --expiry D --contracts N [--style S]` — 开仓提案(推手机批准)
- `python -m src.stats [--ticker X]` — 历史收益统计(账本唯一读取口)
- `python -m src.reconcile --flex [--apply]` — 从 IBKR Flex 报表补录历史成交
  (缺省只打印计划;账本单写者纪律:--apply 前先停 daemon watcher)

## 结构速查

engine/(纯函数:状态机/pnl/qcc/roll/lifecycle 生命周期推断)·
brokers/(ibkr 主 + snaptrade 备/次级,可插拔;连接自愈+executions 对账)·
reconcile.py(IBKR Flex 报表补录:API 只给当日成交,停机期的历史只能从报表取)·
notify/(ntfy 双向)· execution/(提案+propose 护栏+批准执行)·
ledger.py(SQLite 账本,exec_id 幂等)· stats.py(收益统计)·
prompts/(注入式模板)· watcher.py(5s tick 命令 + 300s 行情周期;账本常规写者,
另有 reconcile.py 按需补录,两者不可并发)
