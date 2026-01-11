# T-Mobile Dashboard - Full Stack Startup Script
# Starts infra (containers), backend, and frontend with LAN access
# Press Ctrl+C to gracefully stop all services

$ErrorActionPreference = "Stop"
$script:BackendJob = $null
$script:FrontendJob = $null

# Get the repo root directory
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Get-LanIP {
    # Get the first non-loopback IPv4 address
    $ip = (Get-NetIPAddress -AddressFamily IPv4 |
           Where-Object { $_.InterfaceAlias -notmatch "Loopback" -and $_.IPAddress -notmatch "^169\." } |
           Select-Object -First 1).IPAddress
    if (-not $ip) { $ip = "localhost" }
    return $ip
}

function Write-Banner {
    param([string]$Message, [string]$Color = "Cyan")
    Write-Host ""
    Write-Host ("=" * 60) -ForegroundColor $Color
    Write-Host $Message -ForegroundColor $Color
    Write-Host ("=" * 60) -ForegroundColor $Color
    Write-Host ""
}

function Stop-AllServices {
    Write-Banner "Shutting down..." "Yellow"

    # Stop frontend
    if ($script:FrontendJob) {
        Write-Host "Stopping frontend..." -ForegroundColor Yellow
        Stop-Job -Job $script:FrontendJob -ErrorAction SilentlyContinue
        Remove-Job -Job $script:FrontendJob -Force -ErrorAction SilentlyContinue
    }

    # Stop backend
    if ($script:BackendJob) {
        Write-Host "Stopping backend..." -ForegroundColor Yellow
        Stop-Job -Job $script:BackendJob -ErrorAction SilentlyContinue
        Remove-Job -Job $script:BackendJob -Force -ErrorAction SilentlyContinue
    }

    # Stop infra containers
    Write-Host "Stopping infra containers..." -ForegroundColor Yellow
    Push-Location $RepoRoot
    & podman compose -f infra/docker-compose.yml down 2>$null
    Pop-Location

    Write-Host "All services stopped." -ForegroundColor Green
}

# Register cleanup on script exit
$null = Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action { Stop-AllServices }

try {
    Write-Banner "T-Mobile Dashboard - Full Stack" "Green"

    # Step 1: Check/Start Podman machine
    Write-Host "[1/4] Checking Podman machine..." -ForegroundColor Cyan
    $machineStatus = & podman machine list --format "{{.Running}}" 2>$null | Select-Object -First 1
    if ($machineStatus -ne "true" -and $machineStatus -ne "Running") {
        Write-Host "      Starting Podman machine..." -ForegroundColor Yellow
        $startResult = & podman machine start 2>&1
        if ($LASTEXITCODE -ne 0) {
            # Check if it's just "already running" - that's OK
            if ($startResult -match "already running") {
                Write-Host "      Podman machine already running." -ForegroundColor Gray
            } else {
                throw "Failed to start Podman machine: $startResult"
            }
        } else {
            Start-Sleep -Seconds 3
        }
    }
    Write-Host "      Podman machine is running." -ForegroundColor Green

    # Step 2: Configure Prometheus with host IP and start infra containers
    Write-Host "[2/4] Starting infra containers..." -ForegroundColor Cyan
    $lanIP = Get-LanIP

    # Update Prometheus config with the host IP so it can scrape the backend
    $promConfig = "$RepoRoot/infra/prometheus/prometheus.yml"
    $content = Get-Content $promConfig -Raw
    $content = $content -replace "targets: \['[^']+:8080'\]", "targets: ['${lanIP}:8080']"
    Set-Content $promConfig $content
    Write-Host "      Configured Prometheus to scrape backend at ${lanIP}:8080" -ForegroundColor Gray

    Push-Location $RepoRoot
    & podman compose -f infra/docker-compose.yml up -d
    Pop-Location
    Write-Host "      Infra containers started." -ForegroundColor Green

    # Step 3: Start backend
    Write-Host "[3/4] Starting backend (port 8080)..." -ForegroundColor Cyan
    $script:BackendJob = Start-Job -ScriptBlock {
        param($root)
        Set-Location "$root/backend"
        $env:LOG_LEVEL = "WARNING"
        & uv run python -m tmobile_dashboard.main 2>&1
    } -ArgumentList $RepoRoot
    Start-Sleep -Seconds 2
    Write-Host "      Backend started." -ForegroundColor Green

    # Step 4: Start frontend with LAN access
    Write-Host "[4/4] Starting frontend (port 5173 with LAN access)..." -ForegroundColor Cyan
    $script:FrontendJob = Start-Job -ScriptBlock {
        param($root)
        Set-Location $root
        & bun run dev:web -- --host 0.0.0.0 2>&1
    } -ArgumentList $RepoRoot
    Start-Sleep -Seconds 3
    Write-Host "      Frontend started." -ForegroundColor Green

    # Print access URLs
    $lanIP = Get-LanIP
    Write-Banner "Stack is running!" "Green"
    Write-Host "Local access:" -ForegroundColor White
    Write-Host "  Frontend:     http://localhost:5173" -ForegroundColor Cyan
    Write-Host "  Backend API:  http://localhost:8080" -ForegroundColor Cyan
    Write-Host "  Grafana:      http://localhost:3002  (admin/tmobile123)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "LAN access (from other devices):" -ForegroundColor White
    Write-Host "  Frontend:     http://${lanIP}:5173" -ForegroundColor Cyan
    Write-Host "  Backend API:  http://${lanIP}:8080" -ForegroundColor Cyan
    Write-Host "  Grafana:      http://${lanIP}:3002" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Press Ctrl+C to stop all services..." -ForegroundColor Yellow
    Write-Host ""

    # Keep script running and show logs
    while ($true) {
        # Check if jobs are still running
        if ($script:BackendJob.State -eq "Failed") {
            Write-Host "Backend job failed!" -ForegroundColor Red
            Receive-Job -Job $script:BackendJob
            break
        }
        if ($script:FrontendJob.State -eq "Failed") {
            Write-Host "Frontend job failed!" -ForegroundColor Red
            Receive-Job -Job $script:FrontendJob
            break
        }

        # Show any new output from jobs
        Receive-Job -Job $script:BackendJob -ErrorAction SilentlyContinue | Write-Host
        Receive-Job -Job $script:FrontendJob -ErrorAction SilentlyContinue | Write-Host

        Start-Sleep -Seconds 1
    }
}
catch {
    Write-Host "Error: $_" -ForegroundColor Red
}
finally {
    Stop-AllServices
}
