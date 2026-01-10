# Infra Guidelines

## Services Overview

| Service | Port | Purpose |
|---------|------|---------|
| Grafana | 3002 | Dashboards (admin/tmobile123) |
| Prometheus | 9090 | Metrics scraping & alerting |
| Alertmanager | 9093 | Alert routing |
| Mimir | 9009 | Long-term metrics (30 days) |
| Loki | 3100 | Log aggregation |
| Tempo | 3200, 4317, 4318 | Distributed tracing |
| Gateway Exporter | 9100 | T-Mobile gateway metrics |

## Gateway Exporter

### Supported Gateway Models

| Model | Manufacturer | Notes |
|-------|--------------|-------|
| **Arcadyan KVD21** | Arcadyan | Most common, full support |
| **Sagemcom Fast 5688W** | Sagemcom | Full support |
| **Nokia 5G21** | Nokia | Full support |

### Configuration

```bash
# Environment variables
GATEWAY_URL=http://192.168.12.1/TMI/v1/gateway?get=all
SCRAPE_INTERVAL=5  # seconds
EXPORTER_PORT=9100
```

### Metrics Exposed

```
tmobile_signal_rsrp          # Reference Signal Received Power (dBm)
tmobile_signal_sinr          # Signal to Interference Noise Ratio (dB)
tmobile_signal_rsrq          # Reference Signal Received Quality (dB)
tmobile_signal_rssi          # Received Signal Strength Indicator (dBm)
tmobile_cell_pci             # Physical Cell ID
tmobile_cell_enb             # eNodeB ID (tower)
tmobile_cell_band            # Current band number
tmobile_gateway_scrape_success         # 1 if scrape succeeded, 0 if failed
tmobile_gateway_seconds_since_success  # Seconds since last successful scrape
tmobile_scrape_duration_seconds        # How long the scrape took
```

## Prometheus Alert Rules

Three alert groups defined in `prometheus/rules/tmobile_alerts.yml`:

### 1. Signal Quality Alerts (`tmobile_signal_alerts`)

| Alert | Threshold | Severity |
|-------|-----------|----------|
| TMobileRSRPWarning | RSRP < -110 dBm | warning |
| TMobileRSRPCritical | RSRP < -120 dBm | critical |
| TMobileSINRDegraded | SINR < 0 dB | warning |
| TMobileSINRCritical | SINR < -5 dB | critical |
| TMobileRSRQDegraded | RSRQ < -15 dB | warning |
| TMobileRSRQCritical | RSRQ < -19 dB | critical |

### 2. Connection Stability Alerts (`tmobile_connection_alerts`)

| Alert | Condition | Severity |
|-------|-----------|----------|
| TMobileFrequentTowerHandoffs | PCI changes >3 in 15m | warning |
| TMobileExcessiveTowerHandoffs | PCI changes >6 in 15m | critical |
| TMobileConnectionDown | Scrape failed for 30s | critical |
| TMobileConnectionUnstable | <90% success rate over 5m | warning |
| TMobileDataStale | No data for >30s | warning |
| TMobileExtendedOutage | No data for >300s | critical |
| TMobileFrequentBandChanges | Band changes >4 in 30m | warning |

### 3. Performance Alerts (`tmobile_performance_alerts`)

| Alert | Condition | Severity |
|-------|-----------|----------|
| TMobileOverallSignalPoor | RSRP<-105 AND SINR<5 AND RSRQ<-12 | warning |
| TMobileGatewaySlowResponse | Scrape duration >5s | warning |

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
netstat -an | findstr "3002 9090 9093"

# View container logs
podman logs tmobile-grafana
podman logs tmobile-prometheus
```

**Gateway exporter not collecting:**
```bash
# Test gateway directly
curl http://192.168.12.1/TMI/v1/gateway?get=all

# Check exporter logs
podman logs tmobile-gateway-exporter

# Verify metrics endpoint
curl http://localhost:9100/metrics
```

**Prometheus not scraping:**
```bash
# Check targets status
curl http://localhost:9090/api/v1/targets

# Verify config loaded
curl http://localhost:9090/api/v1/status/config
```

**Grafana dashboards empty:**
- Verify Prometheus datasource: http://localhost:3002/connections/datasources
- Check time range selector (default may be too narrow)
- Ensure gateway exporter is running and scraped

---

## Prometheus Exporters

### Never Cache Stale Data

When a scrape fails, **clear all metric values** instead of serving cached data:

```python
# WRONG: Leaves stale values in gauges
except Exception as e:
    scrape_success.set(0)
    logger.error(f"Scrape failed: {e}")

# CORRECT: Clear metrics so Grafana shows gaps
except Exception as e:
    scrape_success.set(0)
    clear_all_metrics()  # Set gauges to float('nan')
    logger.error(f"Scrape failed: {e}")
```

Prometheus gauges retain their last value forever. NaN values create visible gaps in graphs.

### Track Staleness Duration

Always include metrics that answer:
- "When was the last successful data collection?"
- "How long has data been stale?"

```python
gateway_last_success_timestamp = Gauge('..._last_success_timestamp', 'Unix timestamp of last successful scrape')
gateway_seconds_since_success = Gauge('..._seconds_since_success', 'Seconds since last successful data collection')
```

This enables staleness-based alerts and dashboards showing outage duration.

## Prometheus Alerting

### Alert on Data Staleness, Not Just Failures

```yaml
# Alert when data is stale (not just when scrape fails)
- alert: TMobileDataStale
  expr: tmobile_gateway_seconds_since_success > 30
  labels:
    severity: warning

- alert: TMobileExtendedOutage
  expr: tmobile_gateway_seconds_since_success > 300
  labels:
    severity: critical
```

## Grafana Dashboards

### Annotate Outages on All Signal Graphs

Add annotations to dashboards so outage periods are clearly visible:

```json
"annotations": {
  "list": [
    {
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "enable": true,
      "expr": "your_scrape_success_metric == 0",
      "iconColor": "red",
      "name": "Outages",
      "titleFormat": "Service Offline"
    }
  ]
}
```

This adds red markers on all time-series panels during outages.

### Show Staleness in Stats

Include panels that show:
- Current staleness duration (with color thresholds: green/yellow/red)
- Last successful scrape timestamp (using `dateTimeFromNow` unit)

## Core Principle

**The absence of data is itself critical information.**

An observability system must clearly distinguish between:
- "The value is X" (real measurement)
- "We don't know the value" (no data / stale)

Showing stale data as current is worse than showing nothing - it creates false confidence and hides real problems.
