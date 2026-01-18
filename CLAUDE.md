# Guidelines

- use bun over npm/npx when possible (should be always)
- monorepo
- create verifiable success/error feedback loops (ex: linter)
  - use oxfmt: https://oxc.rs/llms.txt
  - quiet success, loud failure: preserve context window by compressing success signals and expanding only on errors. See: https://www.humanlayer.dev/blog/context-efficient-backpressure
- use base ui: https://base-ui.com/llms.txt
- use Effect: https://effect.website/llms.txt
- During a refactor: Unless directly told otherwise, *do not keep backwards compatibility* when refactoring. Your priority is to refactor everything to work with the new design and remove legacy code.

## Browser Verification

Use the Claude Code Chrome extension to test and verify frontend changes directly in the browser: https://code.claude.com/docs/en/chrome.md

## Integration Verification

When implementing features that span multiple services (API + frontend, etc.), do not consider the feature complete until you've verified data flows end-to-end:

1. Start all required services
2. Make an actual request through the full stack
3. Verify the response matches expectations

**Use assertions that follow quiet success, loud failure:**

```bash
# Good: silent on success, shows error details on failure
curl -sf http://localhost:3001/health | jq -e '.status == "healthy"' > /dev/null || echo "FAIL: Backend not healthy. Is it running? Check: bun run dev in apps/api"

# Verify signal endpoint returns data
curl -sf http://localhost:3001/api/signal | jq -e '.nr_sinr' > /dev/null || echo "FAIL: Signal endpoint broken or gateway unreachable"
```

The ideal verification should follow the "quiet success, loud failure" intent.

## File Upload Verification

After implementing any multipart/file upload endpoint, you MUST verify with an actual upload before considering the feature complete:

1. Start the API: `bun run dev` in apps/api
2. Create a test file and upload via curl:
   ```bash
   # Create test image
   echo "test" > /tmp/test.jpg

   # Upload and verify - silent on success, error details on failure
   curl -sf -X POST http://localhost:3001/api/items/photo \
     -F "file=@/tmp/test.jpg;type=image/jpeg" \
     | grep -q '"id"' || echo "FAIL: Upload endpoint broken. Check schema matches payload structure."
   ```
3. Verify the response contains expected fields (id, filename, etc.)
4. If adding frontend, test from the actual form with a real file

**Common Effect multipart issues:**
- Effect's `Multipart.SingleFileSchema` expects `{fieldName: [PersistedFile]}`, not a raw array
- Always wrap in `Schema.Struct({ fieldName: Multipart.SingleFileSchema })`
- Handler must access `payload.fieldName`, not `payload` directly

## API Backend (Effect/TypeScript)

```bash
cd apps/api
bun run dev    # Run server (port 3001)
bun run build  # Build for production
bun run test   # Run tests
```

- Effect-based TypeScript backend
- Located at `apps/api/`

## Observability (LGTM Stack)

```bash
bun run infra:up    # Start stack (requires podman machine running)
bun run infra:down  # Stop stack
bun run dev         # Starts infra + all apps
bun run dev:app     # Apps only (skip infra)
```

| Service | Port | URL / Notes |
|---------|------|-------------|
| **Backend API** | 3001 | http://localhost:3001 |
| **Frontend** | 5173 | http://localhost:5173 (Vite dev) |
| **Grafana** | 3002 | http://localhost:3002 (admin/netpulse123) |
| **PostgreSQL** | 5432 | postgres://netpulse:netpulse_secret@localhost:5432/netpulse |

> **Note:** The stack uses PostgreSQL for data storage - Grafana's native datasource (no plugins needed). Prometheus, Alertmanager, Mimir, Loki, Tempo, and Gateway Exporter have been removed for simplicity.

**Database Migration:** Run `bun run db:migrate` in `apps/api/` after starting PostgreSQL to create tables.

**RCA Guide:** See `docs/rca-guide.md` for diagnosing network issues using SQL queries.

## Full Stack Scripts

### Start Stack (with LAN Access)

Start the entire stack (infra + backend + frontend) with a single command. Services are exposed on the local network so you can access dashboards from other devices.

```bash
# Windows (PowerShell)
.\scripts\start-stack.ps1

# Linux/Mac/WSL
./scripts/start-stack.sh
```

**Features:**
- Starts Podman/Docker container (Grafana)
- Starts Effect/TypeScript backend on port 3001
- Starts Vite frontend on port 5173 with `--host` for LAN access
- Prints local and LAN URLs on startup
- Graceful shutdown on Ctrl+C (stops all services)

**LAN Access:** After starting, access from other devices using your machine's IP:
- Frontend: `http://<your-ip>:5173`
- Backend API: `http://<your-ip>:3001`
- Grafana: `http://<your-ip>:3002`

### Kill Zombie Processes

If processes get orphaned (e.g., after a crash), use the kill scripts to clean up:

```bash
# Windows (PowerShell)
.\scripts\kill-stack.ps1

# Linux/Mac/WSL
./scripts/kill-stack.sh
```

**What it kills:**
- API backend processes (Bun/Node)
- Frontend processes (Vite)
- Stack container (grafana)

**Output includes:** Port status showing which ports are free vs still in use

---

## Current State

| Practice | Current State |
|----------|--------------|
| Package manager | Bun |
| Backend | Effect/TypeScript (apps/api) |
| Database | PostgreSQL (native Grafana datasource) |
| Frontend UI | Base UI |
| Linting | oxfmt (TS) |
| Infra commands | bun run infra:* |
| Monorepo | Full workspace |
