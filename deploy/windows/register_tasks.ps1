# AutoCovered Windows 部署:注册三个计划任务(当前用户,无需管理员)
# 运行:powershell -ExecutionPolicy Bypass -File deploy\windows\register_tasks.ps1
# 卸载:'AutoCovered-Watcher','AutoCovered-Daily','AutoCovered-Weekly' |
#        ForEach-Object { Unregister-ScheduledTask -TaskName $_ -Confirm:$false }

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = "python"   # 用虚拟环境时改成完整路径,如 "$RepoRoot\.venv\Scripts\python.exe"

Write-Host "仓库根目录: $RepoRoot"

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)   # 0 = 不限时(watcher 常驻)

# 1) Watcher:登录时启动,常驻(内部自判交易日/盘中)
$a1 = New-ScheduledTaskAction -Execute $Python -Argument "-m src.watcher" -WorkingDirectory $RepoRoot
$t1 = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "AutoCovered-Watcher" -Action $a1 -Trigger $t1 `
    -Settings $settings -Description "Covered call 盘中监控循环" -Force

# 2) 每日晨报:工作日 6:15(本地时间;run_task 内部有交易日门禁,节假日自动跳过)
$limit = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$a2 = New-ScheduledTaskAction -Execute $Python -Argument "-m src.run_task daily" -WorkingDirectory $RepoRoot
$t2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 6:15AM
Register-ScheduledTask -TaskName "AutoCovered-Daily" -Action $a2 -Trigger $t2 `
    -Settings $limit -Description "Covered call 每日晨报(Claude)" -Force

# 3) 周度复盘:周日 18:00
$a3 = New-ScheduledTaskAction -Execute $Python -Argument "-m src.run_task weekly" -WorkingDirectory $RepoRoot
$t3 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 6:00PM
Register-ScheduledTask -TaskName "AutoCovered-Weekly" -Action $a3 -Trigger $t3 `
    -Settings $limit -Description "Covered call 周度复盘(Claude)" -Force

Write-Host "已注册 3 个计划任务。注意:确保电源设置不会在盘中休眠。"
Write-Host "提示:6:15 AM 按太平洋时间设计(=9:15 AM 美东,美国夏令时同步切换);其他时区请自行调整。"
