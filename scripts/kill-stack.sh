#!/usr/bin/env bash
# T-Mobile Dashboard - Kill Zombie Processes
# Finds and terminates any orphaned processes from the stack

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

banner() {
    echo ""
    echo -e "${YELLOW}============================================================${NC}"
    echo -e "${YELLOW}$1${NC}"
    echo -e "${YELLOW}============================================================${NC}"
    echo ""
}

kill_by_pattern() {
    local pattern="$1"
    local description="$2"
    local pids=""

    # Find PIDs matching the pattern in command line
    if command -v pgrep &> /dev/null; then
        pids=$(pgrep -f "$pattern" 2>/dev/null || true)
    else
        pids=$(ps aux | grep "$pattern" | grep -v grep | awk '{print $2}' || true)
    fi

    if [ -n "$pids" ]; then
        local count=$(echo "$pids" | wc -w)
        echo -e "  ${YELLOW}Found $count $description process(es)${NC}"
        for pid in $pids; do
            local proc_name=$(ps -p "$pid" -o comm= 2>/dev/null || echo "unknown")
            echo -e "    ${RED}Killing PID $pid ($proc_name)${NC}"
            kill -9 "$pid" 2>/dev/null || true
        done
        return 0
    fi
    return 1
}

check_port() {
    local port=$1
    local pid=""

    if command -v lsof &> /dev/null; then
        pid=$(lsof -ti ":$port" 2>/dev/null | head -1)
    elif command -v ss &> /dev/null; then
        pid=$(ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K\d+' | head -1)
    elif command -v netstat &> /dev/null; then
        pid=$(netstat -tlnp 2>/dev/null | grep ":$port " | grep -oP '\d+(?=/)' | head -1)
    fi

    if [ -n "$pid" ]; then
        local proc_name=$(ps -p "$pid" -o comm= 2>/dev/null || echo "unknown")
        echo -e "  Port $port: ${RED}IN USE${NC} (PID $pid - $proc_name)"
    else
        echo -e "  Port $port: ${GREEN}free${NC}"
    fi
}

banner "T-Mobile Dashboard - Cleanup Zombie Processes"

found_any=false

# 1. Kill Python backend processes
echo -e "${CYAN}[1/5] Checking for Python backend processes...${NC}"
if kill_by_pattern "tmobile_dashboard" "backend"; then
    found_any=true
else
    echo -e "      ${GREEN}None found.${NC}"
fi

# 2. Kill uvicorn processes
echo -e "${CYAN}[2/5] Checking for uvicorn processes...${NC}"
if kill_by_pattern "uvicorn" "uvicorn"; then
    found_any=true
else
    echo -e "      ${GREEN}None found.${NC}"
fi

# 3. Kill Vite/Node processes
echo -e "${CYAN}[3/5] Checking for Vite/Node processes...${NC}"
if kill_by_pattern "vite" "Vite"; then
    found_any=true
else
    echo -e "      ${GREEN}None found.${NC}"
fi

# 4. Kill Bun processes
echo -e "${CYAN}[4/5] Checking for Bun processes...${NC}"
if kill_by_pattern "bun.*dev" "Bun dev"; then
    found_any=true
else
    echo -e "      ${GREEN}None found.${NC}"
fi

# 5. Stop and remove containers
echo -e "${CYAN}[5/5] Checking for stack containers...${NC}"

containers=(
    "tmobile-grafana"
    "tmobile-prometheus"
    "tmobile-alertmanager"
    "tmobile-mimir"
    "tmobile-loki"
    "tmobile-tempo"
    "tmobile-gateway-exporter"
)

container_runtime=""
if command -v podman &> /dev/null; then
    container_runtime="podman"
elif command -v docker &> /dev/null; then
    container_runtime="docker"
fi

if [ -n "$container_runtime" ]; then
    running_containers=$($container_runtime ps --format "{{.Names}}" 2>/dev/null || true)
    stopped_any=false

    for container in "${containers[@]}"; do
        if echo "$running_containers" | grep -q "^${container}$"; then
            echo -e "  ${YELLOW}Stopping container: $container${NC}"
            $container_runtime stop "$container" 2>/dev/null || true
            $container_runtime rm "$container" 2>/dev/null || true
            stopped_any=true
            found_any=true
        fi
    done

    if [ "$stopped_any" = false ]; then
        echo -e "      ${GREEN}No stack containers running.${NC}"
    fi
else
    echo -e "      ${CYAN}No container runtime found.${NC}"
fi

# Summary
echo ""
if [ "$found_any" = true ]; then
    echo -e "${GREEN}Cleanup complete. Killed orphaned processes.${NC}"
else
    echo -e "${GREEN}No zombie processes found. Stack is clean.${NC}"
fi
echo ""

# Show port status
echo -e "${CYAN}Port status:${NC}"
ports=(5173 8080 3002 9090 9093 9100)
for port in "${ports[@]}"; do
    check_port "$port"
done
echo ""
