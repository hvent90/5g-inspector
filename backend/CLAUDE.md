# Backend Guidelines

## Quick Start

```bash
cd backend
uv run python -m tmobile_dashboard.main    # Run server (port 8080)
uv run uvicorn tmobile_dashboard.api:app --reload  # Dev with hot reload
uv run pytest                              # Run tests
uv run ruff check src                      # Lint
```

## Signal Metrics Glossary

Understanding these metrics is essential for this codebase:

| Metric | Name | Range | What It Means |
|--------|------|-------|---------------|
| **RSRP** | Reference Signal Received Power | -44 to -140 dBm | Signal strength. Higher (less negative) = stronger signal. |
| **SINR** | Signal to Interference Noise Ratio | -20 to +30 dB | Signal quality vs noise. Higher = cleaner signal. |
| **RSRQ** | Reference Signal Received Quality | -3 to -20 dB | Signal quality relative to interference. Higher = better. |
| **RSSI** | Received Signal Strength Indicator | -30 to -100 dBm | Total received power including noise. |

### Quality Thresholds

| Metric | Excellent | Good | Fair | Poor | Critical |
|--------|-----------|------|------|------|----------|
| **RSRP** | > -80 | -80 to -90 | -90 to -100 | -100 to -110 | < -110 |
| **SINR** | > 20 | 13 to 20 | 0 to 13 | -5 to 0 | < -5 |
| **RSRQ** | > -10 | -10 to -12 | -12 to -15 | -15 to -19 | < -19 |

### Cell Identifiers

| Term | Meaning |
|------|---------|
| **PCI** | Physical Cell ID - identifies the specific sector/antenna |
| **eNB** | eNodeB ID - identifies the LTE tower |
| **gNB** | gNodeB ID - identifies the 5G NR tower |
| **CID** | Cell ID - unique cell identifier |
| **Band** | Frequency band (n41=2.5GHz, n71=600MHz, B66=AWS, etc.) |

---

## API Endpoints Reference

### Health & Status

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Full health status with components |
| GET | `/health/live` | Kubernetes liveness probe |
| GET | `/health/ready` | Kubernetes readiness probe |
| GET | `/metrics` | Prometheus metrics |
| GET | `/api/stats` | Gateway polling statistics |
| GET | `/api/db-stats` | Database statistics |

### Signal Data

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/signal` | Current signal from gateway |
| GET | `/api/signal/raw` | Raw gateway JSON response |
| GET | `/api/history?minutes=60&resolution=1` | Historical signal data |
| GET | `/api/tower-history?hours=24` | Tower/cell change history |
| GET | `/api/advanced` | Health score, grade (A+ to F) |

### Congestion Analysis

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/congestion?days=7` | Full congestion summary |
| GET | `/api/congestion/heatmap` | Hour-of-day analysis |
| GET | `/api/congestion/daily?days=30` | Daily patterns |
| GET | `/api/congestion/peaks?top_n=10` | Top congested periods |
| GET | `/api/congestion/weekday-weekend` | Weekday vs weekend stats |
| POST | `/api/congestion/aggregate` | Trigger hourly aggregation |
| GET | `/api/congestion-proof` | FCC evidence report |

### Speed Testing

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/speedtest/status` | Check if test is running |
| GET | `/api/speedtest/tools` | Available tools and configuration |
| GET | `/api/speedtest/history?limit=100` | Test history |
| POST | `/api/speedtest?tool=X&server_id=Y` | Run speed test (blocking, 30-60s) |
| GET | `/api/speedtest?tool=X&server_id=Y` | Same as POST |

**Query Parameters** for `/api/speedtest`:

| Param | Description |
|-------|-------------|
| `tool` | Speedtest tool to use: `ookla-speedtest`, `speedtest-cli`, `fast-cli` |
| `server_id` | Ookla server ID to target (only works with ookla-speedtest) |

**Available Tools:**
- `ookla-speedtest`: Official Ookla CLI (most accurate, supports server targeting)
- `speedtest-cli`: Python speedtest-cli package
- `fast-cli`: Netflix fast.com (download only, represents real-world CDN performance)

### Alerts

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/alerts` | Active alerts |
| GET | `/api/alerts/history?limit=100` | Alert history |
| GET | `/api/alerts/config` | Current thresholds |
| PUT | `/api/alerts/config` | Update thresholds |
| POST | `/api/alerts/{id}/acknowledge` | Acknowledge alert |
| POST | `/api/alerts/{id}/clear` | Clear alert |
| POST | `/api/alerts/test` | Trigger test alert |
| GET | `/api/alerts/stream` | SSE real-time stream |

### Diagnostics

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/diagnostics?days=7` | Full diagnostic report |
| GET | `/api/diagnostics/signal-summary` | Signal statistics |
| GET | `/api/diagnostics/disruptions` | Disruption events |
| GET | `/api/diagnostics/time-patterns` | Time-of-day patterns |
| GET | `/api/diagnostics/tower-history` | Tower connection log |
| GET | `/api/diagnostics/export/json` | JSON export |
| GET | `/api/diagnostics/export/csv` | CSV export |

### Disruptions

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/disruption?hours=24` | Recent disruption events |
| GET | `/api/disruption/stats` | Disruption statistics |

### Service Terms & Support

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/service-terms` | Service terms documentation |
| PUT | `/api/service-terms` | Update terms |
| GET | `/api/support` | Support interaction list |
| POST | `/api/support` | Log new interaction |
| GET | `/api/support/fcc-export` | FCC-formatted export |

### Scheduler (Automated Speed Tests)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/scheduler/config` | Scheduler configuration |
| PUT | `/api/scheduler/config` | Update config |
| GET | `/api/scheduler/stats` | Scheduler statistics |
| POST | `/api/scheduler/start` | Start scheduler |
| POST | `/api/scheduler/stop` | Stop scheduler |
| POST | `/api/scheduler/trigger` | Manual trigger |
| GET | `/api/scheduler/history` | Scheduled test history |
| GET | `/api/scheduler/evidence` | FCC evidence summary |

**Scheduler Config Options** (via PUT `/api/scheduler/config`):

| Option | Default | Description |
|--------|---------|-------------|
| `enabled` | false | Enable/disable scheduler |
| `interval_minutes` | 30 | Time between tests (5-1440) |
| `start_hour` / `end_hour` | 0 / 24 | Testing window |
| `test_on_weekends` | true | Include weekends |
| `low_speed_threshold_mbps` | 10.0 | Alert threshold |
| `idle_hours` | [2,3,4,5] | Hours tagged as "baseline" context |
| `baseline_latency_ms` | 20.0 | Expected latency when network idle |
| `light_latency_multiplier` | 1.5 | Latency ratio for "light" context |
| `busy_latency_multiplier` | 2.5 | Latency ratio for "busy" context |
| `enable_latency_probe` | true | Run pre-test latency check |

**Network Context** helps distinguish clean baseline measurements from tests affected by local network usage (TV streaming, downloads). Filter in Grafana: `WHERE network_context = 'baseline'`

### Network Quality (Ping/Jitter/Loss)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/network-quality/config` | Monitor configuration |
| PUT | `/api/network-quality/config` | Update config |
| GET | `/api/network-quality/stats` | Monitor statistics |
| POST | `/api/network-quality/start` | Start monitoring |
| POST | `/api/network-quality/stop` | Stop monitoring |
| GET | `/api/network-quality/history` | Quality history |
| GET | `/api/network-quality/evidence` | FCC evidence summary |

### FCC Report

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/fcc-report?days=30` | Comprehensive complaint report |
| GET | `/api/fcc-readiness` | Evidence collection checklist |

---

## Configuration (Environment Variables)

All settings support `.env` file or environment variables with nested delimiter `__`.

### Gateway Settings (`GATEWAY_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `GATEWAY_HOST` | 192.168.12.1 | Gateway IP address |
| `GATEWAY_PORT` | 80 | Gateway HTTP port |
| `GATEWAY_POLL_INTERVAL_MS` | 200 | Polling interval in ms |
| `GATEWAY_TIMEOUT_SECONDS` | 2.0 | Request timeout |
| `GATEWAY_FAILURE_THRESHOLD` | 3 | Circuit breaker failures before open |
| `GATEWAY_RECOVERY_TIMEOUT_SECONDS` | 30 | Circuit breaker recovery time |

### Database Settings (`DB_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | signal_history.db | SQLite database path |
| `DB_BATCH_INTERVAL_SECONDS` | 5 | Flush interval |
| `DB_RETENTION_DAYS` | 30 | Data retention period |
| `DB_WAL_MODE` | true | Enable WAL mode |
| `DB_POOL_SIZE` | 5 | Connection pool size |

### Server Settings (`SERVER_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVER_HOST` | 0.0.0.0 | Bind address |
| `SERVER_PORT` | 8080 | HTTP port |
| `SERVER_WORKERS` | 1 | Uvicorn workers |

### Disruption Detection (`DISRUPTION_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `DISRUPTION_SINR_DROP_5G` | 10.0 | SINR drop threshold (5G) |
| `DISRUPTION_SINR_DROP_4G` | 8.0 | SINR drop threshold (4G) |
| `DISRUPTION_RSRP_DROP_5G` | 10.0 | RSRP drop threshold (5G) |
| `DISRUPTION_SINR_CRITICAL_5G` | 0.0 | Critical SINR (5G) |
| `DISRUPTION_RSRP_CRITICAL_5G` | -110.0 | Critical RSRP (5G) |
| `DISRUPTION_DETECTION_WINDOW_SECONDS` | 30 | Detection window |

### Alert Settings (`ALERT_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `ALERT_ENABLED` | true | Enable alerting |
| `ALERT_MAX_HISTORY_SIZE` | 1000 | Max stored alerts |

### Logging Settings (`LOG_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | INFO | DEBUG, INFO, WARNING, ERROR |
| `LOG_FORMAT` | json | json or console |
| `LOG_CORRELATION_ID` | true | Enable request correlation |

### Speedtest Settings (`SPEEDTEST_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `SPEEDTEST_PREFERRED_TOOLS` | `["fast-cli", "speedtest-cli", "ookla-speedtest"]` | Tool preference order (fast-cli preferred for real-world speeds) |
| `SPEEDTEST_OOKLA_SERVER_ID` | (none) | Default Ookla server ID to target |
| `SPEEDTEST_TIMEOUT_SECONDS` | 120 | Timeout for speedtest execution |

---

## Database Schema

### Tables

**signal_history** - Raw signal samples (200ms interval)
```sql
id, timestamp, timestamp_unix,
nr_sinr, nr_rsrp, nr_rsrq, nr_rssi, nr_bands, nr_gnb_id, nr_cid,
lte_sinr, lte_rsrp, lte_rsrq, lte_rssi, lte_bands, lte_enb_id, lte_cid,
registration_status, device_uptime
```

**disruption_events** - Detected signal disruptions
```sql
id, timestamp, timestamp_unix,
event_type, severity, description,
before_state, after_state, duration_seconds,
resolved, resolved_at
```

**speedtest_results** - Speed test results with signal snapshot, network context, and tool info
```sql
id, timestamp, timestamp_unix,
download_mbps, upload_mbps, ping_ms, jitter_ms, packet_loss_percent,
server_name, server_location, server_host, server_id,
client_ip, isp, tool, result_url,
signal_snapshot, status, error_message, triggered_by,
network_context, pre_test_latency_ms
```

- `tool`: Which speedtest tool was used (`ookla-speedtest`, `speedtest-cli`, `fast-cli`, `unknown`)
- `server_id`: Ookla server ID (for server targeting)
- `result_url`: Shareable result URL (Ookla only)
- Network context values: `baseline` (idle hours), `idle` (low latency), `light` (moderate latency), `busy` (high latency), `unknown`

Filter by tool in Grafana: `WHERE tool = 'ookla-speedtest'`

**network_quality** - Ping/jitter/loss measurements
```sql
id, timestamp, timestamp_unix,
ping_ms, jitter_ms, packet_loss_percent,
target_host, packet_count, signal_snapshot
```

**hourly_metrics** - Aggregated hourly data for congestion analysis
```sql
id, date, hour, day_of_week, is_weekend,
nr_sinr_avg/min/max, nr_rsrp_avg/min/max, nr_rsrq_avg,
lte_sinr_avg/min/max, lte_rsrp_avg/min/max, lte_rsrq_avg,
congestion_score, sample_count
```

---

## Architecture

```
FastAPI App (api.py)
    ├── Middleware (CORS, RequestLogging, CorrelationID)
    ├── Lifespan (services/lifespan.py) - AppState initialization
    │
    └── Services Layer
        ├── GatewayService - Polls gateway, circuit breaker
        ├── SignalRepository - Database writes/reads
        ├── DisruptionService - Detects signal drops
        ├── AlertService - Manages alerts, SSE streaming
        ├── SpeedtestService - Runs speed tests
        ├── SchedulerService - Automated test scheduling
        ├── NetworkQualityService - Ping/jitter monitoring
        ├── CongestionService - Congestion analysis
        ├── DiagnosticsService - Report generation
        └── ServiceTermsService - Documentation storage
```

---

## Metrics & Observability

### Never Return Cached Data on Failure

When collecting metrics from external sources (gateways, APIs, devices), **do not return stale cached values** when the source is unreachable:

```python
# WRONG: Returns stale data, hides the outage
async def poll_once(self):
    if not self.circuit_breaker.can_execute():
        return self._current_data  # Stale!
    try:
        data = await self._fetch_data()
        self._current_data = data
        return data
    except Exception:
        return self._current_data  # Stale!

# CORRECT: Return None or raise, let caller handle absence
async def poll_once(self):
    if not self.circuit_breaker.can_execute():
        return None  # Explicit "no data"
    try:
        data = await self._fetch_data()
        self._current_data = data
        self._last_success = time.time()
        return data
    except Exception:
        return None  # Explicit "no data"
```

### Track When Data Was Last Valid

Always maintain timestamps for data freshness:

```python
class GatewayService:
    def __init__(self):
        self._current_data: SignalData | None = None
        self._last_success: float = 0  # Track when we last got real data

    @property
    def seconds_since_success(self) -> float:
        if self._last_success == 0:
            return float('inf')
        return time.time() - self._last_success

    @property
    def is_data_stale(self) -> bool:
        return self.seconds_since_success > 30  # Define your threshold
```

### Prometheus Metrics: Use NaN for Missing Data

When exposing Prometheus metrics, set gauges to NaN when data is unavailable:

```python
from prometheus_client import Gauge

signal_rsrp = Gauge('signal_rsrp_dbm', 'Signal strength')

def update_metrics(data: SignalData | None):
    if data is None:
        signal_rsrp.set(float('nan'))  # Creates gaps in Grafana
    else:
        signal_rsrp.set(data.rsrp)
```

### Expose Staleness as Metrics

Add dedicated metrics for monitoring data freshness:

```python
last_success_timestamp = Gauge('service_last_success_timestamp',
    'Unix timestamp of last successful data fetch')
seconds_since_success = Gauge('service_seconds_since_success',
    'Seconds since last successful data fetch')

def update_staleness_metrics():
    if self._last_success > 0:
        last_success_timestamp.set(self._last_success)
        seconds_since_success.set(time.time() - self._last_success)
```

## Circuit Breakers

Circuit breakers prevent hammering failed services, but **must not mask data staleness**:

```python
class CircuitBreaker:
    # When circuit is OPEN, the service knows requests will fail
    # But consumers must still know data is stale!

    def can_execute(self) -> bool:
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                return True
            return False  # Don't attempt, but caller must handle staleness
        return True
```

## Core Principle

**The absence of data is itself critical information.**

When a data source becomes unavailable:
1. Return `None` or raise an exception - don't silently return cached data
2. Set metric gauges to NaN so dashboards show gaps
3. Track and expose staleness duration as its own metric
4. Let the UI/alerting layer decide how to present "no data"

Hiding failures behind cached data creates false confidence and delays incident detection.
