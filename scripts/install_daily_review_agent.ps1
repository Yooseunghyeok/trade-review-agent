param(
    [switch]$DryRun,
    [switch]$EnableWindowsLogonTask,
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$HermesHome = (Join-Path $env:LOCALAPPDATA "hermes")
)

$ErrorActionPreference = "Stop"
$skillSource = Join-Path $ProjectRoot "skills\toobit-daily-review"
# Hermes cron runs --script with Python, so the cron entrypoint MUST be .py.
# The .ps1 wrapper is kept for the optional Windows logon task / manual runs.
$scriptSource = Join-Path $ProjectRoot "scripts\hermes\run_daily_review.py"
$scriptSourcePs1 = Join-Path $ProjectRoot "scripts\hermes\run_daily_review.ps1"
$skillTarget = Join-Path $HermesHome "skills\toobit-daily-review"
$scriptTargetDir = Join-Path $HermesHome "scripts"
$taskName = "ICTDailyTradingReview"

Write-Output "ProjectRoot: $ProjectRoot"
Write-Output "HermesHome: $HermesHome"
Write-Output "Will copy skill to: $skillTarget"
Write-Output "Will copy wrapper to: $scriptTargetDir"
Write-Output "Will create or print daily 00:00 Hermes cron command with deliver=local"
if ($EnableWindowsLogonTask) {
    Write-Output "Will create user-level Windows logon scheduled task: $taskName"
}

if ($DryRun) {
    Write-Output "DRY_RUN no files, cron entries, or scheduled tasks changed"
    Write-Output "Hermes cron command: hermes cron create --name toobit-daily-review --deliver local --no-agent --script `"run_daily_review.py`" --workdir `"$ProjectRoot`" `"0 0 * * *`""
    exit 0
}

$confirmation = Read-Host "Type YES to install Daily Trading Review Agent assets"
if ($confirmation -ne "YES") {
    Write-Output "CANCELLED"
    exit 1
}

New-Item -ItemType Directory -Force -Path $skillTarget | Out-Null
Copy-Item -Path (Join-Path $skillSource "*") -Destination $skillTarget -Recurse -Force
New-Item -ItemType Directory -Force -Path $scriptTargetDir | Out-Null
Copy-Item -Path $scriptSource -Destination (Join-Path $scriptTargetDir "run_daily_review.py") -Force
Copy-Item -Path $scriptSourcePs1 -Destination (Join-Path $scriptTargetDir "run_daily_review.ps1") -Force

$hermes = Get-Command hermes -ErrorAction SilentlyContinue
if ($hermes) {
    Write-Output "Hermes: OK"
    Write-Output "Run if not already registered:"
    Write-Output "hermes cron create --name toobit-daily-review --deliver local --no-agent --script `"run_daily_review.py`" --workdir `"$ProjectRoot`" `"0 0 * * *`""
} else {
    Write-Output "Hermes: MISSING"
}

if ($EnableWindowsLogonTask) {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File `"$scriptTargetDir\run_daily_review.ps1`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $existing) {
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Description "Run ICT daily review bootstrap at user logon." | Out-Null
    }
}

Write-Output "Verify with:"
Write-Output "powershell -ExecutionPolicy Bypass -File `"$ProjectRoot\scripts\verify_daily_agent_environment.ps1`""
Write-Output "hermes cron list"
Write-Output "hermes gateway status"
