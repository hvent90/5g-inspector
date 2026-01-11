# Guidelines

- use bun over npm/npx when possible (should be always)
- use uv over pip/venv for Python
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
curl -sf http://localhost:8080/health | jq -e '.status == "healthy"' > /dev/null || echo "FAIL: Backend not healthy. Is it running? Check: uv run python -m tmobile_dashboard.main"

# Verify signal endpoint returns data
curl -sf http://localhost:8080/api/signal | jq -e '.nr_sinr' > /dev/null || echo "FAIL: Signal endpoint broken or gateway unreachable"
```

The ideal verification should follow the "quiet success, loud failure" intent.

## File Upload Verification (for Effect/TypeScript backend)

> **Note:** This applies to the target Effect/TypeScript backend, not the current Python backend.

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

## Python Backend

```bash
cd backend
uv run python -m tmobile_dashboard.main    # Run server (port 8080)
uv run uvicorn tmobile_dashboard.api:app --reload  # Dev with hot reload
uv run pytest                              # Run tests
uv run ruff check src                      # Lint
```

- No `__init__.py` files needed (implicit namespace packages, PEP 420)
- Dependencies managed via `pyproject.toml`
- `uv run` auto-creates venv and installs deps on first run

## Observability (LGTM Stack)

```bash
bun run infra:up    # Start stack (requires podman machine running)
bun run infra:down  # Stop stack
bun run dev         # Starts infra + all apps
bun run dev:app     # Apps only (skip infra)
```

| Service | Port | URL / Notes |
|---------|------|-------------|
| **Backend API** | 8080 | http://localhost:8080 |
| **Frontend** | 5173 | http://localhost:5173 (Vite dev) |
| **Grafana** | 3002 | http://localhost:3002 (admin/tmobile123) |
| **Prometheus** | 9090 | http://localhost:9090 |
| **Alertmanager** | 9093 | http://localhost:9093 |
| **Mimir** | 9009 | Long-term metrics storage |
| **Loki** | 3100 | Log aggregation |
| **Tempo** | 3200 | Tracing UI |
| **Gateway Exporter** | 9100 | T-Mobile gateway metrics |

**OTLP endpoints:** `localhost:4317` (gRPC) or `localhost:4318` (HTTP)

**RCA Guide:** See `docs/rca-guide.md` for diagnosing network issues using SQLite and Loki queries.

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
- Starts Podman/Docker containers (Grafana, Prometheus, etc.)
- Starts Python backend on port 8080
- Starts Vite frontend on port 5173 with `--host` for LAN access
- Prints local and LAN URLs on startup
- Graceful shutdown on Ctrl+C (stops all services)

**LAN Access:** After starting, access from other devices using your machine's IP:
- Frontend: `http://<your-ip>:5173`
- Backend API: `http://<your-ip>:8080`
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
- Python backend processes (`tmobile_dashboard`, `uvicorn`)
- Node/Bun processes (Vite frontend)
- Stack containers (grafana, prometheus, alertmanager, etc.)

**Output includes:** Port status showing which ports are free vs still in use

---

## Current State & Parity Tasks

> **Note:** This project is migrating toward the practices above. Current state:

| Practice | Current State | Target |
|----------|--------------|--------|
| Package manager | Bun (TS), uv (Python) | Bun |
| Backend | Python/FastAPI | Effect/TypeScript |
| Frontend UI | Base UI (partial) | Base UI |
| Linting | ruff (Python), eslint (TS) | oxfmt |
| Infra commands | `infra/docker-compose.yml` | bun run infra:* |
| Monorepo | Partial (frontend/backend dirs) | Full workspace |
