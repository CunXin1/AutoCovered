# AutoCovered

**Covered-call lifecycle automation: a deterministic Python watcher for the numbers, Claude Code for the judgment, and your phone for approvals.**

*English | [中文](#中文)*

Monitors covered-call positions (IBKR / Schwab) every 5 minutes, runs a tested
P&L state machine, pushes alerts *with suggestions* to your phone (ntfy, two-way),
generates AI morning briefings / weekly reviews / event deep-dives via Claude Code,
and — only after you tap **Approve** — can place net-credit roll orders (off by default).

> **Core discipline: Python owns the numbers, Claude owns the narrative.** Every
> P&L / delta / net-credit figure is computed deterministically by Python and
> written to `state/positions.json`. Claude only *reads* those facts to fuse news,
> compare options, and explain tax implications — it never has a chance to
> hallucinate a number.

## Architecture

```
[IBKR Gateway]──ib_async (live Greeks, primary)──┐
[SnapTrade]────read-only snapshot (2 brokers, fallback)──┤  brokers/ pluggable
                                                 ▼
┌─ Watcher (Python daemon, no LLM) ─────────────────────────────┐
│ every 5 min: positions + Greeks → state machine → positions.json │
│ state transition → ntfy push to phone (numbers + suggestion)     │
│ 🟠🔴 → roll proposal (✅approve / ❌reject buttons) + trigger Claude │
└──────────────────┬────────────────────────────────────────────┘
                   │ state/ is the only interface
┌──────────────────▼────────────────────────────────────────────┐
│ Claude Code: morning briefing (trading day 6:15) / weekly (Sun) │
│ reads state + runs deterministic scripts + WebSearch news       │
│ Also interactive in the Claude Code window: "check my positions" │
└────────────────────────────────────────────────────────────────┘
```

## State machine (10 states)

| State | Trigger | Action |
|---|---|---|
| 🔴 BREACHED | spot > strike | immediate push + roll proposal + Claude 3-way analysis (roll / buy-back / let it get called) |
| 🟠 ROLL_WINDOW | delta ≥ 0.60 and 14–30 DTE | push + proposal + option comparison |
| 🟠 EVENT_RISK | expiry crosses earnings / high delta before ex-div | push 3 days ahead |
| 🟡 TESTED | within 3% of strike or delta ≥ 0.45 | push + Claude checks news to explain the move |
| 🟡 OPTION_LOSS | call-leg buy-back cost ≥ 2× premium | push stop-loss suggestion |
| 🟡 EXPIRING | DTE ≤ 7 | daily reminder |
| 🟡 MANAGE_DTE | DTE ≤ 21 | management-window reminder (dodge gamma) |
| 🟢 PROFIT_TAKE | ≥ 50% of premium earned back | can close to lock in |
| ⚪ UNCOVERED | ≥ 100 shares, no call | briefing suggests QCC-compliant open candidates |
| ⚪ ON_TRACK | default | silent |

Built-in discipline (configurable): sell OTM + DTE > 30 only (QCC tax
compliance), roll for net credit only, expiries crossing earnings are allowed
but carry a ⚠️ marker (the analysis layer must price the earnings gap risk and
event premium explicitly), partial coverage + low delta for high-volatility
names, and a long-term capital-gains countdown flagged on positions held under
a year.

## Quick start

```bash
pip install -r requirements.txt
cp config/settings.example.yaml config/settings.yaml   # fill in ntfy topic, IBKR port
cp config/lots.example.yaml config/lots.yaml           # fill in open dates (for tax)
python -m pytest                                       # engine tests should all pass
python -m src.watcher --once                           # one smoke tick (needs IB Gateway, paper 7497)
```

> On Windows, use `copy config\settings.example.yaml config\settings.yaml`.

For a resident deployment (macOS launchd / Windows Task Scheduler) see
[`deploy/README.md`](deploy/README.md).

**Phone:** install the [ntfy](https://ntfy.sh) app and subscribe to the two
topics you named in `settings.yaml` (the topic name *is* the password — use a
long random string). Commands: `STATUS` / `ANALYZE NVDA` / `APPROVE <proposal-id>`
/ `REJECT <proposal-id>` (proposal pushes ship with buttons).

## Semi-automatic execution (off by default)

Out of the box this is a pure decision-support tool. Turning on semi-auto passes
three gates, recommended in order:

1. `execution.enabled: true` + `dry_run: true` → simulate fills after approval, rehearse first
2. **paper account** (port 7497 / 4002) to test the combo order-symbol convention
3. Only then set `dry_run: false`. Daily order cap, 30-minute proposal expiry, limit orders only.

The system **never** places an order without approval from your phone.
There is no fully automatic mode, and none will be added.

## Three modules

1. **Opening (semi-auto):** in the Claude Code window say "open a call on NVDA" →
   Claude runs the deterministic candidate script
   (`--style conservative|aggressive`; per-ticker coverage is a hard cap) +
   WebSearch research → once you pick, the `propose` CLI validates candidate-set
   membership + coverage against live quotes → pushes to phone (✅/❌ buttons,
   `APPROVE <id> @<price>` to adjust the limit) → on approval the executor
   re-validates and places a SELL limit order (DAY, attributed via orderRef).
2. **Monitoring:** the watcher runs the state machine every 5 minutes; on
   approach/breach of a strike (TESTED / BREACHED) it pushes to your phone and
   triggers Claude to analyze the 3-way roll decision.
3. **Ledger & stats:** every fill (system order + manual TWS order) is reconciled
   into a SQLite ledger via IBKR executions (`state/ledger.db`, idempotent on
   exec_id); expiry/assignment is inferred from position diff + official close,
   and an inferred price can be corrected with `CONFIRM <trade_id> @<price>`.
   `python -m src.stats` reports per-ticker realized P&L, round-level and
   roll-chain win rates, a data-quality tier, and upside given up when called away.

## Using it inside Claude Code

This repo ships a `covered-call` skill. Open Claude Code in the repo directory
and just say: "check my positions" / "run a morning briefing" / "should I roll
that NVDA call now" / "open a call on MSFT, keep it conservative" / "how much
have I made selling CCs per ticker".

**Claude Code scheduled tasks:** the `routines/` directory holds three
Claude-facing task instructions — morning briefing (trading day 6:15), intraday
breach/roll patrol (every 2 hours, pushes only on risk), and weekly review
(Sunday). See [`routines/README.md`](routines/README.md) to register them; the
traditional OS-scheduler route is in [`deploy/README.md`](deploy/README.md) —
pick one, not both.

## Disclaimer

This is a personal decision-support tool and is **not investment advice**. Its
tax logic is a simplified internal discipline (the OTM + DTE > 30 sufficient
condition for a Qualified Covered Call), not a basis for filing taxes. Options
carry risk; paper-test thoroughly before enabling live orders. MIT License.

---

# 中文

**Covered call 生命周期自动化:确定性 Python watcher 管数字,Claude Code 管判断,手机管批准。**

*[English](#autocovered) | 中文*

每 5 分钟监控 covered call 持仓(IBKR / Schwab),跑一套有测试的 P&L 状态机,
把带*建议*的告警推到手机(ntfy,双向),用 Claude Code 生成 AI 晨报 / 周报 /
事件深度分析,并且——只在你点了 **Approve** 之后——才下 net credit 的 roll 单(默认关闭)。

> 核心纪律:**Python 管数字,Claude 管叙述**。所有 P&L / delta / net credit 由
> 确定性 Python 计算并写入 `state/positions.json`;Claude 只读这份事实做新闻融合、
> 方案对比与税务解释——它没有机会幻觉任何数字。

## 架构

```
[IBKR Gateway]──ib_async(实时 Greeks,主)──┐
[SnapTrade]────只读快照(两家券商,备胎)────┤  brokers/ 可插拔
                                          ▼
┌─ Watcher(Python 常驻,无 LLM)──────────────────────────┐
│ 每 5 分钟:持仓+Greeks → 状态机 → state/positions.json │
│ 状态跃迁 → ntfy 推手机(数字+建议)                     │
│ 🟠🔴 → roll 提案(✅批准/❌拒绝按钮)+ 触发 Claude 分析  │
└──────────────────┬────────────────────────────────────┘
                   │ state/ 是唯一接口
┌──────────────────▼────────────────────────────────────┐
│ Claude Code:晨报(交易日 6:15)/ 周报(周日)/ 事件分析 │
│ 读 state + 跑确定性脚本 + WebSearch 新闻 → 中文简报    │
│ 也可直接在 Claude Code 窗口交互:"看一下持仓状态"       │
└───────────────────────────────────────────────────────┘
```

## 状态机(10 态)

| 状态 | 触发 | 动作 |
|---|---|---|
| 🔴 BREACHED | 现价 > strike | 立即推送 + roll 提案 + Claude 三选一分析(roll/买回/被叫走) |
| 🟠 ROLL_WINDOW | delta≥0.60 且 14–30 DTE | 推送 + 提案 + 方案对比 |
| 🟠 EVENT_RISK | 到期跨财报 / 除息前高 delta | 提前 3 天推送 |
| 🟡 TESTED | 距 strike≤3% 或 delta≥0.45 | 推送 + Claude 查新闻解释异动 |
| 🟡 OPTION_LOSS | call 腿买回成本 ≥ 2× 权利金 | 推送止损建议 |
| 🟡 EXPIRING | DTE≤7 | 每日提醒 |
| 🟡 MANAGE_DTE | DTE≤21 | 管理窗口提醒(躲 gamma) |
| 🟢 PROFIT_TAKE | 已赚回 ≥50% 权利金 | 可平仓锁定 |
| ⚪ UNCOVERED | 持股≥100 无 call | 晨报给 QCC 合规开仓候选 |
| ⚪ ON_TRACK | 默认 | 静默 |

内置纪律(可配置):只卖 OTM + DTE>30(QCC 税务合规)、roll 只做 net credit、
跨财报到期允许但候选带 ⚠️ 标记(财报 gap 风险与事件溢价必须由分析层显式定价)、
高波动股部分覆盖 + 低 delta、未满一年持仓标注长期资本利得倒计时。

## 快速开始

```bash
pip install -r requirements.txt
cp config/settings.example.yaml config/settings.yaml   # 填 ntfy topic、IBKR 端口
cp config/lots.example.yaml config/lots.yaml           # 填建仓日期(税务)
python -m pytest                                       # 引擎测试应全绿
python -m src.watcher --once                           # 单轮冒烟(需 IB Gateway,paper 7497)
```

> Windows 用 `copy config\settings.example.yaml config\settings.yaml`。

常驻部署(macOS launchd / Windows Task Scheduler)见 [`deploy/README.md`](deploy/README.md)。

手机端:装 [ntfy](https://ntfy.sh) App,订阅你在 settings.yaml 里起的两个 topic
(topic 名等于密码,用随机长字符串)。可用命令:`STATUS` / `ANALYZE NVDA` /
`APPROVE <提案id>` / `REJECT <提案id>`(提案推送自带按钮)。

## 半自动执行(默认关闭)

出厂 = 纯决策支持。打开半自动要过三道门,建议按顺序:

1. `execution.enabled: true` + `dry_run: true` → 批准后模拟成交,先演练
2. **paper 账户**(端口 7497/4002)实测 combo 下单符号约定
3. 确认无误后再 `dry_run: false`。每日下单上限、30 分钟提案过期、只下限价单

系统**永远不会**未经手机批准自行下单。全自动模式不存在,也不会加。

## 三大模块

1. **开仓(半自动)**:在 Claude Code 里说"给 NVDA 开仓"→ Claude 跑确定性候选
   脚本(`--style conservative|aggressive`,per-ticker 覆盖是硬上限)+ WebSearch
   研究 → 你选定后 `propose` CLI 用实时报价做候选集成员资格 + 覆盖率校验 →
   推手机(✅/❌ 按钮,`APPROVE <id> @<价>` 可改限价)→ 批准后执行器
   二次校验再下 SELL 限价单(DAY,orderRef 归因)。
2. **监控**:watcher 每 5 分钟跑状态机,靠近/突破 strike(TESTED/BREACHED)
   即推手机 + 触发 Claude 分析 roll 三选一。
3. **记账与统计**:所有成交(系统单 + TWS 手动单)经 IBKR executions 对账入
   SQLite 账本(`state/ledger.db`,exec_id 幂等);到期/指派由持仓 diff +
   官方收盘价推断,推断价可 `CONFIRM <trade_id> @<价>` 修正。
   `python -m src.stats` 输出 per-ticker 实现盈亏、round 级 + roll 链级胜率、
   数据质量分层、被叫走的放弃上涨。

## 在 Claude Code 窗口里用

本仓库自带 `covered-call` skill。在仓库目录开 Claude Code 直接说:
"看一下持仓状态" / "跑一份晨报" / "NVDA 那条 call 现在该不该 roll" /
"给 MSFT 开仓,保守一点" / "统计一下每只股票卖 CC 赚了多少"。

**Claude Code 定时任务**:`routines/` 目录有三份面向 Claude 的任务指令 —
晨报(交易日 6:15)、盘中击穿/roll 巡检(每 2 小时,有风险才推送)、周报(周日)。
注册方法见 [`routines/README.md`](routines/README.md);传统 OS 调度器路线见
[`deploy/README.md`](deploy/README.md),二选一即可。

## 免责声明

本项目是个人决策支持工具,不构成投资建议;税务逻辑为简化的内部纪律
(OTM+DTE>30 的 QCC 充分条件),不作报税依据。期权有风险,启用真实下单前
请充分 paper 测试。MIT License。
