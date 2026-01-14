# NetPulse - Full Stack Startup Script
# Starts infra (Grafana container), backend, and frontend with LAN access
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

    # Stop Grafana container
    Write-Host "Stopping Grafana container..." -ForegroundColor Yellow
    Push-Location $RepoRoot
    # Use cmd /c to prevent PowerShell from treating podman-compose stderr messages as errors
    cmd /c "podman compose -f infra/docker-compose.yml down 2>&1" | Out-Null
    Pop-Location

    Write-Host "All services stopped." -ForegroundColor Green
}

# Register cleanup on script exit
$null = Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action { Stop-AllServices }

try {
    Write-Banner "NetPulse - Full Stack" "Green"

    # Step 1: Check/Start Podman machine
    Write-Host "[1/4] Checking Podman machine..." -ForegroundColor Cyan

    # Helper function to check if machine is actually running
    function Test-PodmanMachineRunning {
        $status = & podman machine list --format "{{.Running}}" 2>$null | Select-Object -First 1
        return ($status -eq "true" -or $status -eq "Running")
    }

    if (-not (Test-PodmanMachineRunning)) {
        Write-Host "      Starting Podman machine..." -ForegroundColor Yellow
        # Run podman machine start, ignoring stderr warnings like "screen size is bogus"
        # and "already running" messages. Use cmd /c to prevent PowerShell's
        # ErrorActionPreference from treating stderr as terminating errors.
        cmd /c "podman machine start 2>&1" | Out-Null

        # Wait for machine to be ready (up to 30 seconds)
        $attempts = 0
        $maxAttempts = 10
        while (-not (Test-PodmanMachineRunning) -and $attempts -lt $maxAttempts) {
            Start-Sleep -Seconds 3
            $attempts++
            Write-Host "      Waiting for Podman machine... (attempt $attempts/$maxAttempts)" -ForegroundColor Gray
        }

        if (-not (Test-PodmanMachineRunning)) {
            throw "Podman machine failed to start after $maxAttempts attempts. Try: podman machine stop; podman machine start"
        }
    }
    Write-Host "      Podman machine is running." -ForegroundColor Green

    # Step 2: Start Grafana container
    Write-Host "[2/4] Starting Grafana container..." -ForegroundColor Cyan

    Push-Location $RepoRoot
    # Use cmd /c to prevent PowerShell from treating podman-compose stderr messages as errors
    cmd /c "podman compose -f infra/docker-compose.yml up -d 2>&1"
    Pop-Location
    Write-Host "      Grafana container started." -ForegroundColor Green

    # Step 3: Start backend
    Write-Host "[3/4] Starting backend (port 3001)..." -ForegroundColor Cyan
    $script:BackendJob = Start-Job -ScriptBlock {
        param($root)
        Set-Location "$root/apps/api"
        & bun run dev 2>&1
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
    Write-Host "  Backend API:  http://localhost:3001" -ForegroundColor Cyan
    Write-Host "  Grafana:      http://localhost:3002  (admin/netpulse123)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "LAN access (from other devices):" -ForegroundColor White
    Write-Host "  Frontend:     http://${lanIP}:5173" -ForegroundColor Cyan
    Write-Host "  Backend API:  http://${lanIP}:3001" -ForegroundColor Cyan
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
