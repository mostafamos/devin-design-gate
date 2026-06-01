param(
    [switch]$UpdateEnv,
    [switch]$NoPrompt,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvPath = Join-Path $ProjectRoot ".env"
$UvicornLog = Join-Path $ProjectRoot "uvicorn.log"
$CloudflaredLog = Join-Path $ProjectRoot "cloudflared.log"
$CloudflaredPath = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$LocalBaseUrl = "http://127.0.0.1:$Port"
$LocalTunnelUrl = "http://localhost:$Port"

function Stop-ByName {
    param([string]$Name)

    Get-Process -Name $Name -ErrorAction SilentlyContinue | Stop-Process -Force
}

function Wait-ForHealth {
    $healthUrl = "$LocalBaseUrl/health"
    for ($i = 0; $i -lt 30; $i++) {
        try {
            Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2 | Out-Null
            return
        } catch {
            Start-Sleep -Seconds 1
        }
    }

    throw "FastAPI did not become healthy at $healthUrl"
}

function Wait-ForTunnelUrl {
    for ($i = 0; $i -lt 45; $i++) {
        if (Test-Path $CloudflaredLog) {
            $match = Select-String -Path $CloudflaredLog -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -Last 1
            if ($match -and $match.Matches.Count -gt 0) {
                return $match.Matches[0].Value
            }
        }

        Start-Sleep -Seconds 1
    }

    throw "cloudflared did not print a trycloudflare.com URL"
}

function Set-PublicWebhookUrl {
    param([string]$WebhookUrl)

    if (-not (Test-Path $EnvPath)) {
        New-Item -Path $EnvPath -ItemType File | Out-Null
    }

    $lines = Get-Content -Path $EnvPath -ErrorAction SilentlyContinue
    $updated = $false
    $newLines = foreach ($line in $lines) {
        if ($line -match "^PUBLIC_WEBHOOK_URL=") {
            $updated = $true
            "PUBLIC_WEBHOOK_URL=$WebhookUrl"
        } else {
            $line
        }
    }

    if (-not $updated) {
        $newLines += "PUBLIC_WEBHOOK_URL=$WebhookUrl"
    }

    Set-Content -Path $EnvPath -Value $newLines
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

if (-not (Test-Path $CloudflaredPath)) {
    throw "cloudflared.exe was not found at $CloudflaredPath"
}

Write-Host "Stopping old cloudflared tunnel..."
Stop-ByName -Name "cloudflared"

Write-Host "Stopping old uvicorn server..."
Stop-ByName -Name "uvicorn"
Start-Sleep -Seconds 1

if (Test-Path $UvicornLog) {
    Clear-Content -Path $UvicornLog
}
if (Test-Path $CloudflaredLog) {
    Clear-Content -Path $CloudflaredLog
}

Write-Host "Starting cloudflared tunnel..."
Start-Process -WindowStyle Hidden -FilePath $CloudflaredPath -ArgumentList @(
    "tunnel",
    "--url",
    $LocalTunnelUrl,
    "--logfile",
    $CloudflaredLog
)

$PublicBaseUrl = Wait-ForTunnelUrl
$WebhookUrl = "$PublicBaseUrl/github/webhook"

Write-Host ""
Write-Host "New Cloudflare URL:"
Write-Host $PublicBaseUrl
Write-Host ""
Write-Host "GitHub webhook Payload URL:"
Write-Host $WebhookUrl

$shouldUpdateEnv = $UpdateEnv
if (-not $UpdateEnv -and -not $NoPrompt) {
    $answer = Read-Host "Update PUBLIC_WEBHOOK_URL in .env? [Y/n]"
    $shouldUpdateEnv = ($answer -eq "" -or $answer -match "^[Yy]")
}

if ($shouldUpdateEnv) {
    Set-PublicWebhookUrl -WebhookUrl $WebhookUrl
    Write-Host "Updated $EnvPath"
} else {
    Write-Host "Skipped .env update."
}

Write-Host ""
Write-Host "Paste this into GitHub webhook settings:"
Write-Host $WebhookUrl

if (-not $NoPrompt) {
    Write-Host ""
    Read-Host "After you paste and save the GitHub webhook URL, press Enter to start the app"
}

Write-Host "Starting FastAPI on $LocalBaseUrl..."
Start-Process -WindowStyle Hidden -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile",
    "-Command",
    "Set-Location '$ProjectRoot'; uvicorn app:app --host 127.0.0.1 --port $Port *> '$UvicornLog'"
)

Wait-ForHealth
Write-Host "FastAPI is healthy."

Write-Host ""
Write-Host "Check URLs:"
Write-Host "$PublicBaseUrl/health"
Write-Host "$PublicBaseUrl/github/webhook"
Write-Host "$PublicBaseUrl/report-html"
Write-Host "$PublicBaseUrl/docs"

Open-Report -ReportUrl "$PublicBaseUrl/report-html#latest-run"
