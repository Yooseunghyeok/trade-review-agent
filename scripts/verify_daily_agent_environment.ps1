param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"

function Status-Line {
    param([string]$Name, [string]$Status, [string]$Detail = "")
    if ($Detail) {
        Write-Output ("{0}: {1} ({2})" -f $Name, $Status, $Detail)
    } else {
        Write-Output ("{0}: {1}" -f $Name, $Status)
    }
}

function Command-Status {
    param([string]$Name, [string]$Command, [string[]]$VersionArgs)
    $cmd = Get-Command $Command -ErrorAction SilentlyContinue
    if (-not $cmd) {
        Status-Line $Name "MISSING"
        return
    }
    $version = "available"
    try {
        $version = (& $cmd.Source @VersionArgs 2>$null | Select-Object -First 1)
        if (-not $version) { $version = "available" }
    } catch {
        $version = "available"
    }
    Status-Line $Name "OK" ("{0}; {1}" -f $cmd.Source, $version)
}

function Http-Status {
    param([string]$Name, [string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec 3 -UseBasicParsing
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
            Status-Line $Name "OK"
        } else {
            Status-Line $Name "NOT_RUNNING"
        }
    } catch {
        Status-Line $Name "NOT_RUNNING"
    }
}

function Hermes-Gateway-Status {
    $cmd = Get-Command hermes -ErrorAction SilentlyContinue
    if (-not $cmd) {
        Status-Line "HermesGateway" "MISSING"
        return
    }
    try {
        $output = & $cmd.Source gateway status 2>&1 | Out-String
        if ($output -match "Gateway process running") {
            Status-Line "HermesGateway" "OK"
            return
        }
    } catch {
    }
    Http-Status "HermesGateway" "http://127.0.0.1:8080/health"
}

function Writable-Status {
    param([string]$PathValue)
    try {
        New-Item -ItemType Directory -Force -Path $PathValue | Out-Null
        $probe = Join-Path $PathValue (".write-test-{0}.tmp" -f [guid]::NewGuid().ToString("N"))
        Set-Content -LiteralPath $probe -Value "ok" -Encoding UTF8
        Remove-Item -LiteralPath $probe -Force
        Status-Line ("Writable {0}" -f $PathValue) "OK"
    } catch {
        Status-Line ("Writable {0}" -f $PathValue) "MISSING"
    }
}

Status-Line "ProjectRoot" "OK" ((Resolve-Path $ProjectRoot).Path)
Command-Status "Python" "python" @("--version")
Command-Status "Hermes" "hermes" @("--version")
Command-Status "LiteLLM" "litellm" @("--version")
Command-Status "gcloud" "gcloud" @("--version")
Hermes-Gateway-Status
Http-Status "LiteLLMReadiness" "http://127.0.0.1:4000/health/readiness"
Status-Line "litellm_config.yaml" ($(if (Test-Path (Join-Path $ProjectRoot "litellm_config.yaml")) { "OK" } else { "MISSING" }))
Status-Line ".env" ($(if (Test-Path (Join-Path $ProjectRoot ".env")) { "OK" } else { "MISSING" }))
Writable-Status (Join-Path $ProjectRoot "data\runs")
Writable-Status (Join-Path $ProjectRoot "outputs")
Writable-Status (Join-Path $ProjectRoot "logs")
