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

## 方式二:在 Claude Code 窗口里管理定时任务

不想用 OS 调度器的话,晨报/周报也可以交给 Claude Code 的定时任务
(在 Claude Code 会话里说"帮我建一个每个交易日早上 6:15 跑
`python -m src.run_task daily` 的定时任务"即可,它会用 Cron 工具创建;
watcher 是常驻进程,仍需 OS 级注册)。

日常交互(不需要任何调度器):直接在本仓库开 Claude Code 会话问
"看一下持仓状态"/"给我跑一份晨报"/"NVDA 那个 call 现在怎么样",
covered-call skill 会自动加载。
