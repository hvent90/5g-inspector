"""
Alerting system for T-Mobile dashboard.
Monitors signal metrics, speed test results, and gateway connectivity.
Provides real-time alerts via SSE and optional webhook notifications.
"""

import json
import os
import threading
import time
import urllib.request
from datetime import datetime
from collections import deque
from typing import Optional, Dict, List, Any, Callable

# File paths for persistence
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ALERT_CONFIG_FILE = os.path.join(BASE_DIR, 'alert_config.json')
ALERT_HISTORY_FILE = os.path.join(BASE_DIR, 'alert_history.json')

# Default alert configuration
DEFAULT_CONFIG = {
    'enabled': True,
    'thresholds': {
        '5g_sinr': {'min': 5, 'enabled': True},       # dB
        '5g_rsrp': {'min': -100, 'enabled': True},    # dBm
        '5g_rsrq': {'min': -15, 'enabled': True},     # dB
        '4g_sinr': {'min': 5, 'enabled': True},       # dB
        '4g_rsrp': {'min': -100, 'enabled': True},    # dBm
        '4g_rsrq': {'min': -15, 'enabled': True},     # dB
        'download_mbps': {'min': 25, 'enabled': True},
        'upload_mbps': {'min': 5, 'enabled': True},
        'ping_ms': {'max': 100, 'enabled': True},
    },
    'gateway_timeout': 10,  # seconds before gateway considered disconnected
    'cooldown': 300,        # seconds between same alert type
    'webhook_url': None,    # optional webhook for external notifications
    'max_history': 1000,    # maximum alerts to keep in history
}

# In-memory state
_config = None
_config_lock = threading.Lock()
_history = []
_history_lock = threading.Lock()
_active_alerts = {}  # alert_type -> alert_dict
_active_alerts_lock = threading.Lock()
_cooldowns = {}  # alert_type -> timestamp of last alert
_cooldowns_lock = threading.Lock()
_sse_subscribers = []  # list of queue objects for SSE clients
_sse_lock = threading.Lock()
_monitor_thread = None
_monitor_stop_event = threading.Event()

# Callback for getting current signal data (set by server.py)
_get_signal_data: Optional[Callable] = None
_get_speedtest_results: Optional[Callable] = None


def load_alert_config() -> Dict:
    """Load alert configuration from file or return defaults."""
    global _config
    with _config_lock:
        if _config is not None:
            return _config.copy()

        if os.path.exists(ALERT_CONFIG_FILE):
            try:
                with open(ALERT_CONFIG_FILE, 'r') as f:
                    _config = json.load(f)
                # Merge with defaults for any missing keys
                for key, value in DEFAULT_CONFIG.items():
                    if key not in _config:
                        _config[key] = value
                    elif isinstance(value, dict):
                        for k, v in value.items():
                            if k not in _config[key]:
                                _config[key][k] = v
            except (json.JSONDecodeError, IOError):
                _config = DEFAULT_CONFIG.copy()
        else:
            _config = DEFAULT_CONFIG.copy()

        return _config.copy()


def save_alert_config(config: Dict) -> bool:
    """Save alert configuration to file."""
    global _config
    with _config_lock:
        try:
            with open(ALERT_CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=2)
            _config = config.copy()
            return True
        except IOError:
            return False


def load_alert_history() -> List[Dict]:
    """Load alert history from file."""
    global _history
    with _history_lock:
        if _history:
            return _history.copy()

        if os.path.exists(ALERT_HISTORY_FILE):
            try:
                with open(ALERT_HISTORY_FILE, 'r') as f:
                    _history = json.load(f)
            except (json.JSONDecodeError, IOError):
                _history = []

        return _history.copy()


def save_alert_history() -> bool:
    """Save alert history to file."""
    with _history_lock:
        try:
            config = load_alert_config()
            max_history = config.get('max_history', 1000)
            # Trim history if needed
            if len(_history) > max_history:
                _history[:] = _history[-max_history:]

            with open(ALERT_HISTORY_FILE, 'w') as f:
                json.dump(_history, f)
            return True
        except IOError:
            return False


def check_cooldown(alert_type: str) -> bool:
    """Check if alert type is in cooldown period. Returns True if alert can fire."""
    config = load_alert_config()
    cooldown_period = config.get('cooldown', 300)

    with _cooldowns_lock:
        last_time = _cooldowns.get(alert_type, 0)
        now = time.time()
        return (now - last_time) >= cooldown_period


def set_cooldown(alert_type: str):
    """Set cooldown timestamp for alert type."""
    with _cooldowns_lock:
        _cooldowns[alert_type] = time.time()


def generate_alert_id() -> str:
    """Generate unique alert ID."""
    return f"alert_{int(time.time() * 1000)}_{os.urandom(4).hex()}"


def trigger_alert(alert_type: str, severity: str, message: str,
                  details: Optional[Dict] = None) -> Optional[Dict]:
    """
    Trigger an alert if not in cooldown.

    Args:
        alert_type: Type of alert (e.g., '5g_sinr_low', 'gateway_disconnected')
        severity: 'warning', 'critical', or 'info'
        message: Human-readable alert message
        details: Additional context (current values, thresholds, etc.)

    Returns:
        Alert dict if triggered, None if in cooldown
    """
    if not check_cooldown(alert_type):
        return None

    config = load_alert_config()
    if not config.get('enabled', True):
        return None

    alert = {
        'id': generate_alert_id(),
        'type': alert_type,
        'severity': severity,
        'message': message,
        'details': details or {},
        'timestamp': datetime.now().isoformat(),
        'acknowledged': False,
        'cleared': False,
    }

    # Add to active alerts
    with _active_alerts_lock:
        _active_alerts[alert_type] = alert

    # Add to history
    with _history_lock:
        _history.append(alert.copy())
    save_alert_history()

    # Set cooldown
    set_cooldown(alert_type)

    # Notify SSE subscribers
    notify_sse_subscribers(alert)

    # Send webhook if configured
    webhook_url = config.get('webhook_url')
    if webhook_url:
        send_webhook(webhook_url, alert)

    return alert


def notify_sse_subscribers(alert: Dict):
    """Send alert to all SSE subscribers."""
    message = f"data: {json.dumps(alert)}\n\n"

    with _sse_lock:
        # Remove dead subscribers
        _sse_subscribers[:] = [q for q in _sse_subscribers if not q.get('closed', False)]

        for subscriber in _sse_subscribers:
            try:
                subscriber['queue'].append(message)
            except:
                subscriber['closed'] = True


def subscribe_sse() -> Dict:
    """Create a new SSE subscriber. Returns subscriber dict with queue."""
    subscriber = {
        'queue': deque(maxlen=100),
        'closed': False,
        'created': time.time()
    }
    with _sse_lock:
        _sse_subscribers.append(subscriber)
    return subscriber


def unsubscribe_sse(subscriber: Dict):
    """Remove SSE subscriber."""
    subscriber['closed'] = True
    with _sse_lock:
        if subscriber in _sse_subscribers:
            _sse_subscribers.remove(subscriber)


def send_webhook(url: str, alert: Dict):
    """Send alert to webhook URL (fire and forget)."""
    def _send():
        try:
            data = json.dumps(alert).encode('utf-8')
            req = urllib.request.Request(
                url,
                data=data,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            urllib.request.urlopen(req, timeout=10)
        except:
            pass  # Webhook failures are logged but don't affect alerting

    threading.Thread(target=_send, daemon=True).start()


def get_active_alerts() -> List[Dict]:
    """Get all currently active (uncleared) alerts."""
    with _active_alerts_lock:
        return [a for a in _active_alerts.values() if not a.get('cleared', False)]


def get_alert_history(limit: int = 100, offset: int = 0) -> List[Dict]:
    """Get alert history with pagination."""
    history = load_alert_history()
    # Return newest first
    history = list(reversed(history))
    return history[offset:offset + limit]


def acknowledge_alert(alert_id: str) -> bool:
    """Mark an alert as acknowledged."""
    with _active_alerts_lock:
        for alert in _active_alerts.values():
            if alert['id'] == alert_id:
                alert['acknowledged'] = True
                alert['acknowledged_at'] = datetime.now().isoformat()
                return True

    # Also update in history
    with _history_lock:
        for alert in _history:
            if alert['id'] == alert_id:
                alert['acknowledged'] = True
                alert['acknowledged_at'] = datetime.now().isoformat()
                save_alert_history()
                return True

    return False


def clear_alert(alert_id: str) -> bool:
    """Clear an alert (remove from active alerts)."""
    with _active_alerts_lock:
        for alert_type, alert in list(_active_alerts.items()):
            if alert['id'] == alert_id:
                alert['cleared'] = True
                alert['cleared_at'] = datetime.now().isoformat()
                del _active_alerts[alert_type]

                # Notify SSE subscribers of clear
                clear_notification = {
                    'type': 'alert_cleared',
                    'alert_id': alert_id,
                    'timestamp': datetime.now().isoformat()
                }
                notify_sse_subscribers(clear_notification)
                return True
    return False


def clear_all_alerts() -> int:
    """Clear all active alerts. Returns count of cleared alerts."""
    with _active_alerts_lock:
        count = len(_active_alerts)
        now = datetime.now().isoformat()

        for alert in _active_alerts.values():
            alert['cleared'] = True
            alert['cleared_at'] = now

        _active_alerts.clear()

        if count > 0:
            # Notify SSE subscribers
            clear_notification = {
                'type': 'all_alerts_cleared',
                'count': count,
                'timestamp': now
            }
            notify_sse_subscribers(clear_notification)

        return count


def check_signal_thresholds(signal_data: Dict) -> List[Dict]:
    """Check signal metrics against thresholds. Returns list of triggered alerts."""
    config = load_alert_config()
    thresholds = config.get('thresholds', {})
    alerts = []

    # Map of signal data keys to threshold keys
    signal_map = {
        ('cell_5g_stats', 'PhysicalCellID', 'SNRCurrent'): '5g_sinr',
        ('cell_5g_stats', 'PhysicalCellID', 'RSRPCurrent'): '5g_rsrp',
        ('cell_5g_stats', 'PhysicalCellID', 'RSRQCurrent'): '5g_rsrq',
        ('cell_lte_stats', 'PhysicalCellID', 'SNRCurrent'): '4g_sinr',
        ('cell_lte_stats', 'PhysicalCellID', 'RSRPCurrent'): '4g_rsrp',
        ('cell_lte_stats', 'PhysicalCellID', 'RSRQCurrent'): '4g_rsrq',
    }

    # Extract values from nested signal data
    def get_value(data: Dict, *keys) -> Optional[float]:
        try:
            result = data
            for key in keys:
                if isinstance(result, list) and result:
                    result = result[0]
                if key in result:
                    result = result[key]
                else:
                    return None
            return float(result) if result is not None else None
        except (KeyError, TypeError, ValueError, IndexError):
            return None

    # Check 5G metrics
    for stat_key, metric_name in [('SNRCurrent', '5g_sinr'), ('RSRPCurrent', '5g_rsrp'), ('RSRQCurrent', '5g_rsrq')]:
        threshold_config = thresholds.get(metric_name, {})
        if not threshold_config.get('enabled', True):
            continue

        min_val = threshold_config.get('min')
        if min_val is None:
            continue

        value = get_value(signal_data, 'cell_5g_stats', stat_key)
        if value is not None and value < min_val:
            alert = trigger_alert(
                alert_type=f'{metric_name}_low',
                severity='critical' if metric_name.endswith('sinr') else 'warning',
                message=f'5G {stat_key.replace("Current", "")} below threshold: {value:.1f} (min: {min_val})',
                details={
                    'metric': metric_name,
                    'current_value': value,
                    'threshold': min_val,
                }
            )
            if alert:
                alerts.append(alert)

    # Check 4G metrics
    for stat_key, metric_name in [('SNRCurrent', '4g_sinr'), ('RSRPCurrent', '4g_rsrp'), ('RSRQCurrent', '4g_rsrq')]:
        threshold_config = thresholds.get(metric_name, {})
        if not threshold_config.get('enabled', True):
            continue

        min_val = threshold_config.get('min')
        if min_val is None:
            continue

        value = get_value(signal_data, 'cell_lte_stats', stat_key)
        if value is not None and value < min_val:
            alert = trigger_alert(
                alert_type=f'{metric_name}_low',
                severity='critical' if metric_name.endswith('sinr') else 'warning',
                message=f'4G {stat_key.replace("Current", "")} below threshold: {value:.1f} (min: {min_val})',
                details={
                    'metric': metric_name,
                    'current_value': value,
                    'threshold': min_val,
                }
            )
            if alert:
                alerts.append(alert)

    return alerts


def check_speedtest_results(speedtest: Dict) -> List[Dict]:
    """Check speed test results against thresholds. Returns list of triggered alerts."""
    config = load_alert_config()
    thresholds = config.get('thresholds', {})
    alerts = []

    # Download speed
    dl_config = thresholds.get('download_mbps', {})
    if dl_config.get('enabled', True):
        min_dl = dl_config.get('min', 25)
        dl_value = speedtest.get('download_mbps')
        if dl_value is not None and dl_value < min_dl:
            alert = trigger_alert(
                alert_type='download_speed_low',
                severity='warning',
                message=f'Download speed below threshold: {dl_value:.1f} Mbps (min: {min_dl} Mbps)',
                details={
                    'metric': 'download_mbps',
                    'current_value': dl_value,
                    'threshold': min_dl,
                    'speedtest_timestamp': speedtest.get('timestamp'),
                }
            )
            if alert:
                alerts.append(alert)

    # Upload speed
    ul_config = thresholds.get('upload_mbps', {})
    if ul_config.get('enabled', True):
        min_ul = ul_config.get('min', 5)
        ul_value = speedtest.get('upload_mbps')
        if ul_value is not None and ul_value < min_ul:
            alert = trigger_alert(
                alert_type='upload_speed_low',
                severity='warning',
                message=f'Upload speed below threshold: {ul_value:.1f} Mbps (min: {min_ul} Mbps)',
                details={
                    'metric': 'upload_mbps',
                    'current_value': ul_value,
                    'threshold': min_ul,
                    'speedtest_timestamp': speedtest.get('timestamp'),
                }
            )
            if alert:
                alerts.append(alert)

    # Ping/latency
    ping_config = thresholds.get('ping_ms', {})
    if ping_config.get('enabled', True):
        max_ping = ping_config.get('max', 100)
        ping_value = speedtest.get('ping_ms')
        if ping_value is not None and ping_value > max_ping:
            alert = trigger_alert(
                alert_type='ping_high',
                severity='warning',
                message=f'Ping above threshold: {ping_value:.1f} ms (max: {max_ping} ms)',
                details={
                    'metric': 'ping_ms',
                    'current_value': ping_value,
                    'threshold': max_ping,
                    'speedtest_timestamp': speedtest.get('timestamp'),
                }
            )
            if alert:
                alerts.append(alert)

    return alerts


def check_gateway_connection(signal_data: Optional[Dict], last_success: float) -> Optional[Dict]:
    """Check if gateway connection is lost. Returns alert if triggered."""
    config = load_alert_config()
    timeout = config.get('gateway_timeout', 10)

    now = time.time()
    if signal_data is None or (now - last_success) > timeout:
        return trigger_alert(
            alert_type='gateway_disconnected',
            severity='critical',
            message=f'Gateway connection lost (no response for {now - last_success:.0f}s)',
            details={
                'last_success': datetime.fromtimestamp(last_success).isoformat() if last_success else None,
                'timeout_threshold': timeout,
            }
        )

    # If connection restored, clear the disconnected alert
    with _active_alerts_lock:
        if 'gateway_disconnected' in _active_alerts:
            clear_alert(_active_alerts['gateway_disconnected']['id'])

    return None


def set_data_callbacks(get_signal: Callable, get_speedtest: Callable):
    """Set callback functions for getting current data."""
    global _get_signal_data, _get_speedtest_results
    _get_signal_data = get_signal
    _get_speedtest_results = get_speedtest


def _monitor_loop():
    """Background monitoring loop."""
    last_signal_check = 0
    signal_check_interval = 5  # Check signal every 5 seconds

    while not _monitor_stop_event.is_set():
        try:
            now = time.time()

            # Check signal thresholds periodically
            if _get_signal_data and (now - last_signal_check) >= signal_check_interval:
                signal_data, last_success = _get_signal_data()
                if signal_data:
                    check_signal_thresholds(signal_data)
                check_gateway_connection(signal_data, last_success)
                last_signal_check = now

        except Exception as e:
            pass  # Don't let exceptions crash the monitor

        _monitor_stop_event.wait(1)  # Check every second


def start_alert_monitor():
    """Start the background alert monitoring thread."""
    global _monitor_thread

    if _monitor_thread is not None and _monitor_thread.is_alive():
        return  # Already running

    _monitor_stop_event.clear()
    _monitor_thread = threading.Thread(target=_monitor_loop, daemon=True)
    _monitor_thread.start()


def stop_alert_monitor():
    """Stop the background alert monitoring thread."""
    _monitor_stop_event.set()
    if _monitor_thread:
        _monitor_thread.join(timeout=5)


# Initialize on import
load_alert_config()
load_alert_history()
