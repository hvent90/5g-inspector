# NetPulse - Kill Zombie Processes
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

Write-Banner "NetPulse - Cleanup Zombie Processes" "Yellow"

$foundAny = $false

# 1. Kill API backend processes (Bun)
Write-Host "[1/4] Checking for API backend processes..." -ForegroundColor Cyan
if (Kill-ProcessByPattern "apps.api" "api backend") { $foundAny = $true }
else { Write-Host "      None found." -ForegroundColor Green }

# 2. Kill Vite/Node processes (frontend dev server)
Write-Host "[2/4] Checking for Vite/Node processes..." -ForegroundColor Cyan
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

# 3. Kill Bun processes
Write-Host "[3/4] Checking for Bun processes..." -ForegroundColor Cyan
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

# 4. Stop and remove Docker/Podman containers (Grafana only)
Write-Host "[4/4] Checking for stack containers..." -ForegroundColor Cyan
$containers = @(
    "netpulse-grafana"
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
$ports = @(5173, 3001, 3002)
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
