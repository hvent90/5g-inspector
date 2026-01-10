#!/bin/bash
# Verify API health checks - quiet success, loud failure
# Usage: ./scripts/verify-api.sh [port]

PORT="${1:-8080}"
BASE_URL="http://localhost:$PORT"

# Health check - verify server responds with valid HTML
curl -sf "$BASE_URL/" | head -c 15 | grep -q '<!DOCTYPE html>' || {
    echo "FAIL: Server not responding at $BASE_URL"
    echo "  - Is server.py running? python server.py"
    echo "  - Check if port $PORT is in use: netstat -an | grep $PORT"
    exit 1
}

# API endpoint check - verify /api/signal returns JSON
curl -sf "$BASE_URL/api/signal" | head -c 1 | grep -q '{' || {
    echo "FAIL: /api/signal not returning JSON"
    echo "  - Server may be running but API routes broken"
    echo "  - Check server.py for errors in ProxyHandler"
    exit 1
}

# Database stats check - verify DB is accessible
curl -sf "$BASE_URL/api/db-stats" | grep -q '"total_records"' || {
    echo "FAIL: /api/db-stats not returning expected data"
    echo "  - Database may not be initialized"
    echo "  - Check signal_history.db exists and is accessible"
    exit 1
}

# Alerting system check
curl -sf "$BASE_URL/api/alerts/config" | grep -q '"enabled"' || {
    echo "FAIL: /api/alerts/config not responding"
    echo "  - Alerting system may not be initialized"
    echo "  - Check alerting.py for import errors"
    exit 1
}

# All checks passed - silent success
