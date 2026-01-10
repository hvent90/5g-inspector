"""
Network Quality Monitoring Module for T-Mobile Dashboard

Implements continuous packet loss and jitter monitoring through ping tests
to multiple targets. This supplements speed tests to provide evidence of
network reliability issues for FCC complaints.

Features:
- Periodic ping tests to multiple targets (8.8.8.8, 1.1.1.1, T-Mobile servers)
- Track packet loss percentage
- Track jitter (latency variance)
- Store results in SQLite database with timestamps
- Configurable test intervals and targets
- Real-time monitoring via scheduler integration

Evidence Goal:
- High packet loss = unreliable connection
- High jitter = poor quality for video calls, gaming
- Complements speed test data for FCC complaint
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import statistics
import re
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Callable, Tuple


# Configuration defaults
DEFAULT_CONFIG = {
    'enabled': False,
    'interval_minutes': 5,  # How often to run tests
    'min_interval_minutes': 1,
    'max_interval_minutes': 60,
    'ping_count': 20,  # Number of pings per target
    'ping_timeout_seconds': 5,  # Timeout per ping
    'targets': [
        {'host': '8.8.8.8', 'name': 'Google DNS'},
        {'host': '1.1.1.1', 'name': 'Cloudflare DNS'},
        {'host': '208.54.0.1', 'name': 'T-Mobile DNS'},  # T-Mobile DNS server
    ],
    'packet_loss_threshold_percent': 5,  # Alert when above this
    'jitter_threshold_ms': 50,  # Alert when above this
    'notify_on_threshold': True,
}

# Module state
_config = dict(DEFAULT_CONFIG)
_monitor_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_last_test_time = 0
_next_test_time = 0
_stats = {
    'tests_completed': 0,
    'tests_failed': 0,
    'tests_above_threshold': 0,
    'avg_packet_loss': 0,
    'avg_jitter': 0,
    'max_packet_loss': None,
    'max_jitter': None,
    'monitor_started_at': None,
    'last_error': None
}
_config_lock = threading.Lock()
_stats_lock = threading.Lock()

# Database path (set by server.py)
_db_path: Optional[str] = None
_config_file_path: Optional[str] = None


def init_network_quality_monitor(db_path: str, config_file_path: str):
    """Initialize the network quality monitoring module.

    Args:
        db_path: Path to SQLite database
        config_file_path: Path to save monitor config
    """
    global _db_path, _config_file_path
    _db_path = db_path
    _config_file_path = config_file_path

    # Initialize database table
    _init_database_table()

    # Load saved configuration
    _load_config()

    # Auto-start if was enabled
    if _config.get('enabled'):
        start_monitor()

    print(f'[NETWORK_QUALITY] Initialized - interval: {_config["interval_minutes"]}min, enabled: {_config["enabled"]}')


def _init_database_table():
    """Create the network_quality table if it doesn't exist."""
    if not _db_path:
        return

    conn = sqlite3.connect(_db_path)
    cursor = conn.cursor()

    # Main network quality metrics table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS network_quality (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            timestamp_unix REAL NOT NULL,

            -- Test configuration
            target_host TEXT NOT NULL,
            target_name TEXT,
            ping_count INTEGER,

            -- Packet loss metrics
            packets_sent INTEGER,
            packets_received INTEGER,
            packet_loss_percent REAL,

            -- Latency metrics (in milliseconds)
            latency_min REAL,
            latency_avg REAL,
            latency_max REAL,
            latency_stddev REAL,

            -- Jitter (latency variance) - calculated from individual pings
            jitter_ms REAL,

            -- Individual ping times (JSON array for detailed analysis)
            ping_times_json TEXT,

            -- Test metadata
            test_duration_seconds REAL,
            status TEXT,
            error_message TEXT,

            -- Analysis flags
            above_packet_loss_threshold INTEGER DEFAULT 0,
            above_jitter_threshold INTEGER DEFAULT 0,

            -- Time categorization for analysis
            hour_of_day INTEGER,
            day_of_week INTEGER,
            is_weekend INTEGER
        )
    ''')

    # Create indexes for efficient querying
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_network_quality_timestamp
        ON network_quality(timestamp_unix)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_network_quality_target
        ON network_quality(target_host)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_network_quality_status
        ON network_quality(status)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_network_quality_hour
        ON network_quality(hour_of_day)
    ''')

    # Aggregated hourly metrics for congestion analysis
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS network_quality_hourly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            hour INTEGER NOT NULL,
            day_of_week INTEGER NOT NULL,
            is_weekend INTEGER NOT NULL,
            target_host TEXT NOT NULL,

            -- Packet loss aggregates
            packet_loss_avg REAL,
            packet_loss_min REAL,
            packet_loss_max REAL,

            -- Latency aggregates
            latency_avg REAL,
            latency_min REAL,
            latency_max REAL,

            -- Jitter aggregates
            jitter_avg REAL,
            jitter_min REAL,
            jitter_max REAL,

            -- Sample count
            sample_count INTEGER,

            UNIQUE(date, hour, target_host)
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_network_quality_hourly_date
        ON network_quality_hourly(date, hour)
    ''')

    conn.commit()
    conn.close()
    print('[NETWORK_QUALITY] Database tables initialized')


def _load_config():
    """Load configuration from file."""
    global _config
    if not _config_file_path:
        return

    try:
        if os.path.exists(_config_file_path):
            with open(_config_file_path, 'r') as f:
                saved_config = json.load(f)
                with _config_lock:
                    _config.update(saved_config)
            print(f'[NETWORK_QUALITY] Loaded config from {_config_file_path}')
    except Exception as e:
        print(f'[NETWORK_QUALITY] Error loading config: {e}')


def _save_config():
    """Save configuration to file."""
    if not _config_file_path:
        return

    try:
        with _config_lock:
            config_to_save = dict(_config)
        with open(_config_file_path, 'w') as f:
            json.dump(config_to_save, f, indent=2)
    except Exception as e:
        print(f'[NETWORK_QUALITY] Error saving config: {e}')


def get_config() -> dict:
    """Get current monitor configuration."""
    with _config_lock:
        return dict(_config)


def update_config(updates: dict) -> dict:
    """Update monitor configuration.

    Args:
        updates: Dict of config keys to update

    Returns:
        Updated configuration
    """
    global _config

    with _config_lock:
        # Validate interval
        if 'interval_minutes' in updates:
            interval = updates['interval_minutes']
            min_interval = _config.get('min_interval_minutes', 1)
            max_interval = _config.get('max_interval_minutes', 60)
            updates['interval_minutes'] = max(min_interval, min(max_interval, interval))

        _config.update(updates)

    _save_config()

    # Handle start/stop outside the lock
    if updates.get('enabled') and not is_running():
        start_monitor()
    elif updates.get('enabled') is False and is_running():
        stop_monitor()

    return get_config()


def get_stats() -> dict:
    """Get monitoring statistics."""
    with _stats_lock:
        stats = dict(_stats)

    # Add runtime info
    stats['is_running'] = is_running()
    stats['last_test_time'] = _last_test_time
    stats['next_test_time'] = _next_test_time
    stats['next_test_in_seconds'] = max(0, _next_test_time - time.time()) if _next_test_time else None

    return stats


def _run_ping_test(host: str, count: int = 20, timeout: int = 5) -> Dict:
    """Run a ping test against a specific host.

    Args:
        host: Target host to ping
        count: Number of pings to send
        timeout: Timeout per ping in seconds

    Returns:
        Dict with test results
    """
    start_time = time.time()
    ping_times = []
    packets_sent = count
    packets_received = 0
    error_message = None

    # Determine ping command based on platform
    if sys.platform == 'win32':
        # Windows ping
        cmd = ['ping', '-n', str(count), '-w', str(timeout * 1000), host]
    else:
        # Unix/Linux/Mac ping
        cmd = ['ping', '-c', str(count), '-W', str(timeout), host]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=count * timeout + 10  # Total timeout with buffer
        )

        output = result.stdout

        if sys.platform == 'win32':
            # Parse Windows ping output
            # Example: "Reply from 8.8.8.8: bytes=32 time=15ms TTL=117"
            time_pattern = r'time[=<](\d+(?:\.\d+)?)\s*ms'
            matches = re.findall(time_pattern, output, re.IGNORECASE)
            ping_times = [float(m) for m in matches]
            packets_received = len(ping_times)

            # Check for packet loss line
            loss_pattern = r'\((\d+)%\s+loss\)'
            loss_match = re.search(loss_pattern, output)
            if loss_match:
                packet_loss_percent = float(loss_match.group(1))
            else:
                packet_loss_percent = ((packets_sent - packets_received) / packets_sent) * 100 if packets_sent > 0 else 100
        else:
            # Parse Unix ping output
            # Example: "64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=15.2 ms"
            time_pattern = r'time=(\d+(?:\.\d+)?)\s*ms'
            matches = re.findall(time_pattern, output)
            ping_times = [float(m) for m in matches]
            packets_received = len(ping_times)

            # Parse packet loss
            loss_pattern = r'(\d+(?:\.\d+)?)\s*%\s+packet\s+loss'
            loss_match = re.search(loss_pattern, output, re.IGNORECASE)
            if loss_match:
                packet_loss_percent = float(loss_match.group(1))
            else:
                packet_loss_percent = ((packets_sent - packets_received) / packets_sent) * 100 if packets_sent > 0 else 100

    except subprocess.TimeoutExpired:
        error_message = 'Ping test timed out'
        packet_loss_percent = 100
    except Exception as e:
        error_message = str(e)
        packet_loss_percent = 100

    # Calculate jitter (latency variation)
    # Jitter is typically calculated as the average of absolute differences
    # between consecutive ping times
    jitter_ms = 0
    if len(ping_times) >= 2:
        differences = [abs(ping_times[i] - ping_times[i-1]) for i in range(1, len(ping_times))]
        jitter_ms = statistics.mean(differences) if differences else 0

    # Calculate latency statistics
    latency_min = min(ping_times) if ping_times else None
    latency_avg = statistics.mean(ping_times) if ping_times else None
    latency_max = max(ping_times) if ping_times else None
    latency_stddev = statistics.stdev(ping_times) if len(ping_times) >= 2 else 0

    duration = time.time() - start_time

    return {
        'packets_sent': packets_sent,
        'packets_received': packets_received,
        'packet_loss_percent': round(packet_loss_percent, 2),
        'latency_min': round(latency_min, 2) if latency_min is not None else None,
        'latency_avg': round(latency_avg, 2) if latency_avg is not None else None,
        'latency_max': round(latency_max, 2) if latency_max is not None else None,
        'latency_stddev': round(latency_stddev, 2),
        'jitter_ms': round(jitter_ms, 2),
        'ping_times': ping_times,
        'test_duration_seconds': round(duration, 2),
        'status': 'success' if error_message is None else 'error',
        'error_message': error_message
    }


def run_network_quality_test() -> List[Dict]:
    """Run network quality tests against all configured targets.

    Returns:
        List of test results, one per target
    """
    with _config_lock:
        targets = _config.get('targets', DEFAULT_CONFIG['targets'])
        ping_count = _config.get('ping_count', 20)
        ping_timeout = _config.get('ping_timeout_seconds', 5)
        packet_loss_threshold = _config.get('packet_loss_threshold_percent', 5)
        jitter_threshold = _config.get('jitter_threshold_ms', 50)

    results = []
    test_time = datetime.now()
    test_time_unix = time.time()

    for target in targets:
        host = target.get('host')
        name = target.get('name', host)

        print(f'[NETWORK_QUALITY] Testing {name} ({host})...')

        test_result = _run_ping_test(host, ping_count, ping_timeout)

        # Determine time categorization
        hour_of_day = test_time.hour
        day_of_week = test_time.weekday()
        is_weekend = 1 if day_of_week >= 5 else 0

        # Check thresholds
        above_packet_loss = 1 if test_result['packet_loss_percent'] > packet_loss_threshold else 0
        above_jitter = 1 if test_result['jitter_ms'] > jitter_threshold else 0

        result = {
            'timestamp': test_time.isoformat(),
            'timestamp_unix': test_time_unix,
            'target_host': host,
            'target_name': name,
            'ping_count': ping_count,
            'packets_sent': test_result['packets_sent'],
            'packets_received': test_result['packets_received'],
            'packet_loss_percent': test_result['packet_loss_percent'],
            'latency_min': test_result['latency_min'],
            'latency_avg': test_result['latency_avg'],
            'latency_max': test_result['latency_max'],
            'latency_stddev': test_result['latency_stddev'],
            'jitter_ms': test_result['jitter_ms'],
            'ping_times': test_result['ping_times'],
            'test_duration_seconds': test_result['test_duration_seconds'],
            'status': test_result['status'],
            'error_message': test_result['error_message'],
            'above_packet_loss_threshold': above_packet_loss,
            'above_jitter_threshold': above_jitter,
            'hour_of_day': hour_of_day,
            'day_of_week': day_of_week,
            'is_weekend': is_weekend
        }

        results.append(result)

        if test_result['status'] == 'success':
            print(f'[NETWORK_QUALITY] {name}: {test_result["packet_loss_percent"]}% loss, '
                  f'{test_result["latency_avg"]}ms avg, {test_result["jitter_ms"]}ms jitter')
        else:
            print(f'[NETWORK_QUALITY] {name}: Error - {test_result["error_message"]}')

    # Save results to database
    _save_results_to_db(results)

    # Update stats
    _update_stats(results)

    return results


def _save_results_to_db(results: List[Dict]):
    """Save test results to database."""
    if not _db_path:
        return

    conn = sqlite3.connect(_db_path)
    cursor = conn.cursor()

    try:
        for result in results:
            cursor.execute('''
                INSERT INTO network_quality (
                    timestamp, timestamp_unix, target_host, target_name, ping_count,
                    packets_sent, packets_received, packet_loss_percent,
                    latency_min, latency_avg, latency_max, latency_stddev,
                    jitter_ms, ping_times_json, test_duration_seconds,
                    status, error_message, above_packet_loss_threshold,
                    above_jitter_threshold, hour_of_day, day_of_week, is_weekend
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                result['timestamp'],
                result['timestamp_unix'],
                result['target_host'],
                result['target_name'],
                result['ping_count'],
                result['packets_sent'],
                result['packets_received'],
                result['packet_loss_percent'],
                result['latency_min'],
                result['latency_avg'],
                result['latency_max'],
                result['latency_stddev'],
                result['jitter_ms'],
                json.dumps(result['ping_times']),
                result['test_duration_seconds'],
                result['status'],
                result['error_message'],
                result['above_packet_loss_threshold'],
                result['above_jitter_threshold'],
                result['hour_of_day'],
                result['day_of_week'],
                result['is_weekend']
            ))

        conn.commit()
    except Exception as e:
        print(f'[NETWORK_QUALITY] Database error: {e}')
    finally:
        conn.close()


def _update_stats(results: List[Dict]):
    """Update monitoring statistics."""
    global _stats

    with _stats_lock:
        successful_results = [r for r in results if r['status'] == 'success']
        failed_results = [r for r in results if r['status'] != 'success']

        _stats['tests_completed'] += len(successful_results)
        _stats['tests_failed'] += len(failed_results)

        if successful_results:
            # Calculate averages across all targets
            avg_loss = statistics.mean([r['packet_loss_percent'] for r in successful_results])
            avg_jitter = statistics.mean([r['jitter_ms'] for r in successful_results if r['jitter_ms'] is not None])
            max_loss = max([r['packet_loss_percent'] for r in successful_results])
            max_jitter = max([r['jitter_ms'] for r in successful_results if r['jitter_ms'] is not None], default=0)

            # Update running averages
            n = _stats['tests_completed']
            if n > 0:
                _stats['avg_packet_loss'] = round(
                    (_stats['avg_packet_loss'] * (n - len(successful_results)) + avg_loss * len(successful_results)) / n, 2
                )
                _stats['avg_jitter'] = round(
                    (_stats['avg_jitter'] * (n - len(successful_results)) + avg_jitter * len(successful_results)) / n, 2
                )

            # Update max values
            if _stats['max_packet_loss'] is None or max_loss > _stats['max_packet_loss']:
                _stats['max_packet_loss'] = max_loss
            if _stats['max_jitter'] is None or max_jitter > _stats['max_jitter']:
                _stats['max_jitter'] = max_jitter

            # Count threshold violations
            threshold_violations = sum(1 for r in successful_results
                                       if r['above_packet_loss_threshold'] or r['above_jitter_threshold'])
            _stats['tests_above_threshold'] += threshold_violations


def _monitor_loop():
    """Main monitoring loop."""
    global _next_test_time, _last_test_time, _stats

    with _stats_lock:
        _stats['monitor_started_at'] = datetime.now().isoformat()

    print('[NETWORK_QUALITY] Starting monitor loop')

    while not _stop_event.is_set():
        with _config_lock:
            interval_minutes = _config.get('interval_minutes', 5)
            enabled = _config.get('enabled', True)

        if not enabled:
            time.sleep(1)
            continue

        # Calculate next test time
        interval_seconds = interval_minutes * 60
        _next_test_time = _last_test_time + interval_seconds if _last_test_time else time.time()

        # Wait until next test time
        while not _stop_event.is_set():
            now = time.time()
            wait_time = _next_test_time - now

            if wait_time <= 0:
                break

            time.sleep(min(wait_time, 1))

        if _stop_event.is_set():
            break

        # Run the test
        _last_test_time = time.time()
        try:
            run_network_quality_test()
        except Exception as e:
            print(f'[NETWORK_QUALITY] Error running test: {e}')
            with _stats_lock:
                _stats['last_error'] = str(e)

    print('[NETWORK_QUALITY] Monitor loop stopped')


def start_monitor() -> bool:
    """Start the network quality monitor.

    Returns:
        True if started successfully
    """
    global _monitor_thread, _stop_event

    if is_running():
        print('[NETWORK_QUALITY] Already running')
        return False

    _stop_event.clear()
    _monitor_thread = threading.Thread(target=_monitor_loop, daemon=True)
    _monitor_thread.start()

    with _config_lock:
        _config['enabled'] = True
    _save_config()

    print('[NETWORK_QUALITY] Started')
    return True


def stop_monitor() -> bool:
    """Stop the network quality monitor.

    Returns:
        True if stopped successfully
    """
    global _monitor_thread

    if not is_running():
        print('[NETWORK_QUALITY] Already stopped')
        return False

    _stop_event.set()

    if _monitor_thread:
        _monitor_thread.join(timeout=5)
        _monitor_thread = None

    with _config_lock:
        _config['enabled'] = False
    _save_config()

    print('[NETWORK_QUALITY] Stopped')
    return True


def is_running() -> bool:
    """Check if monitor is running."""
    return _monitor_thread is not None and _monitor_thread.is_alive()


def trigger_test_now() -> List[Dict]:
    """Manually trigger an immediate network quality test.

    Returns:
        Test results
    """
    print('[NETWORK_QUALITY] Manual test triggered')
    return run_network_quality_test()


def get_history(limit: int = 100, offset: int = 0,
                target_filter: Optional[str] = None,
                status_filter: Optional[str] = None,
                hour_filter: Optional[int] = None) -> dict:
    """Get network quality test history from database.

    Args:
        limit: Max records to return
        offset: Records to skip
        target_filter: Filter by target host
        status_filter: Filter by status
        hour_filter: Filter by hour of day

    Returns:
        Dict with count and results
    """
    if not _db_path:
        return {'count': 0, 'results': [], 'error': 'Database not configured'}

    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # Build query
        where_clauses = []
        params = []

        if target_filter:
            where_clauses.append('target_host = ?')
            params.append(target_filter)

        if status_filter:
            where_clauses.append('status = ?')
            params.append(status_filter)

        if hour_filter is not None:
            where_clauses.append('hour_of_day = ?')
            params.append(hour_filter)

        where_sql = ' WHERE ' + ' AND '.join(where_clauses) if where_clauses else ''

        # Get total count
        cursor.execute(f'SELECT COUNT(*) FROM network_quality{where_sql}', params)
        total_count = cursor.fetchone()[0]

        # Get results
        cursor.execute(f'''
            SELECT * FROM network_quality
            {where_sql}
            ORDER BY timestamp_unix DESC
            LIMIT ? OFFSET ?
        ''', params + [limit, offset])

        results = []
        for row in cursor.fetchall():
            result = dict(row)
            # Parse JSON ping times
            if result.get('ping_times_json'):
                result['ping_times'] = json.loads(result['ping_times_json'])
            results.append(result)

        return {
            'total_count': total_count,
            'count': len(results),
            'offset': offset,
            'limit': limit,
            'results': results
        }
    except Exception as e:
        return {'count': 0, 'results': [], 'error': str(e)}
    finally:
        conn.close()


def get_hourly_stats(target_host: Optional[str] = None) -> dict:
    """Get aggregated statistics by hour of day.

    Returns:
        Dict with hourly breakdowns useful for FCC evidence
    """
    if not _db_path:
        return {'error': 'Database not configured'}

    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        target_clause = 'AND target_host = ?' if target_host else ''
        params = [target_host] if target_host else []

        cursor.execute(f'''
            SELECT
                hour_of_day,
                COUNT(*) as test_count,
                AVG(packet_loss_percent) as avg_packet_loss,
                MIN(packet_loss_percent) as min_packet_loss,
                MAX(packet_loss_percent) as max_packet_loss,
                AVG(jitter_ms) as avg_jitter,
                MIN(jitter_ms) as min_jitter,
                MAX(jitter_ms) as max_jitter,
                AVG(latency_avg) as avg_latency,
                SUM(above_packet_loss_threshold) as above_loss_threshold_count,
                SUM(above_jitter_threshold) as above_jitter_threshold_count
            FROM network_quality
            WHERE status = 'success' {target_clause}
            GROUP BY hour_of_day
            ORDER BY hour_of_day
        ''', params)

        hourly_data = [dict(row) for row in cursor.fetchall()]

        # Calculate overall stats
        cursor.execute(f'''
            SELECT
                COUNT(*) as total_tests,
                AVG(packet_loss_percent) as overall_avg_packet_loss,
                AVG(jitter_ms) as overall_avg_jitter,
                SUM(above_packet_loss_threshold) as total_above_loss_threshold,
                SUM(above_jitter_threshold) as total_above_jitter_threshold
            FROM network_quality
            WHERE status = 'success' {target_clause}
        ''', params)
        overall = dict(cursor.fetchone())

        # Find worst hours
        if hourly_data:
            worst_packet_loss_hour = max(hourly_data, key=lambda x: x['avg_packet_loss'] or 0)
            worst_jitter_hour = max(hourly_data, key=lambda x: x['avg_jitter'] or 0)
        else:
            worst_packet_loss_hour = worst_jitter_hour = None

        return {
            'hourly_breakdown': hourly_data,
            'overall': overall,
            'worst_packet_loss_hour': worst_packet_loss_hour,
            'worst_jitter_hour': worst_jitter_hour,
            'evidence_summary': {
                'total_tests': overall['total_tests'] if overall else 0,
                'above_loss_threshold_count': overall['total_above_loss_threshold'] if overall else 0,
                'above_jitter_threshold_count': overall['total_above_jitter_threshold'] if overall else 0,
            }
        }
    except Exception as e:
        return {'error': str(e)}
    finally:
        conn.close()


def get_evidence_summary() -> dict:
    """Generate summary data for FCC complaint evidence.

    Returns:
        Comprehensive evidence summary for network quality
    """
    if not _db_path:
        return {'error': 'Database not configured'}

    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # Overall statistics
        cursor.execute('''
            SELECT
                COUNT(*) as total_tests,
                MIN(timestamp_unix) as first_test,
                MAX(timestamp_unix) as last_test,
                AVG(packet_loss_percent) as avg_packet_loss,
                MIN(packet_loss_percent) as min_packet_loss,
                MAX(packet_loss_percent) as max_packet_loss,
                AVG(jitter_ms) as avg_jitter,
                MIN(jitter_ms) as min_jitter,
                MAX(jitter_ms) as max_jitter,
                AVG(latency_avg) as avg_latency,
                MIN(latency_min) as min_latency,
                MAX(latency_max) as max_latency,
                SUM(above_packet_loss_threshold) as above_loss_threshold_count,
                SUM(above_jitter_threshold) as above_jitter_threshold_count
            FROM network_quality
            WHERE status = 'success'
        ''')
        overall = dict(cursor.fetchone())

        # Per-target statistics
        cursor.execute('''
            SELECT
                target_host,
                target_name,
                COUNT(*) as test_count,
                AVG(packet_loss_percent) as avg_packet_loss,
                MAX(packet_loss_percent) as max_packet_loss,
                AVG(jitter_ms) as avg_jitter,
                MAX(jitter_ms) as max_jitter,
                AVG(latency_avg) as avg_latency
            FROM network_quality
            WHERE status = 'success'
            GROUP BY target_host
        ''')
        by_target = [dict(row) for row in cursor.fetchall()]

        # Tests by day of week
        cursor.execute('''
            SELECT
                day_of_week,
                COUNT(*) as test_count,
                AVG(packet_loss_percent) as avg_packet_loss,
                AVG(jitter_ms) as avg_jitter,
                SUM(above_packet_loss_threshold) as above_threshold
            FROM network_quality
            WHERE status = 'success'
            GROUP BY day_of_week
            ORDER BY day_of_week
        ''')
        by_day = [dict(row) for row in cursor.fetchall()]

        # Calculate collection period
        collection_days = 0
        if overall['first_test'] and overall['last_test']:
            collection_days = (overall['last_test'] - overall['first_test']) / 86400

        # Quality assessment
        avg_loss = overall['avg_packet_loss'] or 0
        avg_jitter = overall['avg_jitter'] or 0

        quality_issues = []
        if avg_loss > 1:
            quality_issues.append(f'Average packet loss of {avg_loss:.2f}% indicates unreliable connection')
        if avg_jitter > 20:
            quality_issues.append(f'Average jitter of {avg_jitter:.1f}ms may impact real-time applications')
        if overall['max_packet_loss'] and overall['max_packet_loss'] > 10:
            quality_issues.append(f'Maximum packet loss of {overall["max_packet_loss"]:.1f}% indicates severe connectivity issues')

        return {
            'collection_period': {
                'days': round(collection_days, 1),
                'first_test': datetime.fromtimestamp(overall['first_test']).isoformat() if overall['first_test'] else None,
                'last_test': datetime.fromtimestamp(overall['last_test']).isoformat() if overall['last_test'] else None,
                'total_tests': overall['total_tests'] or 0
            },
            'packet_loss_metrics': {
                'average_percent': round(avg_loss, 2),
                'min_percent': round(overall['min_packet_loss'] or 0, 2),
                'max_percent': round(overall['max_packet_loss'] or 0, 2),
                'tests_above_threshold': overall['above_loss_threshold_count'] or 0,
                'violation_rate_percent': round(
                    (overall['above_loss_threshold_count'] or 0) / max(1, overall['total_tests'] or 1) * 100, 1
                )
            },
            'jitter_metrics': {
                'average_ms': round(avg_jitter, 2),
                'min_ms': round(overall['min_jitter'] or 0, 2),
                'max_ms': round(overall['max_jitter'] or 0, 2),
                'tests_above_threshold': overall['above_jitter_threshold_count'] or 0,
                'violation_rate_percent': round(
                    (overall['above_jitter_threshold_count'] or 0) / max(1, overall['total_tests'] or 1) * 100, 1
                )
            },
            'latency_metrics': {
                'average_ms': round(overall['avg_latency'] or 0, 2),
                'min_ms': round(overall['min_latency'] or 0, 2),
                'max_ms': round(overall['max_latency'] or 0, 2)
            },
            'by_target': by_target,
            'by_day_of_week': by_day,
            'quality_issues': quality_issues,
            'fcc_evidence_note': (
                'High packet loss and jitter indicate unreliable network performance that affects '
                'real-time applications like video calls, VoIP, and gaming. These metrics demonstrate '
                'service quality issues beyond raw throughput measurements.'
            )
        }
    except Exception as e:
        return {'error': str(e)}
    finally:
        conn.close()


def get_latest_results() -> List[Dict]:
    """Get the most recent test results (one per target).

    Returns:
        List of latest results per target
    """
    if not _db_path:
        return []

    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cursor.execute('''
            SELECT * FROM network_quality
            WHERE id IN (
                SELECT MAX(id) FROM network_quality
                GROUP BY target_host
            )
            ORDER BY timestamp_unix DESC
        ''')

        results = []
        for row in cursor.fetchall():
            result = dict(row)
            if result.get('ping_times_json'):
                result['ping_times'] = json.loads(result['ping_times_json'])
            results.append(result)

        return results
    except Exception as e:
        print(f'[NETWORK_QUALITY] Error getting latest results: {e}')
        return []
    finally:
        conn.close()
