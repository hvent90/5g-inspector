# NetPulse Dashboard

A network monitoring dashboard for T-Mobile home internet, built with Effect/TypeScript backend and React frontend.

## Quick Start

### Using Scripts (Recommended)

```bash
# Windows (PowerShell)
.\scripts\start-stack.ps1

# Linux/Mac/WSL
./scripts/start-stack.sh
```

### Manual Startup

**1. Start infrastructure (Grafana + PostgreSQL)**
```bash
bun run infra:up
```

**2. Run database migrations** (first time or after schema changes)
```bash
cd apps/api
bun run db:migrate
```

**3. Start the backend API** (in a separate terminal)
```bash
cd apps/api
bun run dev
```

**4. Start the frontend** (in a separate terminal)
```bash
cd apps/web
bun run dev --host  # --host exposes on LAN
```

## Services

| Service | Port | URL |
|---------|------|-----|
| Frontend | 5173 | http://localhost:5173 |
| Backend API | 3001 | http://localhost:3001 |
| Grafana | 3002 | http://localhost:3002 |
| PostgreSQL | 5433 | localhost:5433 |

**Grafana credentials:** admin / netpulse123

## Stopping the Stack

- Press `Ctrl+C` in each terminal
- Run `bun run infra:down` to stop containers

If processes get orphaned, use the kill scripts:
```bash
# Windows
.\scripts\kill-stack.ps1

# Linux/Mac/WSL
./scripts/kill-stack.sh
```

## Development

```bash
bun run dev        # Start infra + all apps
bun run dev:app    # Apps only (skip infra)
```

## Tech Stack

- **Backend:** Effect/TypeScript
- **Frontend:** React + Base UI
- **Database:** PostgreSQL
- **Visualization:** Grafana
- **Package Manager:** Bun
