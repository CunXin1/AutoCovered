# AutoCovered

**Covered call lifecycle automation: a deterministic Python watcher for the numbers, Claude Code for the judgment, and your phone for approvals.**

Monitors covered call positions (IBKR / Schwab) every 5 minutes, runs a tested
P&L state machine, pushes alerts *with suggestions* to your phone (ntfy, two-way),
generates AI morning briefings / weekly reviews / event deep-dives via Claude Code,
and — only after you tap **Approve** — can place net-credit roll orders (off by default).

> 核心纪律:**Python 管数字,Claude 管叙述**。所有 P&L/delta/net credit 由确定性
> Python 计算并写入 `state/positions.json`;Claude 只读这份事实做新闻融合、方案
> 对比与税务解释 — 它没有机会幻觉任何数字。

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
到期不跨财报、高波动股部分覆盖 + 低 delta、未满一年持仓标注长期资本利得倒计时。

## 快速开始

```bash
pip install -r requirements.txt
copy config\settings.example.yaml config\settings.yaml   # 填 ntfy topic、IBKR 端口
copy config\lots.example.yaml config\lots.yaml           # 填建仓日期(税务)
python -m pytest                                         # 引擎测试应全绿
python -m src.watcher --once                             # 单轮冒烟(需 IB Gateway,paper 7497)
```

常驻部署(Windows Task Scheduler / macOS launchd)见 [`deploy/README.md`](deploy/README.md)。

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
