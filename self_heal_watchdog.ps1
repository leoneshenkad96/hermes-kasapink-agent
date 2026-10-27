$ErrorActionPreference = 'SilentlyContinue'
$taskName = 'Hermes Kasapink Discord Bot'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$logPath = Join-Path $root 'logs\self-heal-watchdog.log'
$markerPath = Join-Path $root 'data\self-heal-last-restart.txt'

# Watchdog window: active from 17:00 through 07:59 WIB only.
$hour = (Get-Date).Hour
if ($hour -ge 8 -and $hour -lt 17) { exit 0 }

New-Item -ItemType Directory -Path (Split-Path -Parent $logPath) -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $markerPath) -Force | Out-Null
$task = Get-ScheduledTask -TaskName $taskName
$info = Get-ScheduledTaskInfo -TaskName $taskName
$network = Test-NetConnection discord.com -Port 443 -InformationLevel Quiet
$lastRestart = [datetime]::MinValue
if (Test-Path $markerPath) { [datetime]::TryParse((Get-Content $markerPath -Raw), [ref]$lastRestart) | Out-Null }
$cooldown = ((Get-Date) - $lastRestart).TotalMinutes -ge 10
$action = 'healthy'
if ($task -and $network -and $task.State -ne 'Running' -and $cooldown) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    (Get-Date).ToString('o') | Set-Content -LiteralPath $markerPath -Encoding UTF8
    $action = 'restart_requested'
} elseif (-not $network) {
    $action = 'network_unavailable_no_restart'
} elseif (-not $cooldown) {
    $action = 'restart_cooldown'
} elseif ($task -and $task.State -eq 'Running') {
    $action = 'task_running_no_restart'
}
$record = [pscustomobject]@{ at=(Get-Date).ToString('o'); task_state=$task.State; task_result=$info.LastTaskResult; discord_443=[bool]$network; action=$action }
Add-Content -LiteralPath $logPath -Value ($record | ConvertTo-Json -Compress)
