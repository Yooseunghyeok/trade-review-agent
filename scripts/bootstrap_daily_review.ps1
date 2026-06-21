param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd"),
    [switch]$DryRun,
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"
$lockDir = Join-Path $ProjectRoot "data\locks"
$logDir = Join-Path $ProjectRoot "data\runs\bootstrap-$Date"
$lockPath = Join-Path $lockDir "daily-review.lock"
$srcPath = Join-Path $ProjectRoot "src"

if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$srcPath;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $srcPath
}

function Log-Line {
    param([string]$Message)
    $safe = $Message -replace '(?i)(key|secret|token|password)=\S+', '$1=***'
    Write-Output $safe
    if (-not $DryRun) {
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        Add-Content -LiteralPath (Join-Path $logDir "bootstrap.log") -Value $safe -Encoding UTF8
    }
}

function Test-LiteLLMReady {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:4000/health/readiness" -Method Get -TimeoutSec 3 -UseBasicParsing
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    } catch {
        return $false
    }
}

function Quote-ProcessArgument {
    param([string]$Argument)
    if ($null -eq $Argument) {
        return '""'
    }
    if ($Argument -notmatch '[\s"]') {
        return $Argument
    }
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($char in $Argument.ToCharArray()) {
        if ($char -eq '\') {
            $backslashes += 1
            continue
        }
        if ($char -eq '"') {
            [void]$builder.Append(('\' * (($backslashes * 2) + 1)))
            [void]$builder.Append('"')
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append(('\' * $backslashes))
            $backslashes = 0
        }
        [void]$builder.Append($char)
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append(('\' * ($backslashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Invoke-PythonChecked {
    param([string[]]$Arguments)
    $result = Invoke-PythonCapture $Arguments
    if ($result.Code -ne 0) {
        $output = (($result.Stdout, $result.Stderr) -join "`n").Trim()
        if (-not $output) {
            Log-Line ("PYTHON_FAILED python exited with code {0}" -f $result.Code)
        } else {
            foreach ($line in ($output -split "`r?`n")) {
                if (-not $line) { continue }
                Log-Line ("PYTHON_FAILED {0}" -f $line)
            }
        }
        exit $result.Code
    }
    return $result.Stdout
}

function Invoke-PythonCapture {
    param([string[]]$Arguments)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "python"
    $quotedArgs = @()
    foreach ($arg in $Arguments) {
        $quotedArgs += (Quote-ProcessArgument $arg)
    }
    $psi.Arguments = ($quotedArgs -join " ")
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $psi.UseShellExecute = $false
    $process = [System.Diagnostics.Process]::Start($psi)
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    $code = $process.ExitCode
    return [pscustomobject]@{ Code = $code; Stdout = $stdout; Stderr = $stderr }
}

function Invoke-HermesChecked {
    param([string]$HermesPath, [string[]]$Arguments, [string]$StdoutPath, [string]$StderrPath)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $HermesPath
    $quotedArgs = @()
    foreach ($arg in $Arguments) {
        $quotedArgs += (Quote-ProcessArgument $arg)
    }
    $psi.Arguments = ($quotedArgs -join " ")
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $process = [System.Diagnostics.Process]::Start($psi)
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($StdoutPath, $stdout, $utf8NoBom)
    [System.IO.File]::WriteAllText($StderrPath, $stderr, $utf8NoBom)
    return $process.ExitCode
}

Push-Location $ProjectRoot
try {
    if (-not (Test-Path (Join-Path $srcPath "ict_review"))) {
        Log-Line ("PYTHON_FAILED missing package path {0}" -f (Join-Path $srcPath "ict_review"))
        exit 5
    }
    Invoke-PythonChecked @("-c", "import ict_review; import ict_review.cli.daily_review") | Out-Null

    if (Test-Path $lockPath) {
        Log-Line "LOCK_HELD"
        exit 2
    }
    if (-not $DryRun) {
        New-Item -ItemType Directory -Force -Path $lockDir | Out-Null
        New-Item -ItemType File -Path $lockPath -ErrorAction Stop | Out-Null
    }

    $dataRoot = Join-Path $ProjectRoot "data"
    $statusJson = Invoke-PythonChecked @("-m", "ict_review.cli.daily_review", "--data-root", $dataRoot, "status", "--date", $Date)
    $dailyStatus = $statusJson | ConvertFrom-Json
    if ($dailyStatus.latest_status -eq "PUBLISHED") {
        Log-Line "ALREADY_PUBLISHED"
        exit 0
    }

    if ($DryRun) {
        Log-Line "DRY_RUN LiteLLM readiness would be checked at 127.0.0.1:4000"
        Log-Line "DRY_RUN daily_review prepare would run for $Date"
        Log-Line "DRY_RUN Hermes one-shot skill toobit-daily-review would run"
        exit 0
    }

    if (-not (Test-LiteLLMReady)) {
        $litellm = Get-Command litellm -ErrorAction SilentlyContinue
        if (-not $litellm) {
            Log-Line "FAILED_PROXY_START litellm missing"
            Invoke-PythonChecked @("-m", "ict_review.cli.daily_review", "--data-root", (Join-Path $ProjectRoot "data"), "status", "--date", $Date) | Out-Null
            exit 3
        }
        $config = Join-Path $ProjectRoot "litellm_config.yaml"
        Start-Process -FilePath $litellm.Source -ArgumentList @("--config", $config, "--port", "4000") -WindowStyle Hidden | Out-Null
        $ready = $false
        foreach ($i in 1..20) {
            Start-Sleep -Seconds 3
            if (Test-LiteLLMReady) { $ready = $true; break }
        }
        if (-not $ready) {
            Log-Line "FAILED_PROXY_START readiness timeout"
            exit 3
        }
    }

    $retryableStatuses = @("WAITING_FOR_LLM", "MODEL_EMPTY_RESPONSE", "MODEL_RATE_LIMIT", "INVALID_LLM_OUTPUT")
    $runDir = $null
    if ($retryableStatuses -contains $dailyStatus.latest_status -and $dailyStatus.latest_run_id) {
        $candidateRunDir = Join-Path $dataRoot (Join-Path "runs" $dailyStatus.latest_run_id)
        if ((Test-Path (Join-Path $candidateRunDir "review_request.json")) -and (Test-Path (Join-Path $candidateRunDir "manifest.json"))) {
            $runDir = $candidateRunDir
            Invoke-PythonChecked @("-m", "ict_review.cli.daily_review", "--data-root", $dataRoot, "mark-status", "--run-id", $dailyStatus.latest_run_id, "--status", "WAITING_FOR_LLM", "--reason", "Retrying the existing Hermes stage.") | Out-Null
            Log-Line "RESUMED $runDir"
        }
    }
    if (-not $runDir) {
        $runDir = (Invoke-PythonChecked @("-m", "ict_review.cli.daily_review", "--data-root", $dataRoot, "prepare", "--date", $Date) | Out-String).Trim()
        Log-Line "PREPARED $runDir"
    }
    $hermes = Get-Command hermes -ErrorAction SilentlyContinue
    if (-not $hermes) {
        Log-Line "FAILED hermes missing"
        exit 4
    }
    $reviewRequestPath = Join-Path $runDir "review_request.json"
    $hermesRawPath = Join-Path $runDir "hermes.raw.txt"
    $reviewDraftPath = Join-Path $runDir "review_draft.json"
    $hermesErrorPath = Join-Path $runDir "hermes.stderr.log"
    $reviewRequest = Get-Content -LiteralPath $reviewRequestPath -Raw -Encoding UTF8
    $reviewRequestObject = $reviewRequest | ConvertFrom-Json
    $firstEvidence = @($reviewRequestObject.evidence_ids)[0]
    $outputTemplate = [ordered]@{
        run_id = $reviewRequestObject.run_id
        episode_ids = @($reviewRequestObject.episode_ids)
        metrics = @($reviewRequestObject.required_metrics)
        observations = @(
            [ordered]@{
                text = "REPLACE this text with one factual trading observation supported by the attached evidence_ids."
                evidence_ids = @($firstEvidence)
            }
        )
        questions = @("REPLACE this text with one useful question for the trader.")
        pattern_candidates = @()
        evidence_ids = @($reviewRequestObject.evidence_ids)
        model_metadata = [ordered]@{
            model_name = "REPLACE with the active model name"
            provider = "Vertex AI via LiteLLM and Hermes"
            timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        }
        schema_version = "2"
    }
    $outputTemplateJson = $outputTemplate | ConvertTo-Json -Depth 10
    $prompt = @"
Use the toobit-daily-review skill.
The verified review request JSON is included below. Do not call tools and do not read any other file.

$reviewRequest

OUTPUT CONTRACT (highest priority):
- Return exactly one JSON object and no markdown code fence or explanation.
- Use EXACTLY these 9 top-level keys and no others: run_id, episode_ids, metrics, observations, questions, pattern_candidates, evidence_ids, model_metadata, schema_version.
- Copy the template below. Keep run_id, episode_ids, metrics, evidence_ids and schema_version unchanged.
- Replace only the two REPLACE texts and model_metadata.model_name.
- observations must remain an array of objects with text and evidence_ids. questions must remain an array of strings.
- If observations or questions mention a number, copy its full exact string from metrics. Never round or shorten it.
- Write observation text and questions in Korean.

$outputTemplateJson
"@
    $models = @("vertex-gemini-flash", "vertex-gemini-pro", "vertex-gemini-flash-lite")
    $maxAttempts = $models.Count
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        $model = $models[$attempt - 1]
        $attemptRawPath = Join-Path $runDir ("hermes.raw.attempt{0}.txt" -f $attempt)
        $attemptErrorPath = Join-Path $runDir ("hermes.stderr.attempt{0}.log" -f $attempt)
        Log-Line ("HERMES_ATTEMPT {0}/{1} model={2}" -f $attempt, $maxAttempts, $model)
        $code = Invoke-HermesChecked $hermes.Source @("--skills", "toobit-daily-review", "--model", $model, "-z", $prompt) $attemptRawPath $attemptErrorPath
        Copy-Item -LiteralPath $attemptRawPath -Destination $hermesRawPath -Force
        Copy-Item -LiteralPath $attemptErrorPath -Destination $hermesErrorPath -Force
        if ($code -ne 0) {
            Log-Line ("HERMES_FAILED_EXIT model={0} code={1}" -f $model, $code)
            if ($attempt -lt $maxAttempts) {
                continue
            }
            Invoke-PythonChecked @("-m", "ict_review.cli.daily_review", "--data-root", $dataRoot, "mark-status", "--run-id", (Split-Path $runDir -Leaf), "--status", "FAILED", "--reason", "All configured Hermes models exited with an error.") | Out-Null
            exit $code
        }

        $normalize = Invoke-PythonCapture @("-m", "ict_review.cli.daily_review", "--data-root", (Join-Path $ProjectRoot "data"), "normalize-llm-output", "--run-id", (Split-Path $runDir -Leaf), "--raw", $hermesRawPath, "--output", $reviewDraftPath)
        if ($normalize.Code -eq 0) {
            Log-Line ("HERMES_EXIT 0 model={0}" -f $model)
            Invoke-PythonChecked @("-m", "ict_review.cli.daily_review", "--data-root", $dataRoot, "finalize", "--run-id", (Split-Path $runDir -Leaf), "--review-json", $reviewDraftPath) | Out-Null
            Log-Line "FINALIZED $reviewDraftPath"
            exit 0
        }

        $normalizeOutput = (($normalize.Stdout, $normalize.Stderr) -join "`n").Trim()
        if ($normalizeOutput -match "MODEL_RATE_LIMIT") {
            Log-Line ("MODEL_RATE_LIMIT attempt {0} of {1}" -f $attempt, $maxAttempts)
            Invoke-PythonChecked @("-m", "ict_review.cli.daily_review", "--data-root", $dataRoot, "mark-status", "--run-id", (Split-Path $runDir -Leaf), "--status", "MODEL_RATE_LIMIT", "--reason", ("Hermes model {0} hit a rate limit or resource exhaustion." -f $model)) | Out-Null
            if (Test-Path $reviewDraftPath) {
                Remove-Item -LiteralPath $reviewDraftPath -Force -ErrorAction SilentlyContinue
            }
            if ($attempt -lt $maxAttempts) {
                continue
            }
            Log-Line "MODEL_RATE_LIMIT all configured models failed"
            exit 29
        }

        if ($normalizeOutput -match "MODEL_EMPTY_RESPONSE") {
            Log-Line ("MODEL_EMPTY_RESPONSE model={0}" -f $model)
            Invoke-PythonChecked @("-m", "ict_review.cli.daily_review", "--data-root", $dataRoot, "mark-status", "--run-id", (Split-Path $runDir -Leaf), "--status", "MODEL_EMPTY_RESPONSE", "--reason", ("Hermes model {0} returned no review content." -f $model)) | Out-Null
            if ($attempt -lt $maxAttempts) { continue }
            exit 30
        }

        foreach ($line in ($normalizeOutput -split "`r?`n")) {
            if (-not $line) { continue }
            Log-Line ("PYTHON_FAILED {0}" -f $line)
        }
        Invoke-PythonChecked @("-m", "ict_review.cli.daily_review", "--data-root", $dataRoot, "mark-status", "--run-id", (Split-Path $runDir -Leaf), "--status", "INVALID_LLM_OUTPUT", "--reason", ("Hermes model {0} returned invalid review JSON." -f $model)) | Out-Null
        if ($attempt -lt $maxAttempts) { continue }
        exit $normalize.Code
    }
}
finally {
    if (-not $DryRun -and (Test-Path $lockPath)) {
        Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    }
    Pop-Location
}
