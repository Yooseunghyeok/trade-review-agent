param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd"),
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $env:ICT_TRADING_WIKI_ROOT
if (-not $ProjectRoot) {
    $candidate = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    if (Test-Path $candidate) {
        $ProjectRoot = $candidate
    } else {
        throw "ICT_TRADING_WIKI_ROOT is not set and default project path was not found."
    }
}

$bootstrap = Join-Path $ProjectRoot "scripts\bootstrap_daily_review.ps1"
if (-not (Test-Path $bootstrap)) {
    throw "Missing bootstrap script: $bootstrap"
}

$srcPath = Join-Path $ProjectRoot "src"
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$srcPath;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $srcPath
}

if ($DryRun) {
    & powershell -ExecutionPolicy Bypass -File $bootstrap -Date $Date -DryRun -ProjectRoot $ProjectRoot
} else {
    & powershell -ExecutionPolicy Bypass -File $bootstrap -Date $Date -ProjectRoot $ProjectRoot
}
