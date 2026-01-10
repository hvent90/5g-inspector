#!/usr/bin/env python3
"""
T-Mobile 5G Gateway Prometheus Exporter

Scrapes metrics from the T-Mobile gateway (192.168.12.1) and exposes them
in Prometheus format for collection by Prometheus/Mimir.

Metrics exported:
- 5G NR signal metrics (SINR, RSRP, RSRQ, RSSI)
- 4G LTE signal metrics (SINR, RSRP, RSRQ, RSSI)
- Connection information (bands, tower IDs, cell IDs)
- Gateway status (uptime, registration status)
"""

import os
import time
import json
import logging
from prometheus_client import start_http_server, Gauge, Info, Counter, Histogram

try:
    import requests
except ImportError:
    import urllib.request
    import urllib.error
    requests = None

# Configuration from environment
GATEWAY_URL = os.getenv('GATEWAY_URL', 'http://192.168.12.1/TMI/v1/gateway?get=all')
SCRAPE_INTERVAL = int(os.getenv('SCRAPE_INTERVAL', '5'))
EXPORTER_PORT = int(os.getenv('EXPORTER_PORT', '9100'))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================
# Prometheus Metrics Definitions
# ============================================

# 5G NR Signal Metrics
nr_sinr = Gauge('tmobile_5g_sinr_db', '5G NR Signal-to-Interference-plus-Noise Ratio (dB)')
nr_rsrp = Gauge('tmobile_5g_rsrp_dbm', '5G NR Reference Signal Received Power (dBm)')
nr_rsrq = Gauge('tmobile_5g_rsrq_db', '5G NR Reference Signal Received Quality (dB)')
nr_rssi = Gauge('tmobile_5g_rssi_dbm', '5G NR Received Signal Strength Indicator (dBm)')
nr_bars = Gauge('tmobile_5g_bars', '5G NR signal bars (0-5)')

# 4G LTE Signal Metrics
lte_sinr = Gauge('tmobile_4g_sinr_db', '4G LTE Signal-to-Interference-plus-Noise Ratio (dB)')
lte_rsrp = Gauge('tmobile_4g_rsrp_dbm', '4G LTE Reference Signal Received Power (dBm)')
lte_rsrq = Gauge('tmobile_4g_rsrq_db', '4G LTE Reference Signal Received Quality (dB)')
lte_rssi = Gauge('tmobile_4g_rssi_dbm', '4G LTE Received Signal Strength Indicator (dBm)')
lte_bars = Gauge('tmobile_4g_bars', '4G LTE signal bars (0-5)')

# Tower/Cell Information
nr_gnb_id = Gauge('tmobile_5g_gnb_id', '5G NR gNodeB ID (tower identifier)')
nr_cid = Gauge('tmobile_5g_cell_id', '5G NR Cell ID')
lte_enb_id = Gauge('tmobile_4g_enb_id', '4G LTE eNodeB ID (tower identifier)')
lte_cid = Gauge('tmobile_4g_cell_id', '4G LTE Cell ID')

# Band Information (as labels)
nr_band_info = Info('tmobile_5g_bands', '5G NR bands in use')
lte_band_info = Info('tmobile_4g_bands', '4G LTE bands in use')

# Gateway Status
gateway_uptime = Gauge('tmobile_gateway_uptime_seconds', 'Gateway uptime in seconds')
gateway_scrape_success = Gauge('tmobile_gateway_scrape_success', 'Whether the last scrape was successful (1=yes, 0=no)')
gateway_scrape_duration = Histogram('tmobile_gateway_scrape_duration_seconds', 'Time taken to scrape gateway metrics')
gateway_last_success = Gauge('tmobile_gateway_last_success_timestamp', 'Unix timestamp of last successful scrape')
gateway_seconds_since_success = Gauge('tmobile_gateway_seconds_since_success', 'Seconds since last successful data collection')

# Connection Status
connection_info = Info('tmobile_connection', 'Connection status information')

# Scrape counters
scrape_total = Counter('tmobile_scrapes_total', 'Total number of scrape attempts')
scrape_errors = Counter('tmobile_scrape_errors_total', 'Total number of scrape errors')


def fetch_gateway_data():
    """Fetch data from T-Mobile gateway API"""
    if requests:
        response = requests.get(GATEWAY_URL, timeout=5)
        response.raise_for_status()
        return response.json()
    else:
        # Fallback to urllib
        req = urllib.request.Request(GATEWAY_URL)
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read().decode('utf-8'))


def update_metrics(data):
    """Update Prometheus metrics from gateway data"""

    # Parse signal data
    signal = data.get('signal', {})
    s5g = signal.get('5g', {})
    s4g = signal.get('4g', {})
    generic = signal.get('generic', {})
    time_info = data.get('time', {})

    # 5G NR Metrics
    if s5g.get('sinr') is not None:
        nr_sinr.set(s5g['sinr'])
    if s5g.get('rsrp') is not None:
        nr_rsrp.set(s5g['rsrp'])
    if s5g.get('rsrq') is not None:
        nr_rsrq.set(s5g['rsrq'])
    if s5g.get('rssi') is not None:
        nr_rssi.set(s5g['rssi'])
    if s5g.get('bars') is not None:
        nr_bars.set(s5g['bars'])

    # 5G Tower/Cell Info
    if s5g.get('gNBID') is not None:
        nr_gnb_id.set(s5g['gNBID'])
    if s5g.get('cid') is not None:
        nr_cid.set(s5g['cid'])

    # 5G Bands
    nr_bands = s5g.get('bands', [])
    if nr_bands:
        nr_band_info.info({'bands': ','.join(map(str, nr_bands))})

    # 4G LTE Metrics
    if s4g.get('sinr') is not None:
        lte_sinr.set(s4g['sinr'])
    if s4g.get('rsrp') is not None:
        lte_rsrp.set(s4g['rsrp'])
    if s4g.get('rsrq') is not None:
        lte_rsrq.set(s4g['rsrq'])
    if s4g.get('rssi') is not None:
        lte_rssi.set(s4g['rssi'])
    if s4g.get('bars') is not None:
        lte_bars.set(s4g['bars'])

    # 4G Tower/Cell Info
    if s4g.get('eNBID') is not None:
        lte_enb_id.set(s4g['eNBID'])
    if s4g.get('cid') is not None:
        lte_cid.set(s4g['cid'])

    # 4G Bands
    lte_bands = s4g.get('bands', [])
    if lte_bands:
        lte_band_info.info({'bands': ','.join(map(str, lte_bands))})

    # Gateway uptime
    if time_info.get('upTime') is not None:
        gateway_uptime.set(time_info['upTime'])

    # Connection status
    registration = generic.get('registration', 'unknown')
    apn = generic.get('apn', 'unknown')
    roaming = str(generic.get('roaming', False)).lower()
    has_ipv6 = str(generic.get('hasIPv6', False)).lower()

    # Determine connection mode
    has_4g = bool(s4g.get('bands', []))
    has_5g = bool(s5g.get('bands', []))
    if has_5g and has_4g:
        conn_mode = 'NSA'
    elif has_5g:
        conn_mode = 'SA'
    elif has_4g:
        conn_mode = 'LTE'
    else:
        conn_mode = 'none'

    connection_info.info({
        'registration': registration,
        'apn': apn,
        'roaming': roaming,
        'ipv6': has_ipv6,
        'mode': conn_mode
    })


def clear_signal_metrics():
    """Clear all signal metrics when scrape fails to avoid stale data"""
    # Use float('nan') to indicate no data - Prometheus will treat as stale
    nr_sinr.set(float('nan'))
    nr_rsrp.set(float('nan'))
    nr_rsrq.set(float('nan'))
    nr_rssi.set(float('nan'))
    nr_bars.set(float('nan'))
    nr_gnb_id.set(float('nan'))
    nr_cid.set(float('nan'))

    lte_sinr.set(float('nan'))
    lte_rsrp.set(float('nan'))
    lte_rsrq.set(float('nan'))
    lte_rssi.set(float('nan'))
    lte_bars.set(float('nan'))
    lte_enb_id.set(float('nan'))
    lte_cid.set(float('nan'))

    gateway_uptime.set(float('nan'))


def scrape_loop():
    """Main scrape loop"""
    logger.info(f"Starting exporter on port {EXPORTER_PORT}")
    logger.info(f"Scraping {GATEWAY_URL} every {SCRAPE_INTERVAL} seconds")

    last_success_time = 0.0  # Track when we last got real data

    while True:
        scrape_total.inc()
        start_time = time.time()

        try:
            data = fetch_gateway_data()
            update_metrics(data)
            gateway_scrape_success.set(1)
            last_success_time = time.time()
            gateway_last_success.set(last_success_time)
            gateway_seconds_since_success.set(0)
            logger.debug("Scrape successful")
        except Exception as e:
            scrape_errors.inc()
            gateway_scrape_success.set(0)
            clear_signal_metrics()  # Clear stale values on failure
            # Update staleness metrics - shows how long we've been without data
            if last_success_time > 0:
                gateway_seconds_since_success.set(time.time() - last_success_time)
            logger.error(f"Scrape failed: {e}")

        duration = time.time() - start_time
        gateway_scrape_duration.observe(duration)

        time.sleep(SCRAPE_INTERVAL)


if __name__ == '__main__':
    # Start Prometheus HTTP server
    start_http_server(EXPORTER_PORT)
    logger.info(f"Prometheus metrics available at http://0.0.0.0:{EXPORTER_PORT}/metrics")

    # Start scraping
    scrape_loop()
