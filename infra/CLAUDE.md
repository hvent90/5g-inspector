# Infra Guidelines

## Services Overview

| Service | Port | Purpose |
|---------|------|---------|
| PostgreSQL | 5432 | Data storage (netpulse:netpulse_secret) |
| Grafana | 3002 | Dashboards & Alerting (admin/netpulse123) |

> **Note:** The API backend (apps/api) polls the gateway directly at 200ms intervals and stores data in PostgreSQL, which Grafana queries using its native PostgreSQL datasource (no plugins needed).

## Database Setup

PostgreSQL tables:
- `signal_history` - Raw signal samples (200ms interval)
- `speedtest_results` - Speed test results with signal snapshots
- `disruption_events` - Detected signal disruptions
- `network_quality_results` - Network quality ping results

Run migrations after starting the stack:
```bash
cd apps/api && bun run db:migrate
```

## Grafana Alert Rules (PostgreSQL-based)

Alerting is managed via Grafana's unified alerting system using PostgreSQL queries for persistent historical data.
This provides better data retention and more accurate alerting than in-memory solutions.

Alert rules are provisioned from:
- `grafana/provisioning/alerting/alert_rules.yml` - PostgreSQL-based alert rule definitions
- `grafana/provisioning/alerting/alerting.yml` - Contact points and notification policies

### 1. Signal Quality Alerts (30s interval)

| Alert | Threshold | Severity | Data Source |
|-------|-----------|----------|-------------|
| NetPulse 5G SINR Warning | SINR avg < 0 dB for 2m | warning | signal_history |
| NetPulse 5G SINR Critical | SINR avg < -5 dB for 1m | critical | signal_history |
| NetPulse 5G RSRP Warning | RSRP avg < -110 dBm for 2m | warning | signal_history |
| NetPulse 5G RSRP Critical | RSRP avg < -120 dBm for 1m | critical | signal_history |
| NetPulse Overall Signal Poor | RSRP<-105 AND SINR<5 AND RSRQ<-12 for 5m | warning | signal_history |

### 2. Speedtest Alerts (60s interval)

| Alert | Threshold | Severity | Data Source |
|-------|-----------|----------|-------------|
| NetPulse Low Download Speed | avg < 10 Mbps over 1h | warning | speedtest_results |
| NetPulse Very Low Download Speed | avg < 5 Mbps over 1h | critical | speedtest_results |
| NetPulse Speedtest Failures | >3 failures in 1h | warning | speedtest_results |

### 3. Latency Alerts (30s interval)

| Alert | Threshold | Severity | Data Source |
|-------|-----------|----------|-------------|
| NetPulse High Latency Warning | avg > 100ms for 2m | warning | network_quality_results |
| NetPulse High Latency Critical | avg > 200ms for 1m | critical | network_quality_results |
| NetPulse High Packet Loss | > 5% for 2m | warning | network_quality_results |
| NetPulse Severe Packet Loss | > 20% for 1m | critical | network_quality_results |

### 4. Connection Alerts (30s interval)

| Alert | Threshold | Severity | Data Source |
|-------|-----------|----------|-------------|
| NetPulse No Data Collection | >60s since last data | critical | signal_history |
| NetPulse Extended Data Outage | >300s since last data | critical | signal_history |

### 5. Disruption Alerts (60s interval)

| Alert | Threshold | Severity | Data Source |
|-------|-----------|----------|-------------|
| NetPulse Frequent Disruptions | >10 events in 1h | warning | disruption_events |
| NetPulse Critical Disruptions | Any critical severity in 15m | critical | disruption_events |
| NetPulse Frequent Tower Handoffs | >3 tower changes in 15m | warning | disruption_events |
| NetPulse Gateway Unreachable Events | >5 events in 1h | critical | disruption_events |

### Notification Policy

Alerts use Grafana's built-in notification system with severity-based timing:

| Severity | Group Wait | Group Interval | Repeat Interval |
|----------|------------|----------------|-----------------|
| critical | 10s | 1m | 1h |
| warning | 30s | 5m | 4h |
| info | 60s | 15m | 12h |

### Configuring Notifications

To configure notifications in Grafana:

1. **Email**: Edit `grafana/provisioning/alerting/alerting.yml` and set addresses in the `grafana-default-email` contact point
2. **Slack/Discord/PagerDuty**: Add new contact points in `alerting.yml` with the appropriate receiver type
3. **Custom webhooks**: Add webhook receivers pointing to your notification service

## Troubleshooting

### Podman Setup (Windows/Mac)

```bash
# Initialize podman machine (first time only)
podman machine init
podman machine start

# Verify it's running
podman machine list
```

### Common Issues

**Stack won't start:**
```bash
# Check podman is running
podman machine list

# Check for port conflicts
netstat -an | findstr "3002 5432"

# View container logs
podman logs netpulse-grafana
podman logs netpulse-postgres
```

**Grafana dashboards empty:**
- Verify PostgreSQL datasource: http://localhost:3002/connections/datasources
- Check time range selector (default may be too narrow)
- Ensure API backend (apps/api) is running and collecting data
- Run migrations: `cd apps/api && bun run db:migrate`

**Database connection issues:**
```bash
# Check PostgreSQL is running
podman exec netpulse-postgres pg_isready -U netpulse

# Connect to database
podman exec -it netpulse-postgres psql -U netpulse -d netpulse

# Check tables exist
\dt
```

---

## Core Principle

**The absence of data is itself critical information.**

An observability system must clearly distinguish between:
- "The value is X" (real measurement)
- "We don't know the value" (no data / stale)

Showing stale data as current is worse than showing nothing - it creates false confidence and hides real problems.
