执行 covered call 每日晨报。使用 covered-call skill 的全部约束与格式要求。

任务(按顺序):

1. 读 `state/positions.json`,总览所有持仓状态(state/reasons 已由规则引擎判定,
   metrics 已算好,勿重新计算任何数字)。先检查 updated_at 是否过期。
2. 对每个 UNCOVERED 持仓:运行
   `python .claude/skills/covered-call/scripts/roll_candidates.py <TICKER> --mode open`
   获取开仓候选(可各跑一次 --style conservative / aggressive 给两档对比),
   只推荐 QCC 合规选项;结合 per-ticker 覆盖比例给出建议张数,并提示
   "在 Claude Code 窗口说『给 XX 开仓』可走完整研究+提案流程"。
   (脚本报错说明 IB Gateway 离线,如实说明即可,不要编造候选。)
3. WebSearch 每个持仓标的的隔夜重大新闻(财报、评级、产品、宏观),
   只报有实质影响的,每条一句话;无新闻的标的不用提。
4. 列出未来 7 天的财报/除息日历(positions.json 的 events 字段,
   对财报日期用 WebSearch 交叉验证一次)。
5. 检查 `state/proposals.json` 有无待批提案,有则提醒。

输出:中文简报,结论先行,总长 ≤800 字。
**第一行必须是一句话总结**(将用作手机推送标题),
之后按「持仓状态 → 今日建议 → 新闻 → 事件日历」组织。
所有数字只能来自 state 文件或脚本输出,禁止自行估算价格/delta/权利金。
