#!/bin/bash
# Verify full-stack integration - quiet success, loud failure
# Usage: ./scripts/verify-integration.sh [api_port]

SCRIPT_DIR="$(dirname "$0")"
API_PORT="${1:-3001}"
API_URL="http://localhost:$API_PORT"

# Step 1: Verify API is running
curl -sf "$API_URL/health" >/dev/null || {
    echo "FAIL: API server not running at $API_URL"
    echo "  - Start with: bun run dev in apps/api"
    echo "  - Then re-run this verification"
    exit 1
}

# Step 2: Verify frontend build exists
[ -d "$SCRIPT_DIR/../apps/web/dist" ] || {
    echo "FAIL: Frontend build not found"
    echo "  - Run: bun run build:web"
    exit 1
}

# Step 3: Test API endpoints respond with expected data types

# Signal data endpoint
SIGNAL_RESP=$(curl -sf "$API_URL/api/signal")
echo "$SIGNAL_RESP" | grep -q '"nr_sinr"' || {
    echo "FAIL: /api/signal missing NR SINR data"
    echo "  - Response: $SIGNAL_RESP"
    echo "  - Check gateway connectivity"
    exit 1
}

# Disruptions endpoint
curl -sf "$API_URL/api/disruptions" | head -c 1 | grep -q '[' || {
    echo "FAIL: /api/disruptions not returning expected format"
    echo "  - Check apps/api/src/routes/disruptions.ts"
    exit 1
}

# Gateway info endpoint
curl -sf "$API_URL/api/gateway/info" | grep -q '"device_name"' || {
    echo "FAIL: /api/gateway/info not responding"
    echo "  - Check apps/api/src/routes/gateway.ts"
    exit 1
}

# All integration checks passed - silent success
