#!/bin/bash
# Run all verification scripts - quiet success, loud failure
# Usage: ./scripts/verify-all.sh [api_port]

SCRIPT_DIR="$(dirname "$0")"
API_PORT="${1:-3001}"
FAILED=0

# API verification (only if server is running)
if curl -sf "http://localhost:$API_PORT/health" >/dev/null 2>&1; then
    "$SCRIPT_DIR/verify-api.sh" "$API_PORT" || FAILED=1
else
    echo "SKIP: API verification (server not running on port $API_PORT)"
fi

# Frontend verification
"$SCRIPT_DIR/verify-frontend.sh" || FAILED=1

# Integration verification (only if server is running)
if curl -sf "http://localhost:$API_PORT/health" >/dev/null 2>&1; then
    "$SCRIPT_DIR/verify-integration.sh" "$API_PORT" || FAILED=1
else
    echo "SKIP: Integration verification (server not running)"
fi

exit $FAILED
