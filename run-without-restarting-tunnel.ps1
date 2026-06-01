param(
    [int]$Port = 8000,
    [switch]$NoRestart
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvPath = Join-Path $ProjectRoot ".env"
$UvicornLog = Join-Path $ProjectRoot "uvicorn.log"
$LocalBaseUrl = "http://127.0.0.1:$Port"

function Get-PortListenerPids {
    param([int]$Port)

    $pattern = "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
    netstat -ano | ForEach-Object {
        if ($_ -match $pattern) {
            [int]$Matches[1]
        }
    } | Sort-Object -Unique
}

function Stop-Uvicorn {
    Get-Process -Name "uvicorn" -ErrorAction SilentlyContinue | Stop-Process -Force

    foreach ($listenerPid in Get-PortListenerPids -Port $Port) {
        Write-Host "Stopping process $listenerPid listening on port $Port..."
        Stop-Process -Id $listenerPid -Force -ErrorAction SilentlyContinue
    }
}

function Wait-ForPortToClose {
    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Get-PortListenerPids -Port $Port)) {
            return
        }
        Start-Sleep -Milliseconds 500
    }

    throw "Port $Port is still in use after stopping the old server."
}

function Wait-ForHealth {
    param([System.Diagnostics.Process]$StartedProcess)

    $healthUrl = "$LocalBaseUrl/health"
    for ($i = 0; $i -lt 30; $i++) {
        if (Test-Path $UvicornLog) {
            $logTail = Get-Content -Path $UvicornLog -Tail 40 -ErrorAction SilentlyContinue
            if ($logTail -match "error while attempting to bind|address already in use|only one usage of each socket address") {
                throw "New FastAPI process failed to bind port $Port. Check $UvicornLog."
            }
        }

        if ($StartedProcess -and $StartedProcess.HasExited) {
            throw "New FastAPI launcher exited before becoming healthy. Check $UvicornLog."
        }

        try {
            Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2 | Out-Null
            return
        } catch {
            Start-Sleep -Seconds 1
        }
    }

    throw "FastAPI did not become healthy at $healthUrl"
}

function Get-EnvValue {
    param([string]$Name)

    if (-not (Test-Path $EnvPath)) {
        return ""
    }

    $line = Get-Content -Path $EnvPath | Where-Object { $_ -match "^$Name=" } | Select-Object -First 1
    if (-not $line) {
        return ""
    }

    return ($line -replace "^$Name=", "")
}

function Open-Report {
    param([string]$ReportUrl)

    Write-Host ""
    Write-Host "Report:"
    Write-Host $ReportUrl

    try {
        Start-Process $ReportUrl
        Write-Host "Opened report in browser."
    } catch {
        Write-Host "Could not open report automatically. Open the URL above."
    }
}

if (-not $NoRestart) {
    Write-Host "Stopping old uvicorn server..."
    Stop-Uvicorn
    Wait-ForPortToClose
}

if (Test-Path $UvicornLog) {
    try {
        Clear-Content -Path $UvicornLog
    } catch {
        Write-Host "Could not clear uvicorn.log; continuing with existing log file."
    }
}

Write-Host "Starting FastAPI on $LocalBaseUrl without restarting cloudflared..."
$UvicornProcess = Start-Process -WindowStyle Hidden -PassThru -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile",
    "-Command",
    "Set-Location '$ProjectRoot'; uvicorn app:app --host 127.0.0.1 --port $Port *> '$UvicornLog'"
)

Wait-ForHealth -StartedProcess $UvicornProcess
Write-Host "FastAPI is healthy."

$WebhookUrl = Get-EnvValue -Name "PUBLIC_WEBHOOK_URL"
$PublicBaseUrl = ""
if ($WebhookUrl -match "^(https://[^/]+)/github/webhook$") {
    $PublicBaseUrl = $Matches[1]
}

Write-Host ""
Write-Host "Local check URLs:"
Write-Host "$LocalBaseUrl/health"
Write-Host "$LocalBaseUrl/github/webhook"
Write-Host "$LocalBaseUrl/report-html"
Write-Host "$LocalBaseUrl/docs"

if ($PublicBaseUrl) {
    Write-Host ""
    Write-Host "Current Cloudflare check URLs from .env:"
    Write-Host "$PublicBaseUrl/health"
    Write-Host "$PublicBaseUrl/github/webhook"
    Write-Host "$PublicBaseUrl/report-html"
    Write-Host "$PublicBaseUrl/docs"
    Write-Host ""
    Write-Host "GitHub webhook Payload URL:"
    Write-Host $WebhookUrl

    if (-not $NoRestart) {
        Write-Host ""
        Write-Host "⚠️ REMINDER: Ensure this URL matches what is configured in your GitHub Repository Webhook settings."
        $done = Read-Host "Is this webhook configured correctly in GitHub? [Y/n]"
        if ($done -match "^[Nn]") {
             Write-Host "Please update GitHub with: $WebhookUrl"
             Read-Host "Press Enter when you are done"
        }
    }
} else {
    Write-Host ""
    Write-Host "PUBLIC_WEBHOOK_URL is not set in .env. Existing cloudflared tunnel was not changed."
}

$ReportBaseUrl = $LocalBaseUrl
if ($PublicBaseUrl) {
    $ReportBaseUrl = $PublicBaseUrl
}
Open-Report -ReportUrl "$ReportBaseUrl/report-html"

Write-Host ""
Write-Host "====================================================="
Write-Host "Uvicorn is UP and running."
Write-Host "Report URL: $ReportBaseUrl/report-html"
Write-Host "Press Ctrl+C to stop the app and exit."
Write-Host "====================================================="

try {
    while ($true) {
        Start-Sleep -Seconds 1
    }
} finally {
    Write-Host "Stopping uvicorn..."
    Stop-Uvicorn
}
