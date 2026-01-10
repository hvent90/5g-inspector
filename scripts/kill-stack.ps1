# T-Mobile Dashboard - Kill Zombie Processes
# Finds and terminates any orphaned processes from the stack

$ErrorActionPreference = "SilentlyContinue"

function Write-Banner {
    param([string]$Message, [string]$Color = "Cyan")
    Write-Host ""
    Write-Host ("=" * 60) -ForegroundColor $Color
    Write-Host $Message -ForegroundColor $Color
    Write-Host ("=" * 60) -ForegroundColor $Color
    Write-Host ""
}

function Kill-ProcessByPattern {
    param(
        [string]$Pattern,
        [string]$Description
    )

    $processes = Get-Process | Where-Object {
        $_.ProcessName -match $Pattern -or
        ($_.Path -and $_.Path -match $Pattern) -or
        ($_.CommandLine -and $_.CommandLine -match $Pattern)
    }

    # Also check via WMI for command line matching
    $wmiProcesses = Get-WmiObject Win32_Process | Where-Object {
        $_.CommandLine -match $Pattern
    }

    $allPids = @()
    if ($processes) { $allPids += $processes.Id }
    if ($wmiProcesses) { $allPids += $wmiProcesses.ProcessId }
    $allPids = $allPids | Sort-Object -Unique

    if ($allPids.Count -gt 0) {
        Write-Host "  Found $($allPids.Count) $Description process(es)" -ForegroundColor Yellow
        foreach ($pid in $allPids) {
            try {
                $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
                if ($proc) {
                    Write-Host "    Killing PID $pid ($($proc.ProcessName))" -ForegroundColor Red
                    Stop-Process -Id $pid -Force
                }
            } catch {
                # Process may have already exited
            }
        }
        return $true
    }
    return $false
}

Write-Banner "T-Mobile Dashboard - Cleanup Zombie Processes" "Yellow"

$foundAny = $false

# 1. Kill Python backend processes
Write-Host "[1/5] Checking for Python backend processes..." -ForegroundColor Cyan
if (Kill-ProcessByPattern "tmobile_dashboard" "backend") { $foundAny = $true }
else { Write-Host "      None found." -ForegroundColor Green }

# 2. Kill uvicorn processes
Write-Host "[2/5] Checking for uvicorn processes..." -ForegroundColor Cyan
if (Kill-ProcessByPattern "uvicorn" "uvicorn") { $foundAny = $true }
else { Write-Host "      None found." -ForegroundColor Green }

# 3. Kill Vite/Node processes (frontend dev server)
Write-Host "[3/5] Checking for Vite/Node processes..." -ForegroundColor Cyan
$viteKilled = $false
# Look for node processes running vite
$nodeProcesses = Get-WmiObject Win32_Process | Where-Object {
    $_.Name -eq "node.exe" -and $_.CommandLine -match "vite"
}
if ($nodeProcesses) {
    Write-Host "  Found $($nodeProcesses.Count) Vite process(es)" -ForegroundColor Yellow
    foreach ($proc in $nodeProcesses) {
        Write-Host "    Killing PID $($proc.ProcessId)" -ForegroundColor Red
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    $viteKilled = $true
    $foundAny = $true
}
if (-not $viteKilled) { Write-Host "      None found." -ForegroundColor Green }

# 4. Kill Bun processes
Write-Host "[4/5] Checking for Bun processes..." -ForegroundColor Cyan
$bunProcesses = Get-Process -Name "bun" -ErrorAction SilentlyContinue
if ($bunProcesses) {
    Write-Host "  Found $($bunProcesses.Count) Bun process(es)" -ForegroundColor Yellow
    foreach ($proc in $bunProcesses) {
        Write-Host "    Killing PID $($proc.Id)" -ForegroundColor Red
        Stop-Process -Id $proc.Id -Force
    }
    $foundAny = $true
} else {
    Write-Host "      None found." -ForegroundColor Green
}

# 5. Stop and remove Docker/Podman containers
Write-Host "[5/5] Checking for stack containers..." -ForegroundColor Cyan
$containers = @(
    "tmobile-grafana",
    "tmobile-prometheus",
    "tmobile-alertmanager",
    "tmobile-mimir",
    "tmobile-loki",
    "tmobile-tempo",
    "tmobile-gateway-exporter"
)

$containerRuntime = $null
if (Get-Command podman -ErrorAction SilentlyContinue) { $containerRuntime = "podman" }
elseif (Get-Command docker -ErrorAction SilentlyContinue) { $containerRuntime = "docker" }

if ($containerRuntime) {
    $runningContainers = & $containerRuntime ps --format "{{.Names}}" 2>$null
    $stoppedAny = $false

    foreach ($container in $containers) {
        if ($runningContainers -contains $container) {
            Write-Host "  Stopping container: $container" -ForegroundColor Yellow
            & $containerRuntime stop $container 2>$null | Out-Null
            & $containerRuntime rm $container 2>$null | Out-Null
            $stoppedAny = $true
            $foundAny = $true
        }
    }

    if (-not $stoppedAny) {
        Write-Host "      No stack containers running." -ForegroundColor Green
    }
} else {
    Write-Host "      No container runtime found." -ForegroundColor Gray
}

# Summary
Write-Host ""
if ($foundAny) {
    Write-Host "Cleanup complete. Killed orphaned processes." -ForegroundColor Green
} else {
    Write-Host "No zombie processes found. Stack is clean." -ForegroundColor Green
}
Write-Host ""

# Show what's still listening on our ports
Write-Host "Port status:" -ForegroundColor Cyan
$ports = @(5173, 8080, 3002, 9090, 9093, 9100)
foreach ($port in $ports) {
    $conn = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        Write-Host "  Port $port`: " -NoNewline
        Write-Host "IN USE" -ForegroundColor Red -NoNewline
        Write-Host " (PID $($conn.OwningProcess) - $($proc.ProcessName))"
    } else {
        Write-Host "  Port $port`: " -NoNewline
        Write-Host "free" -ForegroundColor Green
    }
}
Write-Host ""
