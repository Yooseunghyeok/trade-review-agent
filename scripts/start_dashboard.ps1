param(
    [int]$Port = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = (Join-Path $ProjectRoot "src")

Push-Location $ProjectRoot
try {
    $arguments = @("-m", "ict_review.ui.server", "--port", "$Port")
    if ($NoBrowser) { $arguments += "--no-browser" }
    & python @arguments
}
finally {
    Pop-Location
}
