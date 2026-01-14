#!/bin/bash
# Verify API health checks - quiet success, loud failure
# Usage: ./scripts/verify-api.sh [port]

PORT="${1:-3001}"
BASE_URL="http://localhost:$PORT"

# Health check - verify server responds with healthy status
curl -sf "$BASE_URL/health" | grep -q '"status":"healthy"' || {
    echo "FAIL: Server not responding at $BASE_URL/health"
    echo "  - Is the API running? bun run dev in apps/api"
    echo "  - Check if port $PORT is in use: netstat -an | grep $PORT"
    exit 1
}

# API endpoint check - verify /api/signal returns JSON
curl -sf "$BASE_URL/api/signal" | head -c 1 | grep -q '{' || {
    echo "FAIL: /api/signal not returning JSON"
    echo "  - Server may be running but API routes broken"
    echo "  - Check apps/api/src/routes/signal.ts for errors"
    exit 1
}

# Alerts endpoint check
curl -sf "$BASE_URL/api/alerts" | head -c 1 | grep -q '[' || {
    echo "FAIL: /api/alerts not returning expected format"
    echo "  - Check apps/api/src/routes/alerts.ts"
    exit 1
}

# All checks passed - silent success
