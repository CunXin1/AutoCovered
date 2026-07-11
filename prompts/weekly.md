执行 covered call 周度复盘。使用 covered-call skill 的全部约束与格式要求。

任务(按顺序):

1. 读 `state/positions.json` 与本周 `state/alerts.jsonl`,总结:
   本周状态跃迁(哪些仓被 tested/breached/止盈)、当前各仓状态。
2. 全持仓下周计划:对每个有空头腿的仓,给出「持有到期 / 止盈平仓 / roll」建议;
   需要候选数字时运行 roll_candidates.py 脚本(Gateway 离线则如实说明)。
3. 收益核算(用 metrics/alerts/stats 的数字,不要自行估算):
   - **历史实现收益**:运行 `python -m src.stats`,引用其 per-ticker 实现盈亏、
     round 级与 roll 链级两套胜率,以及数据质量分层(推断价部分如实标注,
     提醒用户可用 CONFIRM 修正)
   - 本周权利金浮盈变化(各仓 option_pnl_per_share × 张数 × 100)
   - **诚实指标**:被 call 封顶而放弃的上涨(BREACHED 仓的 现价−strike 部分
     + stats 输出的 assigned 放弃上涨)vs 收到的权利金 —
     covered call 在暴涨市会跑输,直说
4. QCC 合规审计:检查所有空头腿是否 OTM、开仓 DTE>30;
   列出每个未满一年持仓的 days_to_long_term。
5. 下周事件日历:财报/除息(WebSearch 交叉验证财报日期)。
6. 运维检查:positions.json 的 updated_at 与 data_source 是否正常;
   `state/proposals.json` 有无遗留 pending/submitted;stats 输出如显示
   有待 CONFIRM 的推断价交易,列出提醒;如启用了 Schwab 直连,提醒
   refresh token 需要每 7 天重新授权。

输出:中文周报,结论先行,**第一行是一句话总结**(用作推送标题)。
按「本周回顾 → 下周计划 → 收益与税务 → 事件与运维」组织,可以比晨报长。
