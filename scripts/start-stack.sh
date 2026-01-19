#!/usr/bin/env bash
# NetPulse - Full Stack Startup Script
# Starts infra (Grafana container), backend, and frontend with LAN access
# Press Ctrl+C to gracefully stop all services

set -e

# Get script directory and repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# PIDs for background processes
BACKEND_PID=""
FRONTEND_PID=""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

banner() {
    local color=$1
    local message=$2
    echo ""
    echo -e "${color}============================================================${NC}"
    echo -e "${color}${message}${NC}"
    echo -e "${color}============================================================${NC}"
    echo ""
}

get_lan_ip() {
    # Try different methods to get LAN IP
    local ip=""

    # Linux
    if command -v ip &> /dev/null; then
        ip=$(ip route get 1 2>/dev/null | awk '{print $7; exit}')
    fi

    # macOS
    if [ -z "$ip" ] && command -v ifconfig &> /dev/null; then
        ip=$(ifconfig | grep "inet " | grep -v 127.0.0.1 | awk '{print $2}' | head -1)
    fi

    # Fallback
    if [ -z "$ip" ]; then
        ip="localhost"
    fi

    echo "$ip"
}

cleanup() {
    banner "$YELLOW" "Shutting down..."

    # Stop frontend
    if [ -n "$FRONTEND_PID" ] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
        echo -e "${YELLOW}Stopping frontend...${NC}"
        kill "$FRONTEND_PID" 2>/dev/null || true
        wait "$FRONTEND_PID" 2>/dev/null || true
    fi

    # Stop backend
    if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo -e "${YELLOW}Stopping backend...${NC}"
        kill "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
    fi

    # Stop Grafana container
    echo -e "${YELLOW}Stopping Grafana container...${NC}"
    cd "$REPO_ROOT"
    if command -v podman &> /dev/null; then
        podman compose -f infra/docker-compose.yml down 2>/dev/null || true
    elif command -v docker &> /dev/null; then
        docker compose -f infra/docker-compose.yml down 2>/dev/null || true
    fi

    echo -e "${GREEN}All services stopped.${NC}"
    exit 0
}

# Register cleanup on exit signals
trap cleanup SIGINT SIGTERM EXIT

main() {
    banner "$GREEN" "NetPulse - Full Stack"

    cd "$REPO_ROOT"

    # Step 1: Check container runtime
    echo -e "${CYAN}[1/5] Checking container runtime...${NC}"
    if command -v podman &> /dev/null; then
        COMPOSE_CMD="podman compose"
        # Check if podman machine is needed (macOS/Windows)
        if [[ "$OSTYPE" == "darwin"* ]] || [[ "$(uname -r)" == *"WSL"* ]]; then
            machine_running=$(podman machine list --format "{{.Running}}" 2>/dev/null | head -1)
            if [ "$machine_running" != "true" ] && [ "$machine_running" != "Running" ]; then
                echo -e "      ${YELLOW}Starting Podman machine...${NC}"
                podman machine start 2>/dev/null || true
                sleep 3
            fi
        fi
        echo -e "      ${GREEN}Using Podman.${NC}"
    elif command -v docker &> /dev/null; then
        COMPOSE_CMD="docker compose"
        echo -e "      ${GREEN}Using Docker.${NC}"
    else
        echo -e "${RED}Error: Neither podman nor docker found. Please install one.${NC}"
        exit 1
    fi

    # Step 2: Start Grafana container (includes PostgreSQL)
    echo -e "${CYAN}[2/5] Starting Grafana container...${NC}"

    $COMPOSE_CMD -f infra/docker-compose.yml up -d
    echo -e "      ${GREEN}Grafana container started.${NC}"

    # Step 3: Wait for PostgreSQL and run migrations
    echo -e "${CYAN}[3/5] Running database migrations...${NC}"
    cd "$REPO_ROOT/apps/api"

    # Wait for PostgreSQL to be ready (up to 30 seconds)
    for i in {1..30}; do
        if bun run db:migrate 2>/dev/null; then
            echo -e "      ${GREEN}Database migrations complete.${NC}"
            break
        fi
        if [ $i -eq 30 ]; then
            echo -e "      ${RED}Warning: Database migration failed. PostgreSQL may not be ready.${NC}"
        fi
        sleep 1
    done
    cd "$REPO_ROOT"

    # Step 4: Start backend
    echo -e "${CYAN}[4/5] Starting backend (port 3001)...${NC}"
    cd "$REPO_ROOT/apps/api"
    bun run dev &
    BACKEND_PID=$!
    cd "$REPO_ROOT"
    sleep 2
    echo -e "      ${GREEN}Backend started (PID: $BACKEND_PID).${NC}"

    # Step 5: Start frontend with LAN access
    echo -e "${CYAN}[5/5] Starting frontend (port 5173 with LAN access)...${NC}"
    bun run dev:web -- --host 0.0.0.0 &
    FRONTEND_PID=$!
    sleep 3
    echo -e "      ${GREEN}Frontend started (PID: $FRONTEND_PID).${NC}"

    # Print access URLs
    LAN_IP=$(get_lan_ip)
    banner "$GREEN" "Stack is running!"

    echo -e "${WHITE}Local access:${NC}"
    echo -e "  ${CYAN}Frontend:     http://localhost:5173${NC}"
    echo -e "  ${CYAN}Backend API:  http://localhost:3001${NC}"
    echo -e "  ${CYAN}Grafana:      http://localhost:3002  (admin/netpulse123)${NC}"
    echo ""
    echo -e "${WHITE}LAN access (from other devices):${NC}"
    echo -e "  ${CYAN}Frontend:     http://${LAN_IP}:5173${NC}"
    echo -e "  ${CYAN}Backend API:  http://${LAN_IP}:3001${NC}"
    echo -e "  ${CYAN}Grafana:      http://${LAN_IP}:3002${NC}"
    echo ""
    echo -e "${YELLOW}Press Ctrl+C to stop all services...${NC}"
    echo ""

    # Wait for background processes
    wait
}

main "$@"
