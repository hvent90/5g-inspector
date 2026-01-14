# Architecture Simplification Plan

## Overview

This document outlines the plan to significantly reduce complexity in the NetPulse Dashboard while preserving all core functionality. The primary goals are:

1. **Rebrand to carrier-agnostic name** (e.g., "SignalPulse")
2. **Reduce container count** from 7 to 1 (Grafana only)
3. **Unify the backend** to a single TypeScript/Effect codebase
4. **Simplify the API surface** from 74 endpoints to ~15
5. **Leverage SQLite directly** for Grafana visualization

---

## Rebranding

The project will be renamed from "NetPulse Dashboard" to a carrier-agnostic name. This enables:
- Use with any 5G home internet provider
- Public release without trademark concerns
- Broader community adoption

**Chosen name:** `NetPulse`

**Naming changes:**
| Current | New |
|---------|-----|
| `netpulse` | `netpulse` |
| `netpulse` (Python module) | N/A (removed - TypeScript only) |
| `netpulse-grafana` | `netpulse-grafana` |
| `NetPulse*` alert names | `NetPulse*` alert names |
| `NETPULSE_*` env vars | `NETPULSE_*` or `NP_*` |

---

## Current vs Target Architecture

### Previous State (Over-engineered)

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend (React/Vite) ─────────────────────────────────────│
│  Port 5173                                                   │
├─────────────────────────────────────────────────────────────┤
│  Backend (Python/FastAPI) [REMOVED] ────────────────────────│
│  Port 8080 │ 74 endpoints │ 30+ Python files                │
├─────────────────────────────────────────────────────────────┤
│  Observability Stack (7 containers)                          │
│  ┌─────────────┬─────────────┬─────────────┬──────────────┐ │
│  │ Grafana     │ Prometheus  │ Alertmanager│ Mimir        │ │
│  │ :3002       │ :9090       │ :9093       │ :9009        │ │
│  ├─────────────┼─────────────┼─────────────┼──────────────┤ │
│  │ Loki        │ Tempo       │ Gateway     │              │ │
│  │ :3100       │ :3200       │ Exporter    │              │ │
│  │             │             │ :9100       │              │ │
│  └─────────────┴─────────────┴─────────────┴──────────────┘ │
├─────────────────────────────────────────────────────────────┤
│  SQLite (signal_history.db)                                  │
└─────────────────────────────────────────────────────────────┘
```

**Problems:**
- 7 containers for a single-user home monitoring tool
- Prometheus + Mimir duplicate what SQLite already stores
- Gateway Exporter duplicates backend's gateway polling
- Two languages (Python + TypeScript) to maintain
- 74 API endpoints (excessive surface area)
- Alertmanager duplicates backend's AlertService

### Target State (Simplified)

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend (React/Vite) ─────────────────────────────────────│
│  Port 5173 │ Controls + At-a-glance view                    │
├─────────────────────────────────────────────────────────────┤
│  Backend (TypeScript/Effect) ───────────────────────────────│
│  Port 8080 │ ~15 endpoints │ Single language                │
├─────────────────────────────────────────────────────────────┤
│  Grafana (1 container) ─────────────────────────────────────│
│  Port 3002 │ SQLite datasource │ All dashboards             │
├─────────────────────────────────────────────────────────────┤
│  SQLite (signal_history.db)                                  │
└─────────────────────────────────────────────────────────────┘
```

**Benefits:**
- 1 container instead of 7
- Single language (TypeScript) for entire stack
- ~15 endpoints instead of 74
- Grafana queries SQLite directly (no Prometheus middleman)
- Simpler deployment, fewer moving parts

---

## What We're Removing

| Component | Why It's Redundant |
|-----------|-------------------|
| **Prometheus** | SQLite already stores time-series signal data |
| **Mimir** | SQLite is the long-term store (30+ days retention) |
| **Alertmanager** | Backend AlertService handles alerts + SSE streaming |
| **Loki** | Nice-to-have logging, not essential for core function |
| **Tempo** | Distributed tracing is overkill for single-user app |
| **Gateway Exporter** | Backend already polls gateway every 200ms |

## What We're Keeping

| Component | Why |
|-----------|-----|
| **Grafana** | Powerful visualization, ad-hoc queries, dashboards |
| **SQLite** | Already the source of truth, works well |
| **React Frontend** | Quick controls, at-a-glance view |
| **Core Backend Services** | Gateway polling, speedtest, alerts, FCC reports |

---

## Implementation Phases

### Phase 1: Grafana + SQLite Integration

**Goal:** Prove Grafana can replace Prometheus by querying SQLite directly.

#### Tasks:

1. **Install SQLite datasource plugin in Grafana**
   - Add `frser-sqlite-datasource` to Grafana container
   - Mount `signal_history.db` as read-only volume (already done in docker-compose)

2. **Create SQLite-based dashboard for signal metrics**
   - Query `signal_history` table for RSRP, SINR, RSRQ, bands
   - Time-series visualization with auto-refresh (5-10s)
   - Verify it matches current Prometheus-based dashboards

3. **Create SQLite-based dashboard for speedtest results**
   - Query `speedtest_results` table
   - Show download/upload trends, latency, packet loss

4. **Create SQLite-based dashboard for disruptions**
   - Query `disruption_events` table
   - Annotations on signal graphs for tower changes

5. **Migrate alerting rules to Grafana**
   - Convert Prometheus alert rules to Grafana alert rules
   - Use SQLite queries for threshold detection
   - Configure notification channels (optional)

#### Success Criteria:
- All current dashboards recreated with SQLite datasource
- Dashboard refresh shows "live" data (5-10s delay acceptable)
- Alerts fire correctly based on SQLite queries

---

### Phase 2: Remove Redundant Containers

**Goal:** Eliminate Prometheus, Mimir, Alertmanager, Loki, Tempo, Gateway Exporter.

#### Tasks:

1. **Update docker-compose.yml**
   - Remove prometheus, mimir, alertmanager, loki, tempo, gateway-exporter services
   - Remove associated volumes
   - Update Grafana depends_on (remove dependencies)

2. **Remove Prometheus datasource from Grafana**
   - Update provisioning to remove Prometheus datasource
   - Ensure all dashboards use SQLite only

3. **Delete exporter directory** (DONE)
   - ~~Remove `infra/exporter/` (Gateway Exporter code)~~

4. **Delete unused config directories** (DONE)
   - ~~Remove `infra/prometheus/`~~
   - ~~Remove `infra/alertmanager/`~~
   - ~~Remove `infra/mimir/`~~
   - ~~Remove `infra/loki/`~~
   - ~~Remove `infra/tempo/`~~

5. **Update documentation**
   - Update CLAUDE.md port table
   - Update infra/CLAUDE.md
   - Remove references to removed services

6. **Update startup scripts**
   - Simplify `scripts/start-stack.ps1` and `scripts/start-stack.sh`
   - Update `bun run infra:up` command

#### Success Criteria:
- `bun run infra:up` starts only Grafana
- All dashboards still functional
- No references to removed services in codebase

---

### Phase 3: Backend Migration to TypeScript/Effect

**Goal:** Replace Python/FastAPI backend with TypeScript/Effect.

#### Tasks:

1. **Create Effect-based API structure**
   - Set up `apps/api/` with Effect HTTP server
   - Define schema types matching current Python models

2. **Migrate core services (priority order):**

   a. **GatewayService** - Gateway polling (200ms interval)
      - HTTP client to poll `192.168.12.1`
      - Parse 5G/LTE signal response
      - Write to SQLite

   b. **SignalRepository** - Database layer
      - SQLite connection with better-sqlite3 or effect-sql
      - CRUD for signal_history, speedtest_results, etc.

   c. **DisruptionService** - Event detection
      - Monitor signal changes for disruptions
      - Tower change detection
      - Band switch detection

   d. **AlertService** - Alerting
      - Threshold-based alerts
      - SSE streaming for real-time updates

   e. **SpeedtestService** - Speed testing
      - Shell out to speedtest CLI (pick one tool, recommend Ookla)
      - Parse results, store in DB

   f. **DiagnosticsService** - FCC reports
      - Generate diagnostic reports
      - FCC complaint evidence generation

3. **Consolidate API endpoints**
   - Target ~15 endpoints (see API Consolidation section below)

4. **Remove Python backend** (DONE)
   - ~~Delete `backend/` directory~~
   - ~~Update root package.json scripts~~
   - ~~Update CLAUDE.md~~

#### Success Criteria:
- All functionality works with TypeScript backend
- Single `bun run dev` starts everything
- Python completely removed from codebase (DONE)

---

### Phase 4: Frontend Simplification

**Goal:** Streamline frontend to complement Grafana, not duplicate it.

#### Tasks:

1. **Define frontend scope**
   - Quick actions: Run speedtest, generate report
   - At-a-glance: Current signal quality (simple gauge)
   - Link to Grafana for detailed analysis

2. **Remove redundant visualization**
   - If Chart.js duplicates Grafana dashboards, remove it
   - Keep only what Grafana can't do (interactive controls)

3. **Consider Grafana embedding**
   - Embed Grafana panels in React app (optional)
   - Or link out to Grafana for all visualization

4. **Consolidate packages/shared**
   - If backend is now TypeScript, shared types can live in apps/api
   - Evaluate if separate package is still needed

#### Success Criteria:
- Frontend is focused on controls, not visualization
- No duplicate charts between frontend and Grafana
- Clean separation of concerns

---

## API Consolidation

### Current: 74 Endpoints

Too many endpoints create maintenance burden and complexity.

### Target: ~15 Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/signal` | GET | Current signal metrics |
| `/api/signal/history` | GET | Historical signal data (with time range params) |
| `/api/speedtest` | POST | Run a speed test |
| `/api/speedtest` | GET | Get speedtest results (with time range params) |
| `/api/disruptions` | GET | Get disruption events |
| `/api/alerts` | GET | Get current alerts |
| `/api/alerts/stream` | GET | SSE stream for real-time alerts |
| `/api/alerts/config` | GET/PUT | Alert thresholds configuration |
| `/api/tower/history` | GET | Tower connection history |
| `/api/report/diagnostic` | POST | Generate diagnostic report |
| `/api/report/fcc` | POST | Generate FCC complaint report |
| `/api/fcc/readiness` | GET | FCC evidence collection status |
| `/api/health` | GET | Health check |
| `/api/config` | GET/PUT | Application configuration |

### Endpoints to Remove/Merge

Many current endpoints can be consolidated:
- Multiple history endpoints → single `/signal/history` with params
- Scheduler endpoints → merge into speedtest or remove (use cron)
- Congestion endpoints → derive from signal history in Grafana
- Network quality → merge into signal or remove (Grafana can query)
- Support/service terms → evaluate if needed, likely remove

---

## Migration Strategy

### Parallel Operation Period

During Phase 3 (backend migration):
1. Run both Python and TypeScript backends
2. Frontend proxies to TypeScript backend
3. Verify feature parity with integration tests
4. Cut over once all tests pass

### Database Compatibility

- SQLite schema remains unchanged
- Both backends read/write same database
- No data migration needed

### Rollback Plan

- Keep Python backend in git history
- Docker-compose can be reverted
- Grafana dashboards are versioned in provisioning/

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Grafana SQLite performance | Test with 30 days of data, optimize indexes if needed |
| Missing Prometheus features | Document any PromQL queries that need SQL equivalents |
| Effect learning curve | Start with simple services, add complexity gradually |
| Data loss during migration | SQLite is file-based, easy to backup/restore |

---

## Success Metrics

After full implementation:

| Metric | Before | After |
|--------|--------|-------|
| Container count | 7 | 1 |
| Languages | 2 (Python + TS) | 1 (TypeScript) |
| API endpoints | 74 | ~15 |
| Backend files | 30+ Python | ~10 TypeScript |
| Startup time | ~30s (all containers) | ~5s (Grafana only) |
| Memory usage | ~2GB (all containers) | ~200MB (Grafana) |

---

## Task Breakdown Summary

| Phase | Tasks | Estimated Complexity |
|-------|-------|---------------------|
| Phase 0: Rebranding | 5 tasks | Low |
| Phase 1: Grafana + SQLite | 5 tasks | Low |
| Phase 2: Remove Containers | 5 tasks | Low |
| Phase 3: Backend Migration | 9 tasks | High |
| Phase 4: Frontend Simplification | 4 tasks | Medium |
| Phase 5: CSS Styling | 4 tasks | Low |

**Recommended order:** Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5

Phase 0 establishes the new brand. Phases 1-2 provide immediate value (fewer containers). Phase 3 is the bulk of the work. Phases 4-5 are polish.

---

## Phase 5: CSS Styling (90s Monotone Aesthetic)

Replace the magenta carrier-specific branding with a clean 90s monotone aesthetic.

**Color palette:**
```css
:root {
  --bg: #ffffff;           /* White background */
  --surface: #f0f0f0;      /* Light gray */
  --border: #c0c0c0;       /* Windows gray */
  --text: #000000;         /* Black text */
  --text-dim: #666666;     /* Dark gray */
  --accent: #000000;       /* Black accent (was magenta) */
  --good: #008000;         /* Classic green */
  --ok: #808000;           /* Olive */
  --bad: #ff0000;          /* Pure red */
}
```

**Additional changes:**
- Remove or simplify pulsing logo animation
- Add subtle inset/outset borders for Windows 95 feel
- Keep IBM Plex Mono font (retro appropriate)
