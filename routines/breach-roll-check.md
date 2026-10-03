# 盘中巡检:击穿防线 / Roll 判定(Claude Code 定时任务指令)

**调度建议:交易日盘中每 2 小时(如本地 07:00 / 09:00 / 11:00 / 13:00)。**

以**定时(headless)模式**执行 scheduled-tasks skill
(`.claude/skills/scheduled-tasks/SKILL.md`)的「盘中击穿/差价巡检」流程:

刷新数据 → 全仓差价快照存档 → 筛 BREACHED/ROLL_WINDOW/TESTED/OPTION_LOSS
风险仓 → **无风险则静默结束(不推送)**,有风险才逐仓给
roll up & forward / 买回 / 被叫走三选项分析并推手机。

巡检流程本身只在 skill 里维护,本文件只是调度入口,不要在这里复制步骤。
