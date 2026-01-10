#!/bin/bash
# Verify frontend build - quiet success, loud failure
# Usage: ./scripts/verify-frontend.sh

FRONTEND_DIR="$(dirname "$0")/../frontend"
cd "$FRONTEND_DIR" || {
    echo "FAIL: Cannot find frontend directory"
    echo "  - Expected at: $FRONTEND_DIR"
    exit 1
}

# Check node_modules exist
[ -d "node_modules" ] || {
    echo "FAIL: node_modules not found"
    echo "  - Run: cd frontend && npm install"
    exit 1
}

# TypeScript check - silent on success
npm run typecheck 2>&1 | grep -q "error TS" && {
    echo "FAIL: TypeScript errors found"
    echo "  - Run: cd frontend && npm run typecheck"
    npm run typecheck 2>&1 | grep "error TS" | head -5
    exit 1
}

# ESLint check - silent on success
npm run lint 2>&1 | grep -qE "error|Error" && {
    echo "FAIL: ESLint errors found"
    echo "  - Run: cd frontend && npm run lint"
    npm run lint 2>&1 | grep -E "error|Error" | head -5
    exit 1
}

# Build check - must produce dist folder
npm run build >/dev/null 2>&1 || {
    echo "FAIL: Build failed"
    echo "  - Run: cd frontend && npm run build"
    npm run build 2>&1 | tail -10
    exit 1
}

[ -d "dist" ] || {
    echo "FAIL: Build succeeded but dist folder not created"
    echo "  - Check vite.config.ts output settings"
    exit 1
}

# All checks passed - silent success
