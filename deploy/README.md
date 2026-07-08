# 部署指南

核心代码纯跨平台,只有调度层分平台。三个任务:
watcher(常驻)、daily(交易日 6:15)、weekly(周日 18:00)。
时间按太平洋时间设计(6:15 PT = 9:15 ET,美国夏令时同步切换);
`run_task daily` 内部有交易日门禁,调度器可以无脑触发。

## Windows(当前环境)

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\register_tasks.ps1
```

注意:
- 电源设置里关闭盘中自动休眠(设置 → 系统 → 电源)
- 用虚拟环境时改脚本里的 `$Python` 为完整路径

## macOS(headless Mac 迁移目标)

```bash
# 1. 把三个 plist 里的 /PATH/TO/AutoCovered 替换为实际路径
# 2. 安装
cp deploy/macos/*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.autocovered.*.plist
# 卸载: launchctl unload ~/Library/LaunchAgents/com.autocovered.*.plist
```

注意:headless Mac 需在"节能"里关闭睡眠;IB Gateway 建议配 IBC 自动重登。

## 方式二:Claude Code 定时任务(推荐,在窗口里管理)

晨报/盘中击穿巡检/周报都有现成的"面向 Claude 的指令"放在 `routines/` 目录,
注册方法见 [`routines/README.md`](../routines/README.md)(Desktop → Routines →
New → **Local**)。注意别和方式一对同一任务重复注册,会推送两遍;
watcher 常驻进程仍建议用 OS 级注册。

日常交互(不需要任何调度器):直接在本仓库开 Claude Code 会话问
"看一下持仓状态"/"给我跑一份晨报"/"NVDA 那个 call 现在怎么样",
covered-call skill 会自动加载。
