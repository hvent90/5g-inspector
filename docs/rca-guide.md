# Root Cause Analysis Guide for Claude Code

This guide helps Claude Code diagnose network issues using the T-Mobile Dashboard's observability stack.

## Quick Reference

### Data Sources

| Source | Location | Query Method | Best For |
|--------|----------|--------------|----------|
| `signal_history` | SQLite | SQL | Signal metrics over time |
| `disruption_events` | SQLite | SQL | Circuit breaker events, tower changes |
| `gateway_poll_events` | SQLite | SQL | Individual poll failures (no retention limit) |
| `continuous_ping` | SQLite | SQL | 30-second connectivity checks |
| `network_quality` | SQLite | SQL | Packet loss, jitter (5-15 min intervals) |
| `speedtest_results` | SQLite | SQL | Speed test history with signal snapshots |
| Loki | `localhost:3100` | LogQL | Real-time events, Grafana dashboards |
| Prometheus | `localhost:9090` | PromQL | Metrics, alerting |

### Database Path

```bash
backend/signal_history.db
```

---

## RCA Workflow

### Step 1: Identify the Symptom

Ask the user to describe the issue:
- "Internet drops for ~20 seconds every few minutes"
- "Speeds are slow during evening hours"
- "Connection unstable when it rains"

### Step 2: Determine Time Window

```sql
-- Find the time range of available data
SELECT
  datetime(MIN(timestamp_unix), 'unixepoch', 'localtime') as earliest,
  datetime(MAX(timestamp_unix), 'unixepoch', 'localtime') as latest,
  COUNT(*) as total_records
FROM signal_history;
```

### Step 3: Query Relevant Data

Based on the symptom, query the appropriate tables (see sections below).

### Step 4: Correlate Events

Look for patterns across multiple data sources at the same timestamps.

### Step 5: Form Hypothesis and Verify

Test your hypothesis against the data before presenting conclusions.

---

## Common Scenarios

### Scenario: Periodic Short Outages

**Symptom**: "20-second drops every 2 minutes"

**Step 1: Check continuous ping for failures**
```sql
SELECT
  datetime(timestamp_unix, 'unixepoch', 'localtime') as time,
  target_host,
  success,
  latency_ms,
  error_type
FROM continuous_ping
WHERE success = 0
ORDER BY timestamp_unix DESC
LIMIT 100;
```

**Step 2: Find the interval between failures**
```sql
WITH failures AS (
  SELECT
    timestamp_unix,
    LAG(timestamp_unix) OVER (ORDER BY timestamp_unix) as prev_time
  FROM continuous_ping
  WHERE success = 0
)
SELECT
  ROUND(AVG(timestamp_unix - prev_time), 1) as avg_gap_seconds,
  ROUND(MIN(timestamp_unix - prev_time), 1) as min_gap_seconds,
  ROUND(MAX(timestamp_unix - prev_time), 1) as max_gap_seconds,
  COUNT(*) as sample_count
FROM failures
WHERE prev_time IS NOT NULL
  AND (timestamp_unix - prev_time) < 600;  -- Ignore gaps > 10 min
```

**Step 3: Correlate with signal quality**
```sql
-- Find signal metrics around failure times
WITH failure_times AS (
  SELECT DISTINCT ROUND(timestamp_unix / 60) * 60 as minute_bucket
  FROM continuous_ping
  WHERE success = 0
)
SELECT
  datetime(s.timestamp_unix, 'unixepoch', 'localtime') as time,
  s.nr_sinr, s.nr_rsrp,
  s.lte_sinr, s.lte_rsrp,
  s.nr_gnb_id, s.lte_enb_id
FROM signal_history s
JOIN failure_times f ON ROUND(s.timestamp_unix / 60) * 60 = f.minute_bucket
ORDER BY s.timestamp_unix DESC
LIMIT 50;
```

**Step 4: Check for tower handoffs**
```sql
SELECT
  datetime(timestamp_unix, 'unixepoch', 'localtime') as time,
  event_type,
  description,
  before_state,
  after_state
FROM disruption_events
WHERE event_type LIKE 'tower_change%'
ORDER BY timestamp_unix DESC
LIMIT 20;
```

---

### Scenario: Gateway Unreachable

**Symptom**: "Can't connect to the internet" or dashboard shows errors

**Step 1: Check recent gateway poll failures**
```sql
SELECT
  datetime(timestamp_unix, 'unixepoch', 'localtime') as time,
  error_type,
  error_message,
  circuit_state,
  signal_snapshot
FROM gateway_poll_events
WHERE success = 0
ORDER BY timestamp_unix DESC
LIMIT 50;
```

**Step 2: Analyze error type distribution**
```sql
SELECT
  error_type,
  COUNT(*) as count,
  ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) as percent
FROM gateway_poll_events
WHERE success = 0
  AND timestamp_unix > unixepoch() - 86400  -- Last 24 hours
GROUP BY error_type
ORDER BY count DESC;
```

**Step 3: Check circuit breaker events**
```sql
SELECT
  datetime(timestamp_unix, 'unixepoch', 'localtime') as time,
  event_type,
  duration_seconds,
  description
FROM disruption_events
WHERE event_type = 'gateway_unreachable'
ORDER BY timestamp_unix DESC
LIMIT 20;
```

**Interpretation:**
- `timeout` errors: Gateway slow to respond, possible congestion
- `connection_refused`: Gateway not listening, possible reboot
- `http_error`: Gateway responding but with errors

---

### Scenario: Slow Speeds

**Symptom**: "Speeds are much lower than expected"

**Step 1: Review recent speed tests**
```sql
SELECT
  datetime(timestamp_unix, 'unixepoch', 'localtime') as time,
  download_mbps,
  upload_mbps,
  ping_ms,
  network_context,
  tool,
  signal_snapshot
FROM speedtest_results
ORDER BY timestamp_unix DESC
LIMIT 20;
```

**Step 2: Compare baseline vs busy periods**
```sql
SELECT
  network_context,
  COUNT(*) as tests,
  ROUND(AVG(download_mbps), 1) as avg_download,
  ROUND(AVG(upload_mbps), 1) as avg_upload,
  ROUND(AVG(ping_ms), 1) as avg_ping
FROM speedtest_results
WHERE timestamp_unix > unixepoch() - 604800  -- Last 7 days
GROUP BY network_context
ORDER BY avg_download DESC;
```

**Step 3: Correlate with signal quality**
```sql
SELECT
  CASE
    WHEN json_extract(signal_snapshot, '$.nr.sinr') > 15 THEN 'excellent'
    WHEN json_extract(signal_snapshot, '$.nr.sinr') > 5 THEN 'good'
    WHEN json_extract(signal_snapshot, '$.nr.sinr') > 0 THEN 'fair'
    ELSE 'poor'
  END as signal_quality,
  COUNT(*) as tests,
  ROUND(AVG(download_mbps), 1) as avg_download
FROM speedtest_results
WHERE signal_snapshot IS NOT NULL
  AND timestamp_unix > unixepoch() - 604800
GROUP BY signal_quality
ORDER BY avg_download DESC;
```

---

### Scenario: Signal Quality Drops

**Symptom**: "Connection becomes unstable randomly"

**Step 1: Find SINR drop events**
```sql
SELECT
  datetime(timestamp_unix, 'unixepoch', 'localtime') as time,
  event_type,
  description,
  json_extract(before_state, '$.nr_sinr') as before_sinr,
  json_extract(after_state, '$.nr_sinr') as after_sinr
FROM disruption_events
WHERE event_type LIKE 'signal_drop%'
ORDER BY timestamp_unix DESC
LIMIT 30;
```

**Step 2: Analyze signal patterns over time**
```sql
SELECT
  strftime('%H', datetime(timestamp_unix, 'unixepoch', 'localtime')) as hour,
  ROUND(AVG(nr_sinr), 1) as avg_5g_sinr,
  ROUND(MIN(nr_sinr), 1) as min_5g_sinr,
  ROUND(AVG(lte_sinr), 1) as avg_4g_sinr,
  COUNT(*) as samples
FROM signal_history
WHERE timestamp_unix > unixepoch() - 86400
GROUP BY hour
ORDER BY hour;
```

**Step 3: Check for tower/band changes during drops**
```sql
WITH signal_drops AS (
  SELECT timestamp_unix
  FROM disruption_events
  WHERE event_type LIKE 'signal_drop%'
)
SELECT
  datetime(d.timestamp_unix, 'unixepoch', 'localtime') as time,
  d.event_type,
  d.description
FROM disruption_events d
WHERE d.event_type IN ('tower_change_5g', 'tower_change_4g', 'connection_mode_change')
  AND EXISTS (
    SELECT 1 FROM signal_drops s
    WHERE ABS(d.timestamp_unix - s.timestamp_unix) < 60
  )
ORDER BY d.timestamp_unix DESC;
```

---

## Loki Queries

### Query Gateway Errors
```bash
curl -s 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={job="tmobile-dashboard", event_type="gateway_error"}' \
  --data-urlencode 'start='$(date -d '1 hour ago' +%s)000000000 \
  --data-urlencode 'end='$(date +%s)000000000 \
  | jq '.data.result[].values | length'
```

### Query Outage Events
```bash
curl -s 'http://localhost:3100/loki/api/v1/query' \
  --data-urlencode 'query={job="tmobile-dashboard", event_type="gateway_outage"} | json' \
  | jq '.data.result[].values[] | .[1] | fromjson'
```

### Query Continuous Ping Failures
```bash
curl -s 'http://localhost:3100/loki/api/v1/query' \
  --data-urlencode 'query={job="tmobile-dashboard", event_type="continuous_ping", success="false"}' \
  | jq '.data.result | length'
```

### Query Signal Quality Drops
```bash
curl -s 'http://localhost:3100/loki/api/v1/query' \
  --data-urlencode 'query={job="tmobile-dashboard", event_type="signal_quality"} | json' \
  | jq '.data.result[].values[] | .[1] | fromjson | {network, drop_db, before: .before_value, after: .after_value}'
```

---

## Signal Quality Reference

### SINR (Signal to Interference Noise Ratio)
| Range | Quality | Expected Performance |
|-------|---------|---------------------|
| > 20 dB | Excellent | Max speeds, stable |
| 13-20 dB | Good | Good speeds |
| 0-13 dB | Fair | Moderate speeds, some drops |
| -5-0 dB | Poor | Slow, unstable |
| < -5 dB | Critical | Frequent disconnections |

### RSRP (Reference Signal Received Power)
| Range | Quality | Notes |
|-------|---------|-------|
| > -80 dBm | Excellent | Close to tower |
| -80 to -90 dBm | Good | Normal indoor |
| -90 to -100 dBm | Fair | Far from tower |
| -100 to -110 dBm | Poor | Edge of coverage |
| < -110 dBm | Critical | May lose connection |

---

## Correlation Patterns

### Pattern: SINR Drop + Tower Change
**Cause**: Handoff to a congested or distant tower
**Evidence**: `tower_change_*` event within 60s of `signal_drop_*`
**Solution**: May need external antenna or location adjustment

### Pattern: Periodic Timeouts + Stable Signal
**Cause**: Gateway firmware issue or ISP-side problem
**Evidence**: Regular timeout errors in `gateway_poll_events` with good signal in `signal_snapshot`
**Solution**: Gateway reboot, firmware update, or ISP ticket

### Pattern: High Packet Loss + Low SINR
**Cause**: Poor signal quality causing retransmissions
**Evidence**: Correlated `network_quality.packet_loss_percent` spikes with low `signal_history.nr_sinr`
**Solution**: Antenna positioning, band locking to stronger signal

### Pattern: Slow Speeds Only During "busy" Context
**Cause**: Local network congestion (other devices) or tower congestion
**Evidence**: `speedtest_results.network_context = 'busy'` consistently slower than `'baseline'`
**Solution**: If baseline is good, local congestion; if baseline also slow, tower congestion

---

## Useful One-Liners

```bash
# Count failures in last hour
sqlite3 backend/signal_history.db "SELECT COUNT(*) FROM gateway_poll_events WHERE success=0 AND timestamp_unix > unixepoch()-3600"

# Average ping latency in last hour
sqlite3 backend/signal_history.db "SELECT ROUND(AVG(latency_ms),1) FROM continuous_ping WHERE success=1 AND timestamp_unix > unixepoch()-3600"

# Current signal quality
sqlite3 backend/signal_history.db "SELECT nr_sinr, nr_rsrp, lte_sinr, lte_rsrp FROM signal_history ORDER BY timestamp_unix DESC LIMIT 1"

# Recent disruption events
sqlite3 backend/signal_history.db "SELECT datetime(timestamp_unix,'unixepoch','localtime'), event_type, description FROM disruption_events ORDER BY timestamp_unix DESC LIMIT 10"

# Failure rate in last 24h
sqlite3 backend/signal_history.db "SELECT ROUND(SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) || '%' FROM continuous_ping WHERE timestamp_unix > unixepoch()-86400"
```

---

## Presenting Findings

When presenting RCA findings to the user:

1. **State the pattern clearly**: "I found X failures over Y period, occurring approximately every Z minutes"

2. **Show the correlation**: "These failures correlate with [signal drops / tower changes / specific times]"

3. **Provide evidence**: Include specific timestamps and values from queries

4. **Suggest root cause**: "This pattern suggests [tower congestion / gateway issue / signal interference]"

5. **Recommend action**: "I recommend [repositioning antenna / contacting ISP / scheduling reboot]"

### Example Output

```
## RCA Summary

**Issue**: Periodic connectivity drops
**Pattern**: 15-25 second outages every 2-3 minutes
**Time Range**: Last 6 hours (2024-01-15 14:00 - 20:00)

**Findings**:
- 47 ping failures detected in continuous_ping
- Average gap between failures: 2.4 minutes
- 12 gateway_unreachable events in disruption_events
- Signal during failures: SINR 8-12 dB (fair), RSRP -95 dBm (fair)
- No tower changes detected during failure windows

**Correlation**: Failures occur regardless of signal quality, suggesting
gateway-side issue rather than signal problem.

**Root Cause**: Likely gateway firmware bug or ISP-side routing issue.

**Recommended Actions**:
1. Reboot gateway and monitor for 1 hour
2. If persists, check for firmware updates
3. If still persists, open ISP support ticket with this data
```
