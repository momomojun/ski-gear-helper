# 注册一个 Windows 计划任务：每天自动抓取一次价格（不需要开着网页）。
# 用法（在 PowerShell 里）：  powershell -ExecutionPolicy Bypass -File scripts\install_daily_task.ps1
# 取消：                       Unregister-ScheduledTask -TaskName "SkiDeals Daily Crawl" -Confirm:$false
param([string]$At = "09:00")

$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    Write-Host "还没有 .venv，请先双击 start.bat 运行一次。" -ForegroundColor Yellow
    exit 1
}
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m skideals crawl" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $At
# StartWhenAvailable：到点时电脑没开，开机后会补跑
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "SkiDeals Daily Crawl" -Action $action -Trigger $trigger -Settings $settings `
    -Description "SkiDeals 双板比价：每天抓取一次各网站价格，并检查关注列表的降价提醒" -Force | Out-Null
Write-Host "已注册：每天 $At 自动抓取价格（日志在 data\crawl.log）。" -ForegroundColor Green
Write-Host '取消：Unregister-ScheduledTask -TaskName "SkiDeals Daily Crawl" -Confirm:$false'
