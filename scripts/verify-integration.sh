#!/bin/bash
# Verify full-stack integration - quiet success, loud failure
# Usage: ./scripts/verify-integration.sh [api_port]

SCRIPT_DIR="$(dirname "$0")"
API_PORT="${1:-8080}"
API_URL="http://localhost:$API_PORT"

# Step 1: Verify API is running
curl -sf "$API_URL/" >/dev/null || {
    echo "FAIL: API server not running at $API_URL"
    echo "  - Start with: python server.py"
    echo "  - Then re-run this verification"
    exit 1
}

# Step 2: Verify frontend build exists
[ -d "$SCRIPT_DIR/../frontend/dist" ] || {
    echo "FAIL: Frontend build not found"
    echo "  - Run: cd frontend && npm run build"
    exit 1
}

# Step 3: Test API endpoints respond with expected data types

# Signal data endpoint
SIGNAL_RESP=$(curl -sf "$API_URL/api/signal")
echo "$SIGNAL_RESP" | grep -q '"4g"' || {
    echo "FAIL: /api/signal missing 4G data"
    echo "  - Response: $SIGNAL_RESP"
    echo "  - Check gateway connectivity"
    exit 1
}

# History endpoint with parameters
curl -sf "$API_URL/api/history?duration=60&resolution=auto" | grep -q '"data"' || {
    echo "FAIL: /api/history not returning expected format"
    echo "  - Database may be empty or query failing"
    exit 1
}

# Scheduler status
curl -sf "$API_URL/api/scheduler/status" | grep -q '"enabled"' || {
    echo "FAIL: /api/scheduler/status not responding"
    echo "  - Check scheduler.py import in server.py"
    exit 1
}

# Service terms endpoint
curl -sf "$API_URL/api/service-terms" | head -c 1 | grep -q '{' || {
    echo "FAIL: /api/service-terms not returning JSON"
    echo "  - Check service_terms.py module"
    exit 1
}

# Step 4: Verify index.html serves properly
curl -sf "$API_URL/index.html" | grep -q '<title>' || {
    echo "FAIL: index.html not serving correctly"
    echo "  - Check file permissions and path"
    exit 1
}

# All integration checks passed - silent success
