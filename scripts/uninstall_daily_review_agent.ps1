param(
    [switch]$DryRun,
    [string]$HermesHome = (Join-Path $env:LOCALAPPDATA "hermes")
)

$ErrorActionPreference = "Stop"
$skillTarget = Join-Path $HermesHome "skills\toobit-daily-review"
$scriptTarget = Join-Path $HermesHome "scripts\run_daily_review.ps1"
$taskName = "ICTDailyTradingReview"

Write-Output "Will remove project-created Hermes skill: $skillTarget"
Write-Output "Will remove project-created Hermes wrapper: $scriptTarget"
Write-Output "Will remove project-created Windows task if present: $taskName"
Write-Output "Will not delete user data, run outputs, reviews, .env, or LiteLLM config"

if ($DryRun) {
    Write-Output "DRY_RUN no files, cron entries, or scheduled tasks changed"
    Write-Output "If Hermes cron was registered, remove it with: hermes cron remove --name toobit-daily-review"
    exit 0
}

if (Test-Path $skillTarget) { Remove-Item -LiteralPath $skillTarget -Recurse -Force }
if (Test-Path $scriptTarget) { Remove-Item -LiteralPath $scriptTarget -Force }
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
Write-Output "Removed project-created local assets. Review Hermes cron list for toobit-daily-review."
