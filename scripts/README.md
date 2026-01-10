# Verification Scripts

Following the "quiet success, loud failure" pattern from [context-efficient backpressure](https://www.humanlayer.dev/blog/context-efficient-backpressure).

## Pattern

- **Silent on success**: No output means everything passed
- **Detailed on failure**: Shows what failed, why, and how to fix

## Scripts

### verify-api.sh
Checks API server health:
- Server responds at port 8080
- `/api/signal` returns JSON
- `/api/db-stats` returns database info
- `/api/alerts/config` returns alerting config

```bash
./scripts/verify-api.sh       # default port 8080
./scripts/verify-api.sh 3000  # custom port
```

### verify-frontend.sh
Checks frontend build:
- node_modules exist
- TypeScript compiles without errors
- ESLint passes
- Vite build produces dist/

```bash
./scripts/verify-frontend.sh
```

### verify-integration.sh
Checks full-stack integration:
- API server running
- Frontend build exists
- Key API endpoints return expected data
- index.html serves properly

```bash
./scripts/verify-integration.sh       # default port 8080
./scripts/verify-integration.sh 3000  # custom port
```

### verify-all.sh
Runs all verification scripts:

```bash
./scripts/verify-all.sh       # default port 8080
./scripts/verify-all.sh 3000  # custom port
```

## Usage in CI/CD

```yaml
# Example GitHub Actions step
- name: Verify build
  run: ./scripts/verify-all.sh
```

## Usage in Development

Run before committing:
```bash
./scripts/verify-all.sh && git commit -m "..."
```

No output = safe to commit.
