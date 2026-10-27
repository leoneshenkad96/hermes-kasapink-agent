$ErrorActionPreference = "Continue"
$agentDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $agentDir "logs"
$logPath = Join-Path $logDir "discord-bot.log"
$python = Join-Path $agentDir ".venv314\Scripts\python.exe"
$script = Join-Path $agentDir "discord_bot.py"
$env:PYTHONPATH = $agentDir
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
if (-not (Test-Path -LiteralPath $python)) {
    Add-Content -LiteralPath $logPath -Value "[$(Get-Date -Format s)] Hermes runtime missing: $python"
    exit 1
}
Set-Location $agentDir
# Task Scheduler can leave an older Discord child process behind when the
# wrapper is stopped. Clean up only Python processes launched from this Hermes
# project before starting the single production instance.
$agentPattern = [regex]::Escape($agentDir)
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine -match $agentPattern -and $_.CommandLine -match 'discord_bot\.py' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
while ($true) {
    if (Test-Path -LiteralPath $logPath) {
        $logItem = Get-Item -LiteralPath $logPath
        if ($logItem.Length -ge 5MB) {
            foreach ($index in 3..1) {
                $source = if ($index -eq 1) { $logPath } else { "$logPath.$($index - 1)" }
                $destination = "$logPath.$index"
                if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Force }
                if (Test-Path -LiteralPath $source) { Move-Item -LiteralPath $source -Destination $destination }
            }
        }
    }
    & $python -u $script 2>&1 | ForEach-Object {
        Add-Content -LiteralPath $logPath -Value ([string]$_) -Encoding UTF8
    }
    $exitCode = $LASTEXITCODE
    Add-Content -LiteralPath $logPath -Value "[$(Get-Date -Format s)] Hermes exited with code $exitCode; retrying in 30 seconds."
    Start-Sleep -Seconds 30
}
