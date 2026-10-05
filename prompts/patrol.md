以**定时(headless)模式**执行盘中击穿/差价巡检:阅读
`.claude/skills/scheduled-tasks/SKILL.md`,严格按其「通用第 0 步」与
「二、盘中击穿/差价巡检」执行,并遵守 covered-call skill 的全部约束。

要点(以 skill 原文为准,这里不复制步骤):
- 先 `python -m src.watcher --once --no-trigger` 刷新事实;
- 无风险仓 → 不推送,直接结束;有风险仓 → 存档到 `state/analysis/` 并用
  `notify.py` 推手机;今日已推过的同一仓同一状态不重复推;
- 所有数字只能来自 state 文件或脚本输出,禁止自行估算。

最后输出一行结论(如"全部 ON_TRACK"或"推送了 N 条:…"),供调度日志记录。
