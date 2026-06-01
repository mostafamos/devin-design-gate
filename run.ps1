param(
    [int]$Port = 8000,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvPath = Join-Path $ProjectRoot ".env"
$UvicornLog = Join-Path $ProjectRoot "uvicorn.log"
$CloudflaredLog = Join-Path $ProjectRoot "cloudflared.log"
$RunLog = Join-Path $ProjectRoot "run.log"
$CloudflaredPath = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$LocalBaseUrl = "http://127.0.0.1:$Port"
$LocalTunnelUrl = "http://localhost:$Port"

function Log-Step {
    param(
        [string]$Status,
        [string]$Message
    )

    $line = "$(Get-Date -Format o) [$Status] $Message"
    Write-Host $line
    Add-Content -Path $RunLog -Value $line
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

function Set-EnvValue {
    param(
        [string]$Name,
        [string]$Value
    )

    if (-not (Test-Path $EnvPath)) {
        New-Item -Path $EnvPath -ItemType File | Out-Null
    }

    $lines = Get-Content -Path $EnvPath -ErrorAction SilentlyContinue
    $updated = $false
    $newLines = foreach ($line in $lines) {
        if ($line -match "^$Name=") {
            $updated = $true
            "$Name=$Value"
        } else {
            $line
        }
    }

    if (-not $updated) {
        $newLines += "$Name=$Value"
    }

    Set-Content -Path $EnvPath -Value $newLines
}

function Get-PortListenerPids {
    param([int]$Port)

    $pattern = "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
    netstat -ano | ForEach-Object {
        if ($_ -match $pattern) {
            [int]$Matches[1]
        }
    } | Sort-Object -Unique
}

function Stop-ByName {
    param([string]$Name)

    Get-Process -Name $Name -ErrorAction SilentlyContinue | Stop-Process -Force
}

function Stop-Uvicorn {
    Stop-ByName -Name "uvicorn"

    foreach ($listenerPid in Get-PortListenerPids -Port $Port) {
        Log-Step "INFO" "Stopping process $listenerPid listening on port $Port."
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
                throw "FastAPI failed to bind port $Port. Check $UvicornLog."
            }
        }

        if ($StartedProcess -and $StartedProcess.HasExited) {
            throw "FastAPI launcher exited before becoming healthy. Check $UvicornLog."
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

function Start-FastApi {
    Log-Step "INFO" "Restarting FastAPI on $LocalBaseUrl."
    Stop-Uvicorn
    Wait-ForPortToClose

    if (Test-Path $UvicornLog) {
        Clear-Content -Path $UvicornLog
    }

    $process = Start-Process -WindowStyle Hidden -PassThru -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile",
        "-Command",
        "Set-Location '$ProjectRoot'; uvicorn app:app --host 127.0.0.1 --port $Port *> '$UvicornLog'"
    )

    Wait-ForHealth -StartedProcess $process
    Log-Step "SUCCESS" "FastAPI is healthy."
}

function Test-Tunnel {
    param([string]$BaseUrl)

    if (-not $BaseUrl) {
        return $false
    }

    try {
        $response = Invoke-WebRequest -Uri "$BaseUrl/health" -TimeoutSec 10 -UseBasicParsing
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
            Log-Step "SUCCESS" "Existing tunnel is healthy: $BaseUrl"
            return $true
        }

        Log-Step "WARN" "Existing tunnel returned HTTP $($response.StatusCode): $BaseUrl"
        return $false
    } catch {
        $statusCode = 0
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }

        if ($statusCode -in 502, 503, 530, 404) {
            Log-Step "WARN" "Existing tunnel is stale, HTTP ${statusCode}: $BaseUrl"
        } else {
            Log-Step "WARN" "Existing tunnel check failed: $($_.Exception.Message)"
        }
        return $false
    }
}

function Wait-ForPublicReport {
    param([string]$ReportUrl)

    Log-Step "INFO" "Waiting for Cloudflare to serve the report. This can lag for a few seconds after tunnel creation."
    for ($i = 1; $i -le 45; $i++) {
        try {
            $response = Invoke-WebRequest -Uri $ReportUrl -TimeoutSec 5 -UseBasicParsing
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                Log-Step "SUCCESS" "Public report is live after attempt ${i}: $ReportUrl"
                return $true
            }

            Log-Step "INFO" "Report not live yet, HTTP $($response.StatusCode). Retry $i/45."
        } catch {
            $statusCode = 0
            if ($_.Exception.Response) {
                $statusCode = [int]$_.Exception.Response.StatusCode
            }

            if ($statusCode) {
                Log-Step "INFO" "Report not live yet, HTTP ${statusCode}. Retry $i/45."
            } else {
                Log-Step "INFO" "Report not live yet: $($_.Exception.Message). Retry $i/45."
            }
        }

        Start-Sleep -Seconds 2
    }

    Log-Step "WARN" "Public report did not become ready yet. Try opening it again shortly: $ReportUrl"
    return $false
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

function Start-NewTunnel {
    if (-not (Test-Path $CloudflaredPath)) {
        throw "cloudflared.exe was not found at $CloudflaredPath"
    }

    Log-Step "INFO" "Starting a new Cloudflare quick tunnel."
    Stop-ByName -Name "cloudflared"

    if (Test-Path $CloudflaredLog) {
        Clear-Content -Path $CloudflaredLog
    }

    Start-Process -WindowStyle Hidden -FilePath $CloudflaredPath -ArgumentList @(
        "tunnel",
        "--url",
        $LocalTunnelUrl,
        "--logfile",
        $CloudflaredLog
    )

    $baseUrl = Wait-ForTunnelUrl
    Log-Step "SUCCESS" "New Cloudflare tunnel: $baseUrl"
    return $baseUrl
}

function Update-GitHubWebhookUrl {
    param(
        [string]$WebhookUrl,
        [string]$OldWebhookUrl
    )

    $repo = Get-EnvValue -Name "TARGET_REPO"
    $token = Get-EnvValue -Name "GITHUB_TOKEN"
    $hookId = Get-EnvValue -Name "GITHUB_WEBHOOK_ID"

    if (-not $repo -or -not $token) {
        Log-Step "WARN" "Skipped GitHub webhook update; TARGET_REPO or GITHUB_TOKEN is missing in .env. Manually set Payload URL to $WebhookUrl"
        return $false
    }

    $headers = @{
        "Accept" = "application/vnd.github+json"
        "Authorization" = "Bearer $token"
        "X-GitHub-Api-Version" = "2022-11-28"
        "User-Agent" = "devin-design-gate"
    }

    try {
        if (-not $hookId) {
            $hooks = Invoke-RestMethod -Method Get -Uri "https://api.github.com/repos/$repo/hooks" -Headers $headers -TimeoutSec 30
            $matchingHook = $hooks | Where-Object {
                ($OldWebhookUrl -and $_.config.url -eq $OldWebhookUrl) -or
                ($_.config.url -match "/github/webhook$")
            } | Select-Object -First 1

            if (-not $matchingHook) {
                Log-Step "WARN" "No matching GitHub webhook found for $repo. Manually set Payload URL to $WebhookUrl"
                return $false
            }

            $hookId = $matchingHook.id
        }

        $body = @{
            config = @{
                url = $WebhookUrl
                content_type = "json"
            }
        } | ConvertTo-Json -Depth 5

        Invoke-RestMethod -Method Patch -Uri "https://api.github.com/repos/$repo/hooks/$hookId" -Headers $headers -Body $body -ContentType "application/json" -TimeoutSec 30 | Out-Null
        Log-Step "SUCCESS" "Updated GitHub webhook $hookId for $repo."
        return $true
    } catch {
        Log-Step "WARN" "Could not update GitHub webhook automatically: $($_.Exception.Message). Manually set Payload URL to $WebhookUrl"
        return $false
    }
}

function Open-Report {
    param([string]$ReportUrl)

    if ($NoOpen) {
        return
    }

    try {
        Start-Process $ReportUrl
        Log-Step "SUCCESS" "Opened report: $ReportUrl"
    } catch {
        Log-Step "WARN" "Could not open browser. Report URL: $ReportUrl"
    }
}

Set-Content -Path $RunLog -Value "$(Get-Date -Format o) [INFO] Starting run.ps1"
Start-FastApi

$oldWebhookUrl = Get-EnvValue -Name "PUBLIC_WEBHOOK_URL"
$publicBaseUrl = ""
if ($oldWebhookUrl -match "^(https://[^/]+)/github/webhook$") {
    $publicBaseUrl = $Matches[1]
}

$tunnelHealthy = Test-Tunnel -BaseUrl $publicBaseUrl
if (-not $tunnelHealthy) {
    $publicBaseUrl = Start-NewTunnel
    $newWebhookUrl = "$publicBaseUrl/github/webhook"
    Set-EnvValue -Name "PUBLIC_WEBHOOK_URL" -Value $newWebhookUrl
    Log-Step "SUCCESS" "Updated .env PUBLIC_WEBHOOK_URL."
    Update-GitHubWebhookUrl -WebhookUrl $newWebhookUrl -OldWebhookUrl $oldWebhookUrl | Out-Null
} else {
    $newWebhookUrl = $oldWebhookUrl
    Log-Step "SUCCESS" "Kept existing PUBLIC_WEBHOOK_URL."
}

Log-Step "INFO" "Health URL: $publicBaseUrl/health"
Log-Step "INFO" "Webhook URL: $newWebhookUrl"
$reportUrl = "$publicBaseUrl/report-html"
Log-Step "INFO" "Report URL: $reportUrl"
Wait-ForPublicReport -ReportUrl $reportUrl | Out-Null
Open-Report -ReportUrl $reportUrl

Write-Host ""
Write-Host "====================================================="
Write-Host "Devin Design Gate is running."
Write-Host "Report URL: $publicBaseUrl/report-html"
Write-Host "Webhook URL: $newWebhookUrl"
Write-Host "Log: $RunLog"
Write-Host "Press Ctrl+C to stop FastAPI. Existing cloudflared is left running."
Write-Host "====================================================="

try {
    while ($true) {
        Start-Sleep -Seconds 1
    }
} finally {
    Log-Step "INFO" "Stopping FastAPI."
    Stop-Uvicorn
}
