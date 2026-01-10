"""
Automated Scheduled Speed Test Module for T-Mobile Dashboard

This module provides automated speed testing capabilities for collecting
evidence for FCC complaints. It runs speed tests at configurable intervals
and logs results with signal snapshots.

Features:
- Configurable test intervals (default 30 minutes)
- Server rotation for validation
- Statistics tracking
- Persistent configuration
- API for control and status

Evidence Goal:
Demonstrate consistent <10 Mbps speeds against advertised 133-415 Mbps range
over a minimum 30-day collection period.
"""

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Callable


# Configuration defaults
DEFAULT_CONFIG = {
    'enabled': False,
    'interval_minutes': 30,  # How often to run tests
    'min_interval_minutes': 5,  # Minimum allowed interval
    'max_interval_minutes': 1440,  # Maximum interval (24 hours)
    'start_hour': 0,  # Start of testing window (0-23)
    'end_hour': 24,  # End of testing window (24 = all day)
    'test_on_weekends': True,
    'rotate_servers': False,  # Use different servers for each test
    'max_concurrent_tests': 1,
    'retry_on_failure': True,
    'retry_delay_seconds': 60,
    'max_retries': 2,
    'notify_on_threshold': True,
    'low_speed_threshold_mbps': 10,  # Alert when below this speed
    'collection_start_date': None,  # When evidence collection started
    'target_days': 30,  # Goal: collect 30 days of evidence
}

# Module state
_config = dict(DEFAULT_CONFIG)
_scheduler_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_last_test_time = 0
_next_test_time = 0
_stats = {
    'tests_completed': 0,
    'tests_failed': 0,
    'tests_below_threshold': 0,
    'avg_download_mbps': 0,
    'avg_upload_mbps': 0,
    'min_download_mbps': None,
    'max_download_mbps': None,
    'scheduler_started_at': None,
    'last_error': None
}
_config_lock = threading.Lock()
_stats_lock = threading.Lock()

# Will be set by server.py
_run_speedtest_func: Optional[Callable] = None
_db_path: Optional[str] = None
_config_file_path: Optional[str] = None


def init_scheduler(db_path: str, config_file_path: str, run_speedtest_func: Callable):
    """Initialize the scheduler module with required dependencies.

    Args:
        db_path: Path to SQLite database
        config_file_path: Path to save scheduler config
        run_speedtest_func: Function to call to run a speed test
    """
    global _db_path, _config_file_path, _run_speedtest_func
    _db_path = db_path
    _config_file_path = config_file_path
    _run_speedtest_func = run_speedtest_func

    # Initialize database table
    _init_database_table()

    # Load saved configuration
    _load_config()

    # Auto-start if was enabled
    if _config.get('enabled'):
        start_scheduler()

    print(f'[SCHEDULER] Initialized - interval: {_config["interval_minutes"]}min, enabled: {_config["enabled"]}')


def _init_database_table():
    """Create the scheduled_speedtests table if it doesn't exist."""
    if not _db_path:
        return

    conn = sqlite3.connect(_db_path)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scheduled_speedtests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            timestamp_unix REAL NOT NULL,
            scheduled_time REAL,

            -- Speed test results
            download_mbps REAL,
            upload_mbps REAL,
            ping_ms REAL,
            jitter_ms REAL,

            -- Server info
            server_name TEXT,
            server_location TEXT,
            server_host TEXT,

            -- Client info
            client_ip TEXT,
            client_isp TEXT,

            -- Signal at test time
            nr_sinr REAL,
            nr_rsrp REAL,
            nr_rsrq REAL,
            nr_bands TEXT,
            nr_gnb_id INTEGER,
            lte_sinr REAL,
            lte_rsrp REAL,
            lte_rsrq REAL,
            lte_bands TEXT,
            lte_enb_id INTEGER,

            -- Test metadata
            duration_seconds REAL,
            tool TEXT,
            status TEXT,
            error_message TEXT,
            retry_count INTEGER DEFAULT 0,

            -- Analysis flags
            below_threshold INTEGER DEFAULT 0,
            hour_of_day INTEGER,
            day_of_week INTEGER,
            is_weekend INTEGER
        )
    ''')

    # Create indexes for analysis queries
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_scheduled_timestamp
        ON scheduled_speedtests(timestamp_unix)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_scheduled_hour
        ON scheduled_speedtests(hour_of_day)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_scheduled_status
        ON scheduled_speedtests(status)
    ''')

    conn.commit()
    conn.close()
    print('[SCHEDULER] Database table initialized')


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
            print(f'[SCHEDULER] Loaded config from {_config_file_path}')
    except Exception as e:
        print(f'[SCHEDULER] Error loading config: {e}')


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
        print(f'[SCHEDULER] Error saving config: {e}')


def get_config() -> dict:
    """Get current scheduler configuration."""
    with _config_lock:
        return dict(_config)


def update_config(updates: dict) -> dict:
    """Update scheduler configuration.

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
            min_interval = _config.get('min_interval_minutes', 5)
            max_interval = _config.get('max_interval_minutes', 1440)
            updates['interval_minutes'] = max(min_interval, min(max_interval, interval))

        # Validate hours
        if 'start_hour' in updates:
            updates['start_hour'] = max(0, min(23, updates['start_hour']))
        if 'end_hour' in updates:
            updates['end_hour'] = max(1, min(24, updates['end_hour']))

        _config.update(updates)

        # Handle enabled state change
        if 'enabled' in updates:
            if updates['enabled'] and not is_running():
                # Will start after releasing lock
                pass
            elif not updates['enabled'] and is_running():
                # Will stop after releasing lock
                pass

    _save_config()

    # Handle start/stop outside the lock
    if updates.get('enabled') and not is_running():
        start_scheduler()
    elif updates.get('enabled') is False and is_running():
        stop_scheduler()

    return get_config()


def get_stats() -> dict:
    """Get scheduler statistics."""
    with _stats_lock:
        stats = dict(_stats)

    # Add runtime info
    stats['is_running'] = is_running()
    stats['last_test_time'] = _last_test_time
    stats['next_test_time'] = _next_test_time
    stats['next_test_in_seconds'] = max(0, _next_test_time - time.time()) if _next_test_time else None

    # Add collection progress
    with _config_lock:
        collection_start = _config.get('collection_start_date')
        target_days = _config.get('target_days', 30)

    if collection_start:
        try:
            start_date = datetime.fromisoformat(collection_start)
            days_collected = (datetime.now() - start_date).days
            stats['collection_days'] = days_collected
            stats['collection_target_days'] = target_days
            stats['collection_progress_percent'] = min(100, round(days_collected / target_days * 100, 1))
        except Exception:
            pass

    return stats


def _update_stats(result: dict):
    """Update statistics after a test."""
    global _stats

    with _stats_lock:
        if result.get('status') == 'success':
            _stats['tests_completed'] += 1

            download = result.get('download_mbps', 0)
            upload = result.get('upload_mbps', 0)

            # Update running averages
            n = _stats['tests_completed']
            _stats['avg_download_mbps'] = round(
                (_stats['avg_download_mbps'] * (n - 1) + download) / n, 2
            )
            _stats['avg_upload_mbps'] = round(
                (_stats['avg_upload_mbps'] * (n - 1) + upload) / n, 2
            )

            # Update min/max
            if _stats['min_download_mbps'] is None or download < _stats['min_download_mbps']:
                _stats['min_download_mbps'] = download
            if _stats['max_download_mbps'] is None or download > _stats['max_download_mbps']:
                _stats['max_download_mbps'] = download

            # Check threshold
            with _config_lock:
                threshold = _config.get('low_speed_threshold_mbps', 10)
            if download < threshold:
                _stats['tests_below_threshold'] += 1
        else:
            _stats['tests_failed'] += 1
            _stats['last_error'] = result.get('error', 'Unknown error')


def _save_to_database(result: dict, scheduled_time: float, retry_count: int = 0):
    """Save speed test result to database."""
    if not _db_path:
        return

    conn = sqlite3.connect(_db_path)
    cursor = conn.cursor()

    try:
        # Extract signal data
        signal = result.get('signal_at_test', {})
        s5g = signal.get('5g', {})
        s4g = signal.get('4g', {})

        # Determine time metadata
        test_time = datetime.fromtimestamp(result.get('timestamp_unix', time.time()))
        hour_of_day = test_time.hour
        day_of_week = test_time.weekday()  # 0=Monday, 6=Sunday
        is_weekend = 1 if day_of_week >= 5 else 0

        # Check if below threshold
        with _config_lock:
            threshold = _config.get('low_speed_threshold_mbps', 10)
        below_threshold = 1 if result.get('download_mbps', 0) < threshold else 0

        cursor.execute('''
            INSERT INTO scheduled_speedtests (
                timestamp, timestamp_unix, scheduled_time,
                download_mbps, upload_mbps, ping_ms, jitter_ms,
                server_name, server_location, server_host,
                client_ip, client_isp,
                nr_sinr, nr_rsrp, nr_rsrq, nr_bands, nr_gnb_id,
                lte_sinr, lte_rsrp, lte_rsrq, lte_bands, lte_enb_id,
                duration_seconds, tool, status, error_message, retry_count,
                below_threshold, hour_of_day, day_of_week, is_weekend
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            result.get('timestamp'),
            result.get('timestamp_unix'),
            scheduled_time,
            result.get('download_mbps'),
            result.get('upload_mbps'),
            result.get('ping_ms'),
            result.get('jitter_ms'),
            result.get('server', {}).get('name'),
            result.get('server', {}).get('location'),
            result.get('server', {}).get('host'),
            result.get('client', {}).get('ip'),
            result.get('client', {}).get('isp'),
            s5g.get('sinr'),
            s5g.get('rsrp'),
            s5g.get('rsrq'),
            ','.join(map(str, s5g.get('bands', []))),
            s5g.get('gNBID'),
            s4g.get('sinr'),
            s4g.get('rsrp'),
            s4g.get('rsrq'),
            ','.join(map(str, s4g.get('bands', []))),
            s4g.get('eNBID'),
            result.get('duration_seconds'),
            result.get('tool'),
            result.get('status'),
            result.get('error'),
            retry_count,
            below_threshold,
            hour_of_day,
            day_of_week,
            is_weekend
        ))

        conn.commit()
    except Exception as e:
        print(f'[SCHEDULER] Database error: {e}')
    finally:
        conn.close()


def _is_in_test_window() -> bool:
    """Check if current time is within the configured testing window."""
    with _config_lock:
        start_hour = _config.get('start_hour', 0)
        end_hour = _config.get('end_hour', 24)
        test_weekends = _config.get('test_on_weekends', True)

    now = datetime.now()
    current_hour = now.hour

    # Check weekend setting
    if not test_weekends and now.weekday() >= 5:
        return False

    # Check hour window
    if end_hour > start_hour:
        # Normal window (e.g., 8-22)
        return start_hour <= current_hour < end_hour
    else:
        # Overnight window (e.g., 22-6)
        return current_hour >= start_hour or current_hour < end_hour


def _run_scheduled_test(scheduled_time: float):
    """Run a scheduled speed test with retry logic."""
    global _last_test_time

    with _config_lock:
        retry_enabled = _config.get('retry_on_failure', True)
        retry_delay = _config.get('retry_delay_seconds', 60)
        max_retries = _config.get('max_retries', 2)

    retry_count = 0
    result = None

    while retry_count <= max_retries:
        if _stop_event.is_set():
            return

        print(f'[SCHEDULER] Running scheduled test (attempt {retry_count + 1})')

        if _run_speedtest_func:
            result = _run_speedtest_func()
        else:
            result = {'status': 'error', 'error': 'Speed test function not configured'}

        _last_test_time = time.time()

        if result.get('status') == 'success':
            break
        elif retry_enabled and retry_count < max_retries:
            print(f'[SCHEDULER] Test failed, retrying in {retry_delay}s: {result.get("error")}')
            retry_count += 1

            # Wait for retry, but check for stop signal
            for _ in range(retry_delay):
                if _stop_event.is_set():
                    return
                time.sleep(1)
        else:
            break

    # Update stats and save to database
    _update_stats(result)
    _save_to_database(result, scheduled_time, retry_count)

    # Log result
    if result.get('status') == 'success':
        print(f'[SCHEDULER] Test complete: {result.get("download_mbps")} Mbps down, '
              f'{result.get("upload_mbps")} Mbps up')
    else:
        print(f'[SCHEDULER] Test failed after {retry_count + 1} attempts: {result.get("error")}')


def _scheduler_loop():
    """Main scheduler loop."""
    global _next_test_time, _stats

    with _stats_lock:
        _stats['scheduler_started_at'] = datetime.now().isoformat()

    # Set collection start date if not set
    with _config_lock:
        if not _config.get('collection_start_date'):
            _config['collection_start_date'] = datetime.now().isoformat()
            _save_config()

    print('[SCHEDULER] Starting scheduler loop')

    while not _stop_event.is_set():
        with _config_lock:
            interval_minutes = _config.get('interval_minutes', 30)
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

            # Sleep in small increments to check for stop signal and config changes
            time.sleep(min(wait_time, 1))

        if _stop_event.is_set():
            break

        # Check if in test window
        if not _is_in_test_window():
            print('[SCHEDULER] Outside test window, skipping')
            _last_test_time = time.time()  # Update to prevent immediate retry
            continue

        # Run the test
        _run_scheduled_test(_next_test_time)

    print('[SCHEDULER] Scheduler loop stopped')


def start_scheduler() -> bool:
    """Start the scheduler.

    Returns:
        True if started successfully
    """
    global _scheduler_thread, _stop_event

    if is_running():
        print('[SCHEDULER] Already running')
        return False

    _stop_event.clear()
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler_thread.start()

    with _config_lock:
        _config['enabled'] = True
    _save_config()

    print('[SCHEDULER] Started')
    return True


def stop_scheduler() -> bool:
    """Stop the scheduler.

    Returns:
        True if stopped successfully
    """
    global _scheduler_thread

    if not is_running():
        print('[SCHEDULER] Already stopped')
        return False

    _stop_event.set()

    if _scheduler_thread:
        _scheduler_thread.join(timeout=5)
        _scheduler_thread = None

    with _config_lock:
        _config['enabled'] = False
    _save_config()

    print('[SCHEDULER] Stopped')
    return True


def is_running() -> bool:
    """Check if scheduler is running."""
    return _scheduler_thread is not None and _scheduler_thread.is_alive()


def trigger_test_now() -> dict:
    """Manually trigger an immediate scheduled test.

    Returns:
        Test result
    """
    if not _run_speedtest_func:
        return {'status': 'error', 'error': 'Speed test function not configured'}

    print('[SCHEDULER] Manual test triggered')
    result = _run_speedtest_func()

    # Still save to database and update stats
    _update_stats(result)
    _save_to_database(result, time.time())

    return result


def get_scheduled_history(limit: int = 100, offset: int = 0,
                          status_filter: Optional[str] = None,
                          hour_filter: Optional[int] = None) -> dict:
    """Get scheduled test history from database.

    Args:
        limit: Max records to return
        offset: Records to skip
        status_filter: Filter by status ('success', 'error', etc.)
        hour_filter: Filter by hour of day (0-23)

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

        if status_filter:
            where_clauses.append('status = ?')
            params.append(status_filter)

        if hour_filter is not None:
            where_clauses.append('hour_of_day = ?')
            params.append(hour_filter)

        where_sql = ' WHERE ' + ' AND '.join(where_clauses) if where_clauses else ''

        # Get total count
        cursor.execute(f'SELECT COUNT(*) FROM scheduled_speedtests{where_sql}', params)
        total_count = cursor.fetchone()[0]

        # Get results
        cursor.execute(f'''
            SELECT * FROM scheduled_speedtests
            {where_sql}
            ORDER BY timestamp_unix DESC
            LIMIT ? OFFSET ?
        ''', params + [limit, offset])

        results = [dict(row) for row in cursor.fetchall()]

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


def get_hourly_stats() -> dict:
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
        cursor.execute('''
            SELECT
                hour_of_day,
                COUNT(*) as test_count,
                AVG(download_mbps) as avg_download,
                MIN(download_mbps) as min_download,
                MAX(download_mbps) as max_download,
                AVG(upload_mbps) as avg_upload,
                AVG(ping_ms) as avg_ping,
                SUM(below_threshold) as below_threshold_count,
                AVG(nr_sinr) as avg_nr_sinr,
                AVG(nr_rsrp) as avg_nr_rsrp
            FROM scheduled_speedtests
            WHERE status = 'success'
            GROUP BY hour_of_day
            ORDER BY hour_of_day
        ''')

        hourly_data = [dict(row) for row in cursor.fetchall()]

        # Calculate overall stats
        cursor.execute('''
            SELECT
                COUNT(*) as total_tests,
                AVG(download_mbps) as overall_avg_download,
                SUM(below_threshold) as total_below_threshold
            FROM scheduled_speedtests
            WHERE status = 'success'
        ''')
        overall = dict(cursor.fetchone())

        # Find worst and best hours
        if hourly_data:
            worst_hour = min(hourly_data, key=lambda x: x['avg_download'] or float('inf'))
            best_hour = max(hourly_data, key=lambda x: x['avg_download'] or 0)
        else:
            worst_hour = best_hour = None

        return {
            'hourly_breakdown': hourly_data,
            'overall': overall,
            'worst_hour': worst_hour,
            'best_hour': best_hour,
            'evidence_summary': {
                'total_tests': overall['total_tests'] if overall else 0,
                'below_threshold_count': overall['total_below_threshold'] if overall else 0,
                'below_threshold_percent': round(
                    (overall['total_below_threshold'] or 0) / max(1, overall['total_tests'] or 1) * 100, 1
                ) if overall else 0
            }
        }
    except Exception as e:
        return {'error': str(e)}
    finally:
        conn.close()


def get_evidence_summary() -> dict:
    """Generate summary data for FCC complaint evidence.

    Returns:
        Comprehensive evidence summary
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
                AVG(download_mbps) as avg_download,
                MIN(download_mbps) as min_download,
                MAX(download_mbps) as max_download,
                AVG(upload_mbps) as avg_upload,
                AVG(ping_ms) as avg_ping,
                SUM(below_threshold) as below_threshold_count,
                AVG(nr_sinr) as avg_signal_sinr,
                AVG(nr_rsrp) as avg_signal_rsrp
            FROM scheduled_speedtests
            WHERE status = 'success'
        ''')
        overall = dict(cursor.fetchone())

        # Tests by day of week
        cursor.execute('''
            SELECT
                day_of_week,
                COUNT(*) as test_count,
                AVG(download_mbps) as avg_download,
                SUM(below_threshold) as below_threshold
            FROM scheduled_speedtests
            WHERE status = 'success'
            GROUP BY day_of_week
            ORDER BY day_of_week
        ''')
        by_day = [dict(row) for row in cursor.fetchall()]

        # Weekday vs weekend
        cursor.execute('''
            SELECT
                is_weekend,
                COUNT(*) as test_count,
                AVG(download_mbps) as avg_download
            FROM scheduled_speedtests
            WHERE status = 'success'
            GROUP BY is_weekend
        ''')
        weekday_weekend = {row['is_weekend']: dict(row) for row in cursor.fetchall()}

        # Calculate collection period
        collection_days = 0
        if overall['first_test'] and overall['last_test']:
            collection_days = (overall['last_test'] - overall['first_test']) / 86400

        # Advertised vs actual speeds (user reported: 133-415 Mbps advertised)
        advertised_min = 133
        advertised_max = 415
        actual_avg = overall['avg_download'] or 0

        return {
            'collection_period': {
                'days': round(collection_days, 1),
                'first_test': datetime.fromtimestamp(overall['first_test']).isoformat() if overall['first_test'] else None,
                'last_test': datetime.fromtimestamp(overall['last_test']).isoformat() if overall['last_test'] else None,
                'total_tests': overall['total_tests'] or 0
            },
            'speed_metrics': {
                'average_download_mbps': round(actual_avg, 2),
                'min_download_mbps': round(overall['min_download'] or 0, 2),
                'max_download_mbps': round(overall['max_download'] or 0, 2),
                'average_upload_mbps': round(overall['avg_upload'] or 0, 2),
                'average_ping_ms': round(overall['avg_ping'] or 0, 1)
            },
            'threshold_violations': {
                'tests_below_10mbps': overall['below_threshold_count'] or 0,
                'violation_rate_percent': round(
                    (overall['below_threshold_count'] or 0) / max(1, overall['total_tests'] or 1) * 100, 1
                )
            },
            'advertised_vs_actual': {
                'advertised_range_mbps': f'{advertised_min}-{advertised_max}',
                'actual_average_mbps': round(actual_avg, 2),
                'percent_of_min_advertised': round(actual_avg / advertised_min * 100, 1) if actual_avg else 0,
                'shortfall_mbps': round(advertised_min - actual_avg, 2) if actual_avg < advertised_min else 0
            },
            'signal_quality': {
                'avg_5g_sinr_db': round(overall['avg_signal_sinr'] or 0, 1),
                'avg_5g_rsrp_dbm': round(overall['avg_signal_rsrp'] or 0, 1),
                'signal_quality_note': 'Good signal with poor speeds suggests network congestion'
                    if (overall['avg_signal_sinr'] or 0) > 10 and actual_avg < advertised_min * 0.25
                    else 'Signal quality correlates with speed issues'
            },
            'by_day_of_week': by_day,
            'weekday_vs_weekend': {
                'weekday': weekday_weekend.get(0, {}),
                'weekend': weekday_weekend.get(1, {})
            }
        }
    except Exception as e:
        return {'error': str(e)}
    finally:
        conn.close()
