# Observability Gaps Analysis

## Executive Summary

This document identifies critical gaps in the T-Mobile Dashboard's logging and observability infrastructure that prevent effective diagnosis of recurring network outages. The current system captures high-level events but lacks the granularity needed to detect patterns like "20-second outages every 2 minutes."

## Current Observability Stack

### Data Sources

| Source | Location | Data Type | Query Method |
|--------|----------|-----------|--------------|
| SQLite: `signal_history` | `backend/signal_history.db` | Signal metrics (SINR, RSRP, etc.) | SQL |
| SQLite: `disruption_events` | `backend/signal_history.db` | Gateway outages | SQL |
| SQLite: `network_quality` | `backend/signal_history.db` | Ping/jitter/packet loss | SQL |
| SQLite: `speedtest_results` | `backend/signal_history.db` | Speed tests | SQL |
| Loki | `localhost:3100` | Structured events | LogQL |
| Prometheus | `localhost:9090` | Metrics | PromQL |
| Console (structlog) | stdout | All backend logs | Not persisted |

### Data Collection Frequency

| Data Type | Current Frequency | Adequate for Diagnosis |
|-----------|-------------------|------------------------|
| Signal polling | 200ms | Yes |
| Network quality (ping) | 5-15 minutes | **No** - too sparse |
| Speedtest | Manual/scheduled | N/A |
| Gateway errors | On occurrence | **Partial** - not persisted to Loki |

## Critical Gaps

### Gap 1: Gateway Errors Not Persisted to Loki

**Problem**: The following events are logged to console via structlog but NOT pushed to Loki:

```python
# These events only go to stdout, not Loki:
log.warning("gateway_poll_error", error=message, error_count=self._error_count)
log.warning("circuit_breaker_opened", failures=..., recovery_timeout=...)
log.warning("gateway_outage_started", start_time=..., error_count=...)
log.info("gateway_outage_resolved", duration_seconds=..., error_count=...)
```

**Impact**: Cannot query historical gateway failures. When investigating outages, only `disruption_events` table is available, which only captures circuit-breaker-level events (3+ consecutive failures).

**Current Loki event types**:
```
event_type: ["network_quality", "speedtest"]  # Missing: gateway_error, outage
```

**Recommendation**: Add Loki push for gateway events in `services/gateway.py`.

### Gap 2: Network Quality Monitoring Too Infrequent

**Problem**: Network quality tests (ping, jitter, packet loss) run every 5-15 minutes based on `scheduler_config.json`:

```json
{
  "interval_minutes": 15,
  "min_interval_minutes": 5
}
```

**Impact**: Cannot detect "20-second outages every 2 minutes" - the monitoring window is 2.5-7.5x larger than the pattern interval.

**Recommendation**: Add continuous lightweight ping monitoring (every 30 seconds) separate from the full network quality test.

### Gap 3: Individual Poll Failures Not Tracked

**Problem**: The `disruption_events` table only records when the circuit breaker opens (after 3 consecutive failures). Individual failures are not persisted.

**Current behavior**:
- 1 failure: logged to console, not persisted
- 2 failures: logged to console, not persisted
- 3 failures: circuit breaker opens, `disruption_events` record created

**Impact**: Intermittent single failures or patterns of 1-2 failures are invisible in historical data.

**Recommendation**: Create `gateway_poll_events` table to track every failure with timestamp.

### Gap 4: No Signal Quality Drop Detection

**Problem**: While signal metrics are stored every 200ms, there's no detection/alerting for sudden signal drops that don't trigger a full gateway outage.

**Example scenario**:
- SINR drops from 12 to 2 for 20 seconds
- Gateway remains reachable (no circuit breaker)
- User experiences degraded connectivity
- No event is logged

**Recommendation**: Add threshold-based signal quality event detection.

## Data Schema Gaps

### Missing Tables

```sql
-- Proposed: gateway_poll_events
CREATE TABLE gateway_poll_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    timestamp_unix REAL NOT NULL,
    success INTEGER NOT NULL,
    error_type TEXT,           -- 'timeout', 'connection_refused', 'http_error', etc.
    error_message TEXT,
    response_time_ms REAL,
    circuit_state TEXT         -- 'closed', 'open', 'half_open'
);

-- Proposed: signal_quality_events
CREATE TABLE signal_quality_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    timestamp_unix REAL NOT NULL,
    event_type TEXT NOT NULL,  -- 'sinr_drop', 'rsrp_drop', 'band_change', 'tower_change'
    network TEXT NOT NULL,     -- '5g', '4g'
    before_value REAL,
    after_value REAL,
    duration_seconds REAL
);
```

### Missing Loki Event Types

```python
# Events that should be pushed to Loki:
event_types_needed = [
    "gateway_poll_error",      # Every failed poll
    "gateway_poll_timeout",    # Specific timeout events
    "gateway_outage_start",    # Circuit breaker opened
    "gateway_outage_end",      # Circuit breaker recovered
    "signal_quality_drop",     # SINR/RSRP dropped below threshold
    "signal_quality_recovery", # SINR/RSRP recovered
    "tower_handoff",           # Changed cell tower
    "band_change",             # Changed frequency band
]
```

## Query Limitations

### What I Can Query Now

```sql
-- Find gaps in signal data (indirect outage detection)
WITH gaps AS (
  SELECT
    timestamp,
    timestamp_unix - LAG(timestamp_unix) OVER (ORDER BY timestamp_unix) as gap_seconds
  FROM signal_history
)
SELECT * FROM gaps WHERE gap_seconds > 5;

-- Find recorded disruption events
SELECT * FROM disruption_events ORDER BY timestamp_unix DESC;

-- Find packet loss events (but only every 5-15 min)
SELECT * FROM network_quality WHERE packet_loss_percent > 0;
```

### What I Cannot Query

```sql
-- CANNOT: Find individual gateway poll failures
-- CANNOT: Find signal quality drops without full outage
-- CANNOT: Correlate gateway errors with signal metrics
-- CANNOT: Find patterns in failures (e.g., "every 2 minutes")
```

### LogQL Limitations

```logql
-- CAN query:
{job="tmobile-dashboard", event_type="network_quality"}

-- CANNOT query (events not in Loki):
{job="tmobile-dashboard", event_type="gateway_error"}
{job="tmobile-dashboard", event_type="outage"}
```

## Recommended Improvements

### Priority 1: Push Gateway Events to Loki

**File**: `backend/src/tmobile_dashboard/services/gateway.py`

Add Loki push after each error:

```python
async def _handle_error(self, message: str) -> None:
    # Existing code...

    # NEW: Push to Loki
    await self._push_to_loki({
        "event": "gateway_poll_error",
        "error": message,
        "error_count": self._error_count,
        "circuit_state": self.circuit_breaker.state.value,
    })
```

### Priority 2: Add Continuous Ping Monitor

**New file**: `backend/src/tmobile_dashboard/services/ping_monitor.py`

```python
class ContinuousPingMonitor:
    """Lightweight ping monitor running every 30 seconds."""

    def __init__(self):
        self.interval = 30  # seconds
        self.targets = ["8.8.8.8", "1.1.1.1", "208.54.0.1"]

    async def ping_once(self, target: str) -> PingResult:
        # Single ping with 2-second timeout
        ...
```

### Priority 3: Add Signal Quality Event Detection

**File**: `backend/src/tmobile_dashboard/services/gateway.py`

Add threshold detection in `poll_once()`:

```python
# Detect significant signal drops
if prev_sinr and current_sinr:
    drop = prev_sinr - current_sinr
    if drop > 5:  # 5dB drop threshold
        await self._emit_signal_event("sinr_drop", prev_sinr, current_sinr)
```

### Priority 4: Persist Individual Poll Failures

**File**: `backend/src/tmobile_dashboard/db/database.py`

Add new table and insert on each failure:

```python
async def record_poll_failure(
    self,
    error_type: str,
    error_message: str,
    circuit_state: str
) -> None:
    await self.execute(
        "INSERT INTO gateway_poll_events (...) VALUES (...)",
        ...
    )
```

## Success Criteria

After implementing these improvements:

| Requirement | Current | Target |
|-------------|---------|--------|
| Query gateway failures in Loki | No | Yes |
| Detect 20s outages in 2min window | No | Yes |
| Track individual poll failures | No | Yes |
| Correlate signal drops with outages | Partial | Yes |
| Historical failure pattern analysis | No | Yes |

## Implementation Effort

| Improvement | Effort | Impact |
|-------------|--------|--------|
| Loki gateway events | 2-4 hours | High |
| Continuous ping monitor | 4-6 hours | High |
| Signal quality detection | 2-3 hours | Medium |
| Poll failure persistence | 2-3 hours | Medium |
| **Total** | **10-16 hours** | |

## Conclusion

The current observability stack is insufficient for diagnosing recurring network outages. The 5-15 minute network quality interval cannot catch 2-minute patterns, and gateway errors are not persisted to queryable storage. Implementing the recommended improvements will provide the visibility needed to diagnose issues like "20-second outages every 2 minutes."

### Immediate Actions

1. Add Loki push for gateway error events
2. Reduce network quality monitoring interval to 30 seconds
3. Create `gateway_poll_events` table for failure tracking
4. Add signal quality drop detection with configurable thresholds
