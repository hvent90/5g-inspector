from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
import urllib.request
from urllib.parse import urlparse, parse_qs
import json
import socket
import os
import threading
import time
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from collections import deque
import math

# Import diagnostic reports module
import diagnostic_reports

# Import congestion analysis module
import congestion

# ============================================
# Disruption Detection Configuration
# ============================================
DISRUPTION_THRESHOLDS = {
    # Signal drop detection (dB drop in SINR)
    'sinr_drop_5g': 10,  # 10 dB drop in 5G SINR triggers alert
    'sinr_drop_4g': 8,   # 8 dB drop in 4G SINR triggers alert
    'rsrp_drop_5g': 10,  # 10 dBm drop in 5G RSRP
    'rsrp_drop_4g': 10,  # 10 dBm drop in 4G RSRP

    # Critical thresholds (absolute values)
    'sinr_critical_5g': 0,    # 5G SINR below 0 dB is critical
    'sinr_critical_4g': 0,    # 4G SINR below 0 dB is critical
    'rsrp_critical_5g': -110, # 5G RSRP below -110 dBm is critical
    'rsrp_critical_4g': -115, # 4G RSRP below -115 dBm is critical

    # Detection window (seconds)
    'detection_window': 30,   # Look for drops within 30 seconds
    'cooldown_period': 60,    # Don't report same event type within 60 seconds
}

# Track previous signal values for disruption detection
last_signal_state = {
    '5g': {'sinr': None, 'rsrp': None, 'gnb_id': None, 'cid': None},
    '4g': {'sinr': None, 'rsrp': None, 'enb_id': None, 'cid': None},
    'registration': None,
    'timestamp': 0
}
last_signal_lock = threading.Lock()

# Disruption event cooldowns
disruption_cooldowns = {}
disruption_cooldown_lock = threading.Lock()

# Import scheduler module for automated speed tests
import scheduler

# Import FCC complaint report generator
import fcc_complaint_report

# Import alerting system
import alerting

# Import service terms documentation
import service_terms

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return 'unknown'

# Cached signal data with stats
cache = {
    'data': None,
    'last_success': 0,
    'last_attempt': 0,
    'success_count': 0,
    'error_count': 0,
    'last_error': None
}
cache_lock = threading.Lock()

def get_signal_data_for_alerting():
    """Return current signal data and last success time for alerting system."""
    with cache_lock:
        return cache['data'], cache['last_success']

def get_speedtest_results_for_alerting():
    """Return recent speedtest results for alerting system."""
    with speedtest_results_lock:
        return speedtest_results[-1] if speedtest_results else None

GATEWAY_POLL_INTERVAL = 0.2  # 200ms
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'signal_history.db')
DB_BATCH_INTERVAL = 5  # seconds between batch inserts
DB_RETENTION_DAYS = 30  # how long to keep data
SUPPORT_INTERACTIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'support_interactions.json')
SCHEDULER_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scheduler_config.json')

# Buffer for batch inserts
db_buffer = deque(maxlen=1000)
db_buffer_lock = threading.Lock()

# Speed test state
speedtest_results = []  # In-memory storage for speed test results
speedtest_results_lock = threading.Lock()
speedtest_running = False
speedtest_running_lock = threading.Lock()
SPEEDTEST_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'speedtest_history.json')

# Support interaction state
support_interactions = []
support_interactions_lock = threading.Lock()

def load_speedtest_history():
    """Load speed test history from file"""
    global speedtest_results
    try:
        if os.path.exists(SPEEDTEST_HISTORY_FILE):
            with open(SPEEDTEST_HISTORY_FILE, 'r') as f:
                speedtest_results = json.load(f)
                print(f'[SPEEDTEST] Loaded {len(speedtest_results)} historical results')
    except Exception as e:
        print(f'[SPEEDTEST] Error loading history: {e}')
        speedtest_results = []

def load_support_interactions():
    """Load support interactions from file"""
    global support_interactions
    try:
        if os.path.exists(SUPPORT_INTERACTIONS_FILE):
            with open(SUPPORT_INTERACTIONS_FILE, 'r') as f:
                support_interactions = json.load(f)
                print(f'[SUPPORT] Loaded {len(support_interactions)} support interactions')
    except Exception as e:
        print(f'[SUPPORT] Error loading interactions: {e}')
        support_interactions = []

def save_support_interactions():
    """Save support interactions to file"""
    try:
        with support_interactions_lock:
            with open(SUPPORT_INTERACTIONS_FILE, 'w') as f:
                json.dump(support_interactions, f, indent=2)
    except Exception as e:
        print(f'[SUPPORT] Error saving interactions: {e}')

def add_support_interaction(interaction_data):
    """Add a new support interaction"""
    interaction = {
        'id': str(int(time.time() * 1000)),  # Unique ID based on timestamp
        'created_at': datetime.utcnow().isoformat(),
        'contact_date': interaction_data.get('contact_date'),
        'contact_time': interaction_data.get('contact_time'),
        'contact_method': interaction_data.get('contact_method', 'phone'),
        'agent_name': interaction_data.get('agent_name', ''),
        'agent_id': interaction_data.get('agent_id', ''),
        'ticket_number': interaction_data.get('ticket_number', ''),
        'complaint_summary': interaction_data.get('complaint_summary', ''),
        'response_received': interaction_data.get('response_received', ''),
        'resolution_offered': interaction_data.get('resolution_offered', ''),
        'resolution_status': interaction_data.get('resolution_status', 'unresolved'),
        'notes': interaction_data.get('notes', ''),
        'wait_time_minutes': interaction_data.get('wait_time_minutes'),
        'call_duration_minutes': interaction_data.get('call_duration_minutes'),
        'was_transferred': interaction_data.get('was_transferred', False),
        'transfer_count': interaction_data.get('transfer_count', 0),
        'customer_satisfaction': interaction_data.get('customer_satisfaction'),  # 1-5 scale
    }

    with support_interactions_lock:
        support_interactions.append(interaction)

    save_support_interactions()
    print(f'[SUPPORT] Added interaction {interaction["id"]}')
    return interaction

def update_support_interaction(interaction_id, updates):
    """Update an existing support interaction"""
    with support_interactions_lock:
        for i, interaction in enumerate(support_interactions):
            if interaction['id'] == interaction_id:
                # Update allowed fields
                allowed_fields = [
                    'contact_date', 'contact_time', 'contact_method', 'agent_name',
                    'agent_id', 'ticket_number', 'complaint_summary', 'response_received',
                    'resolution_offered', 'resolution_status', 'notes', 'wait_time_minutes',
                    'call_duration_minutes', 'was_transferred', 'transfer_count',
                    'customer_satisfaction'
                ]
                for field in allowed_fields:
                    if field in updates:
                        support_interactions[i][field] = updates[field]
                support_interactions[i]['updated_at'] = datetime.utcnow().isoformat()
                save_support_interactions()
                print(f'[SUPPORT] Updated interaction {interaction_id}')
                return support_interactions[i]
    return None

def delete_support_interaction(interaction_id):
    """Delete a support interaction"""
    with support_interactions_lock:
        for i, interaction in enumerate(support_interactions):
            if interaction['id'] == interaction_id:
                deleted = support_interactions.pop(i)
                save_support_interactions()
                print(f'[SUPPORT] Deleted interaction {interaction_id}')
                return deleted
    return None

def get_support_interactions_summary():
    """Get summary statistics for support interactions"""
    with support_interactions_lock:
        interactions = list(support_interactions)

    if not interactions:
        return {
            'total_interactions': 0,
            'by_method': {},
            'by_status': {},
            'avg_wait_time': None,
            'avg_call_duration': None,
            'avg_satisfaction': None,
            'total_transfers': 0,
            'unresolved_count': 0
        }

    by_method = {}
    by_status = {}
    wait_times = []
    call_durations = []
    satisfaction_scores = []
    total_transfers = 0

    for interaction in interactions:
        # Count by method
        method = interaction.get('contact_method', 'unknown')
        by_method[method] = by_method.get(method, 0) + 1

        # Count by status
        status = interaction.get('resolution_status', 'unknown')
        by_status[status] = by_status.get(status, 0) + 1

        # Collect numeric metrics
        if interaction.get('wait_time_minutes') is not None:
            wait_times.append(interaction['wait_time_minutes'])
        if interaction.get('call_duration_minutes') is not None:
            call_durations.append(interaction['call_duration_minutes'])
        if interaction.get('customer_satisfaction') is not None:
            satisfaction_scores.append(interaction['customer_satisfaction'])

        total_transfers += interaction.get('transfer_count', 0)

    return {
        'total_interactions': len(interactions),
        'by_method': by_method,
        'by_status': by_status,
        'avg_wait_time': round(sum(wait_times) / len(wait_times), 1) if wait_times else None,
        'avg_call_duration': round(sum(call_durations) / len(call_durations), 1) if call_durations else None,
        'avg_satisfaction': round(sum(satisfaction_scores) / len(satisfaction_scores), 2) if satisfaction_scores else None,
        'total_transfers': total_transfers,
        'unresolved_count': by_status.get('unresolved', 0)
    }

def export_support_interactions_for_fcc():
    """Export support interactions in a format suitable for FCC complaint"""
    with support_interactions_lock:
        interactions = list(support_interactions)

    if not interactions:
        return {
            'error': 'No support interactions recorded',
            'interactions': []
        }

    summary = get_support_interactions_summary()

    # Sort by date
    sorted_interactions = sorted(
        interactions,
        key=lambda x: (x.get('contact_date', ''), x.get('contact_time', ''))
    )

    # Format for FCC complaint
    formatted = []
    for interaction in sorted_interactions:
        formatted.append({
            'date': interaction.get('contact_date', 'Unknown'),
            'time': interaction.get('contact_time', 'Unknown'),
            'method': interaction.get('contact_method', 'Unknown'),
            'agent': interaction.get('agent_name') or interaction.get('agent_id') or 'Unknown',
            'ticket': interaction.get('ticket_number') or 'None provided',
            'complaint': interaction.get('complaint_summary', ''),
            'response': interaction.get('response_received', ''),
            'resolution': interaction.get('resolution_offered', 'None'),
            'status': interaction.get('resolution_status', 'Unknown'),
            'wait_minutes': interaction.get('wait_time_minutes'),
            'duration_minutes': interaction.get('call_duration_minutes'),
            'was_transferred': interaction.get('was_transferred', False),
            'transfer_count': interaction.get('transfer_count', 0),
            'notes': interaction.get('notes', '')
        })

    return {
        'export_date': datetime.utcnow().isoformat(),
        'summary': {
            'total_contacts': summary['total_interactions'],
            'unresolved_issues': summary['unresolved_count'],
            'average_wait_time_minutes': summary['avg_wait_time'],
            'average_call_duration_minutes': summary['avg_call_duration'],
            'total_transfers': summary['total_transfers'],
            'contact_methods_used': summary['by_method']
        },
        'interactions': formatted,
        'fcc_narrative': generate_fcc_narrative(sorted_interactions, summary)
    }

def generate_fcc_narrative(interactions, summary):
    """Generate a narrative summary suitable for FCC complaint"""
    if not interactions:
        return "No support interactions have been recorded."

    first_contact = interactions[0].get('contact_date', 'Unknown date')
    last_contact = interactions[-1].get('contact_date', 'Unknown date')

    narrative_parts = [
        f"Between {first_contact} and {last_contact}, I contacted T-Mobile customer support "
        f"{summary['total_interactions']} time(s) regarding ongoing service issues."
    ]

    if summary['avg_wait_time']:
        narrative_parts.append(
            f"Average wait time before speaking with a representative was {summary['avg_wait_time']} minutes."
        )

    if summary['total_transfers'] > 0:
        narrative_parts.append(
            f"I was transferred between departments {summary['total_transfers']} time(s) total during these calls."
        )

    if summary['unresolved_count'] > 0:
        narrative_parts.append(
            f"As of this filing, {summary['unresolved_count']} of my reported issues remain unresolved."
        )

    # Include notable quotes or dismissive responses
    dismissive_responses = []
    for interaction in interactions:
        response = interaction.get('response_received', '')
        if response and any(keyword in response.lower() for keyword in ['can\'t', 'cannot', 'don\'t matter', 'youtube', 'streaming']):
            dismissive_responses.append(f"- \"{response}\" ({interaction.get('contact_date', 'Unknown date')})")

    if dismissive_responses:
        narrative_parts.append("\nNotable dismissive responses from T-Mobile support:")
        narrative_parts.extend(dismissive_responses)

    return "\n".join(narrative_parts)

def save_speedtest_history():
    """Save speed test history to file"""
    try:
        with speedtest_results_lock:
            with open(SPEEDTEST_HISTORY_FILE, 'w') as f:
                json.dump(speedtest_results, f, indent=2)
    except Exception as e:
        print(f'[SPEEDTEST] Error saving history: {e}')

def get_current_signal_snapshot():
    """Get current signal metrics for correlation with speed test"""
    with cache_lock:
        data = cache['data']

    if not data:
        return None

    try:
        gw_data = json.loads(data) if isinstance(data, bytes) else data
        s5g = gw_data.get('signal', {}).get('5g', {})
        s4g = gw_data.get('signal', {}).get('4g', {})
        generic = gw_data.get('signal', {}).get('generic', {})

        return {
            '5g': {
                'sinr': s5g.get('sinr'),
                'rsrp': s5g.get('rsrp'),
                'rsrq': s5g.get('rsrq'),
                'bands': s5g.get('bands', []),
                'gNBID': s5g.get('gNBID'),
                'cid': s5g.get('cid')
            },
            '4g': {
                'sinr': s4g.get('sinr'),
                'rsrp': s4g.get('rsrp'),
                'rsrq': s4g.get('rsrq'),
                'bands': s4g.get('bands', []),
                'eNBID': s4g.get('eNBID'),
                'cid': s4g.get('cid')
            },
            'registration': generic.get('registration')
        }
    except Exception as e:
        print(f'[SPEEDTEST] Error getting signal snapshot: {e}')
        return None

def run_speedtest():
    """Run speedtest-cli and return results"""
    global speedtest_running

    with speedtest_running_lock:
        if speedtest_running:
            return {'error': 'Speed test already running', 'status': 'busy'}
        speedtest_running = True

    try:
        print('[SPEEDTEST] Starting speed test...')

        # Capture signal at start of test
        signal_at_test = get_current_signal_snapshot()
        test_start = time.time()

        # Try speedtest-cli first (Python package)
        try:
            # Use sys.executable to ensure we find the right Python with speedtest installed
            result = subprocess.run(
                [sys.executable, '-m', 'speedtest', '--json'],
                capture_output=True,
                text=True,
                timeout=120  # 2 minute timeout
            )

            if result.returncode == 0:
                speedtest_data = json.loads(result.stdout)

                # Convert to consistent format (speeds in Mbps)
                test_result = {
                    'timestamp': datetime.utcnow().isoformat(),
                    'timestamp_unix': time.time(),
                    'download_mbps': round(speedtest_data.get('download', 0) / 1_000_000, 2),
                    'upload_mbps': round(speedtest_data.get('upload', 0) / 1_000_000, 2),
                    'ping_ms': round(speedtest_data.get('ping', 0), 1),
                    'jitter_ms': None,  # speedtest-cli doesn't provide jitter
                    'server': {
                        'name': speedtest_data.get('server', {}).get('name'),
                        'sponsor': speedtest_data.get('server', {}).get('sponsor'),
                        'location': f"{speedtest_data.get('server', {}).get('name')}, {speedtest_data.get('server', {}).get('country')}",
                        'host': speedtest_data.get('server', {}).get('host'),
                        'latency': speedtest_data.get('server', {}).get('latency')
                    },
                    'client': {
                        'ip': speedtest_data.get('client', {}).get('ip'),
                        'isp': speedtest_data.get('client', {}).get('isp')
                    },
                    'bytes_sent': speedtest_data.get('bytes_sent'),
                    'bytes_received': speedtest_data.get('bytes_received'),
                    'share_url': speedtest_data.get('share'),
                    'signal_at_test': signal_at_test,
                    'duration_seconds': round(time.time() - test_start, 1),
                    'tool': 'speedtest-cli',
                    'status': 'success'
                }

                # Store result
                with speedtest_results_lock:
                    speedtest_results.append(test_result)
                    # Keep only last 100 results in memory
                    if len(speedtest_results) > 100:
                        speedtest_results.pop(0)

                save_speedtest_history()
                print(f'[SPEEDTEST] Complete: {test_result["download_mbps"]} Mbps down, {test_result["upload_mbps"]} Mbps up, {test_result["ping_ms"]} ms ping')
                return test_result
            else:
                raise Exception(f'speedtest-cli failed: {result.stderr}')

        except FileNotFoundError:
            # speedtest-cli not installed, try ookla speedtest
            try:
                result = subprocess.run(
                    ['speedtest', '--format=json', '--accept-license', '--accept-gdpr'],
                    capture_output=True,
                    text=True,
                    timeout=120
                )

                if result.returncode == 0:
                    speedtest_data = json.loads(result.stdout)

                    # Ookla format is different
                    test_result = {
                        'timestamp': datetime.utcnow().isoformat(),
                        'timestamp_unix': time.time(),
                        'download_mbps': round(speedtest_data.get('download', {}).get('bandwidth', 0) * 8 / 1_000_000, 2),
                        'upload_mbps': round(speedtest_data.get('upload', {}).get('bandwidth', 0) * 8 / 1_000_000, 2),
                        'ping_ms': round(speedtest_data.get('ping', {}).get('latency', 0), 1),
                        'jitter_ms': round(speedtest_data.get('ping', {}).get('jitter', 0), 1),
                        'server': {
                            'name': speedtest_data.get('server', {}).get('name'),
                            'location': speedtest_data.get('server', {}).get('location'),
                            'host': speedtest_data.get('server', {}).get('host'),
                            'id': speedtest_data.get('server', {}).get('id')
                        },
                        'client': {
                            'ip': speedtest_data.get('interface', {}).get('externalIp'),
                            'isp': speedtest_data.get('isp')
                        },
                        'result_url': speedtest_data.get('result', {}).get('url'),
                        'signal_at_test': signal_at_test,
                        'duration_seconds': round(time.time() - test_start, 1),
                        'tool': 'ookla-speedtest',
                        'status': 'success'
                    }

                    with speedtest_results_lock:
                        speedtest_results.append(test_result)
                        if len(speedtest_results) > 100:
                            speedtest_results.pop(0)

                    save_speedtest_history()
                    print(f'[SPEEDTEST] Complete: {test_result["download_mbps"]} Mbps down, {test_result["upload_mbps"]} Mbps up, {test_result["ping_ms"]} ms ping')
                    return test_result
                else:
                    raise Exception(f'ookla speedtest failed: {result.stderr}')

            except FileNotFoundError:
                return {
                    'error': 'No speed test tool found. Install speedtest-cli (pip install speedtest-cli) or Ookla Speedtest CLI.',
                    'status': 'error',
                    'install_instructions': {
                        'speedtest-cli': 'pip install speedtest-cli',
                        'ookla': 'Download from https://www.speedtest.net/apps/cli'
                    }
                }

    except subprocess.TimeoutExpired:
        return {'error': 'Speed test timed out after 120 seconds', 'status': 'timeout'}
    except json.JSONDecodeError as e:
        return {'error': f'Failed to parse speed test output: {e}', 'status': 'error'}
    except Exception as e:
        return {'error': str(e), 'status': 'error'}
    finally:
        with speedtest_running_lock:
            speedtest_running = False

def init_database():
    """Initialize SQLite database with schema"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Create table for signal history
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signal_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            timestamp_unix REAL NOT NULL,

            -- 5G NR metrics
            nr_sinr REAL,
            nr_rsrp REAL,
            nr_rsrq REAL,
            nr_rssi REAL,
            nr_bands TEXT,
            nr_gnb_id INTEGER,
            nr_cid INTEGER,

            -- 4G LTE metrics
            lte_sinr REAL,
            lte_rsrp REAL,
            lte_rsrq REAL,
            lte_rssi REAL,
            lte_bands TEXT,
            lte_enb_id INTEGER,
            lte_cid INTEGER,

            -- General info
            registration_status TEXT,
            device_uptime INTEGER
        )
    ''')

    # Create index for efficient time-based queries
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_timestamp_unix
        ON signal_history(timestamp_unix)
    ''')

    # Create table for hourly aggregated metrics (for congestion analysis)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS hourly_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            hour INTEGER NOT NULL,
            day_of_week INTEGER NOT NULL,
            is_weekend INTEGER NOT NULL,

            -- 5G NR aggregates
            nr_sinr_avg REAL,
            nr_sinr_min REAL,
            nr_sinr_max REAL,
            nr_rsrp_avg REAL,
            nr_rsrp_min REAL,
            nr_rsrp_max REAL,
            nr_rsrq_avg REAL,

            -- 4G LTE aggregates
            lte_sinr_avg REAL,
            lte_sinr_min REAL,
            lte_sinr_max REAL,
            lte_rsrp_avg REAL,
            lte_rsrp_min REAL,
            lte_rsrp_max REAL,
            lte_rsrq_avg REAL,

            -- Congestion metrics (derived from SINR)
            congestion_score REAL,
            sample_count INTEGER,

            UNIQUE(date, hour)
        )
    ''')

    # Create index for efficient hourly queries
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_hourly_date_hour
        ON hourly_metrics(date, hour)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_hourly_day_of_week
        ON hourly_metrics(day_of_week, hour)
    ''')

    # Create table for disruption events (real-time detection)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS disruption_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            timestamp_unix REAL NOT NULL,
            event_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            description TEXT,

            -- Signal values at event time
            nr_sinr_before REAL,
            nr_sinr_after REAL,
            nr_rsrp_before REAL,
            nr_rsrp_after REAL,
            lte_sinr_before REAL,
            lte_sinr_after REAL,
            lte_rsrp_before REAL,
            lte_rsrp_after REAL,

            -- Tower/cell info at event time
            nr_gnb_id_before INTEGER,
            nr_gnb_id_after INTEGER,
            lte_enb_id_before INTEGER,
            lte_enb_id_after INTEGER,

            -- Event duration tracking
            duration_seconds REAL,
            resolved INTEGER DEFAULT 0
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_disruption_timestamp
        ON disruption_events(timestamp_unix)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_disruption_type
        ON disruption_events(event_type)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_disruption_severity
        ON disruption_events(severity)
    ''')

    conn.commit()
    conn.close()
    print(f'[DATABASE] Initialized at {DB_PATH}')

def parse_signal_data(data):
    """Parse gateway JSON response into flat dict for DB insertion"""
    try:
        if isinstance(data, bytes):
            data = json.loads(data.decode('utf-8'))

        s5g = data.get('signal', {}).get('5g', {})
        s4g = data.get('signal', {}).get('4g', {})
        generic = data.get('signal', {}).get('generic', {})
        time_info = data.get('time', {})

        return {
            'timestamp': datetime.utcnow().isoformat(),
            'timestamp_unix': time.time(),
            'nr_sinr': s5g.get('sinr'),
            'nr_rsrp': s5g.get('rsrp'),
            'nr_rsrq': s5g.get('rsrq'),
            'nr_rssi': s5g.get('rssi'),
            'nr_bands': ','.join(map(str, s5g.get('bands', []))),
            'nr_gnb_id': s5g.get('gNBID'),
            'nr_cid': s5g.get('cid'),
            'lte_sinr': s4g.get('sinr'),
            'lte_rsrp': s4g.get('rsrp'),
            'lte_rsrq': s4g.get('rsrq'),
            'lte_rssi': s4g.get('rssi'),
            'lte_bands': ','.join(map(str, s4g.get('bands', []))),
            'lte_enb_id': s4g.get('eNBID'),
            'lte_cid': s4g.get('cid'),
            'registration_status': generic.get('registration'),
            'device_uptime': time_info.get('upTime')
        }
    except Exception as e:
        print(f'[DATABASE] Parse error: {e}')
        return None


def log_disruption_event(event_type, severity, description, before_state, after_state):
    """Log a disruption event to the database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        cursor.execute('''
            INSERT INTO disruption_events (
                timestamp, timestamp_unix, event_type, severity, description,
                nr_sinr_before, nr_sinr_after, nr_rsrp_before, nr_rsrp_after,
                lte_sinr_before, lte_sinr_after, lte_rsrp_before, lte_rsrp_after,
                nr_gnb_id_before, nr_gnb_id_after, lte_enb_id_before, lte_enb_id_after
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.utcnow().isoformat(),
            time.time(),
            event_type,
            severity,
            description,
            before_state['5g'].get('sinr'),
            after_state['5g'].get('sinr'),
            before_state['5g'].get('rsrp'),
            after_state['5g'].get('rsrp'),
            before_state['4g'].get('sinr'),
            after_state['4g'].get('sinr'),
            before_state['4g'].get('rsrp'),
            after_state['4g'].get('rsrp'),
            before_state['5g'].get('gnb_id'),
            after_state['5g'].get('gnb_id'),
            before_state['4g'].get('enb_id'),
            after_state['4g'].get('enb_id'),
        ))
        conn.commit()
        print(f'[DISRUPTION] Logged {severity} {event_type}: {description}')
    except Exception as e:
        print(f'[DISRUPTION] Error logging event: {e}')
    finally:
        conn.close()


def check_cooldown(event_type):
    """Check if an event type is in cooldown period"""
    with disruption_cooldown_lock:
        if event_type in disruption_cooldowns:
            if time.time() - disruption_cooldowns[event_type] < DISRUPTION_THRESHOLDS['cooldown_period']:
                return True
        return False


def set_cooldown(event_type):
    """Set cooldown for an event type"""
    with disruption_cooldown_lock:
        disruption_cooldowns[event_type] = time.time()


def detect_disruption_realtime(raw_data):
    """Detect disruptions in real-time by comparing current signal to previous state.

    Detects:
    - Signal drops (sudden decrease in SINR/RSRP)
    - Tower handoffs (gNB/eNB ID changes)
    - Registration changes
    - Gateway unreachable (handled separately in poll_gateway)
    """
    global last_signal_state

    try:
        if isinstance(raw_data, bytes):
            data = json.loads(raw_data.decode('utf-8'))
        else:
            data = raw_data

        s5g = data.get('signal', {}).get('5g', {})
        s4g = data.get('signal', {}).get('4g', {})
        generic = data.get('signal', {}).get('generic', {})

        current_state = {
            '5g': {
                'sinr': s5g.get('sinr'),
                'rsrp': s5g.get('rsrp'),
                'gnb_id': s5g.get('gNBID'),
                'cid': s5g.get('cid')
            },
            '4g': {
                'sinr': s4g.get('sinr'),
                'rsrp': s4g.get('rsrp'),
                'enb_id': s4g.get('eNBID'),
                'cid': s4g.get('cid')
            },
            'registration': generic.get('registration'),
            'timestamp': time.time()
        }

        with last_signal_lock:
            prev_state = last_signal_state.copy()
            prev_5g = prev_state['5g'].copy()
            prev_4g = prev_state['4g'].copy()

        # Skip if this is the first reading
        if prev_state['timestamp'] == 0:
            with last_signal_lock:
                last_signal_state = current_state
            return

        # Check time since last reading
        time_delta = current_state['timestamp'] - prev_state['timestamp']
        if time_delta > DISRUPTION_THRESHOLDS['detection_window']:
            # Too long between readings, just update state
            with last_signal_lock:
                last_signal_state = current_state
            return

        events_detected = []

        # ---- 5G Signal Drop Detection ----
        if prev_5g['sinr'] is not None and current_state['5g']['sinr'] is not None:
            sinr_drop = prev_5g['sinr'] - current_state['5g']['sinr']
            if sinr_drop >= DISRUPTION_THRESHOLDS['sinr_drop_5g']:
                if not check_cooldown('5g_sinr_drop'):
                    severity = 'critical' if current_state['5g']['sinr'] < DISRUPTION_THRESHOLDS['sinr_critical_5g'] else 'warning'
                    events_detected.append({
                        'type': '5g_sinr_drop',
                        'severity': severity,
                        'description': f"5G SINR dropped {sinr_drop:.1f} dB ({prev_5g['sinr']:.1f} -> {current_state['5g']['sinr']:.1f} dB)"
                    })
                    set_cooldown('5g_sinr_drop')

        if prev_5g['rsrp'] is not None and current_state['5g']['rsrp'] is not None:
            rsrp_drop = prev_5g['rsrp'] - current_state['5g']['rsrp']
            if rsrp_drop >= DISRUPTION_THRESHOLDS['rsrp_drop_5g']:
                if not check_cooldown('5g_rsrp_drop'):
                    severity = 'critical' if current_state['5g']['rsrp'] < DISRUPTION_THRESHOLDS['rsrp_critical_5g'] else 'warning'
                    events_detected.append({
                        'type': '5g_rsrp_drop',
                        'severity': severity,
                        'description': f"5G RSRP dropped {rsrp_drop:.1f} dBm ({prev_5g['rsrp']:.1f} -> {current_state['5g']['rsrp']:.1f} dBm)"
                    })
                    set_cooldown('5g_rsrp_drop')

        # Critical 5G thresholds
        if current_state['5g']['sinr'] is not None and current_state['5g']['sinr'] < DISRUPTION_THRESHOLDS['sinr_critical_5g']:
            if not check_cooldown('5g_sinr_critical'):
                events_detected.append({
                    'type': '5g_sinr_critical',
                    'severity': 'critical',
                    'description': f"5G SINR critically low: {current_state['5g']['sinr']:.1f} dB"
                })
                set_cooldown('5g_sinr_critical')

        # ---- 4G Signal Drop Detection ----
        if prev_4g['sinr'] is not None and current_state['4g']['sinr'] is not None:
            sinr_drop = prev_4g['sinr'] - current_state['4g']['sinr']
            if sinr_drop >= DISRUPTION_THRESHOLDS['sinr_drop_4g']:
                if not check_cooldown('4g_sinr_drop'):
                    severity = 'critical' if current_state['4g']['sinr'] < DISRUPTION_THRESHOLDS['sinr_critical_4g'] else 'warning'
                    events_detected.append({
                        'type': '4g_sinr_drop',
                        'severity': severity,
                        'description': f"4G SINR dropped {sinr_drop:.1f} dB ({prev_4g['sinr']:.1f} -> {current_state['4g']['sinr']:.1f} dB)"
                    })
                    set_cooldown('4g_sinr_drop')

        # ---- Tower Handoff Detection ----
        if prev_5g['gnb_id'] is not None and current_state['5g']['gnb_id'] is not None:
            if prev_5g['gnb_id'] != current_state['5g']['gnb_id']:
                if not check_cooldown('5g_tower_handoff'):
                    events_detected.append({
                        'type': '5g_tower_handoff',
                        'severity': 'info',
                        'description': f"5G tower handoff: gNB {prev_5g['gnb_id']} -> {current_state['5g']['gnb_id']}"
                    })
                    set_cooldown('5g_tower_handoff')

        if prev_4g['enb_id'] is not None and current_state['4g']['enb_id'] is not None:
            if prev_4g['enb_id'] != current_state['4g']['enb_id']:
                if not check_cooldown('4g_tower_handoff'):
                    events_detected.append({
                        'type': '4g_tower_handoff',
                        'severity': 'info',
                        'description': f"4G tower handoff: eNB {prev_4g['enb_id']} -> {current_state['4g']['enb_id']}"
                    })
                    set_cooldown('4g_tower_handoff')

        # ---- Registration Status Change ----
        if prev_state['registration'] is not None and current_state['registration'] is not None:
            if prev_state['registration'] != current_state['registration']:
                if not check_cooldown('registration_change'):
                    severity = 'warning' if 'registered' not in current_state['registration'].lower() else 'info'
                    events_detected.append({
                        'type': 'registration_change',
                        'severity': severity,
                        'description': f"Registration status changed: {prev_state['registration']} -> {current_state['registration']}"
                    })
                    set_cooldown('registration_change')

        # Log all detected events
        for event in events_detected:
            log_disruption_event(
                event['type'],
                event['severity'],
                event['description'],
                prev_state,
                current_state
            )

        # Update stored state
        with last_signal_lock:
            last_signal_state = current_state

    except Exception as e:
        print(f'[DISRUPTION] Detection error: {e}')


def get_disruption_stats(duration_hours=24):
    """Get disruption statistics for the specified duration"""
    cutoff = time.time() - (duration_hours * 3600)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # Total events
        cursor.execute('''
            SELECT COUNT(*) as total FROM disruption_events
            WHERE timestamp_unix >= ?
        ''', (cutoff,))
        total = cursor.fetchone()['total']

        # By type
        cursor.execute('''
            SELECT event_type, COUNT(*) as count FROM disruption_events
            WHERE timestamp_unix >= ?
            GROUP BY event_type
            ORDER BY count DESC
        ''', (cutoff,))
        by_type = {row['event_type']: row['count'] for row in cursor.fetchall()}

        # By severity
        cursor.execute('''
            SELECT severity, COUNT(*) as count FROM disruption_events
            WHERE timestamp_unix >= ?
            GROUP BY severity
        ''', (cutoff,))
        by_severity = {row['severity']: row['count'] for row in cursor.fetchall()}

        # Recent events
        cursor.execute('''
            SELECT * FROM disruption_events
            WHERE timestamp_unix >= ?
            ORDER BY timestamp_unix DESC
            LIMIT 50
        ''', (cutoff,))
        recent_events = [dict(row) for row in cursor.fetchall()]

        # Average signal values during disruptions
        cursor.execute('''
            SELECT
                AVG(nr_sinr_after) as avg_5g_sinr,
                AVG(nr_rsrp_after) as avg_5g_rsrp,
                AVG(lte_sinr_after) as avg_4g_sinr,
                AVG(lte_rsrp_after) as avg_4g_rsrp
            FROM disruption_events
            WHERE timestamp_unix >= ?
        ''', (cutoff,))
        avg_row = cursor.fetchone()

        return {
            'duration_hours': duration_hours,
            'total_events': total,
            'by_type': by_type,
            'by_severity': by_severity,
            'avg_signal_during_disruptions': {
                '5g_sinr': round(avg_row['avg_5g_sinr'], 1) if avg_row['avg_5g_sinr'] else None,
                '5g_rsrp': round(avg_row['avg_5g_rsrp'], 1) if avg_row['avg_5g_rsrp'] else None,
                '4g_sinr': round(avg_row['avg_4g_sinr'], 1) if avg_row['avg_4g_sinr'] else None,
                '4g_rsrp': round(avg_row['avg_4g_rsrp'], 1) if avg_row['avg_4g_rsrp'] else None,
            },
            'recent_events': recent_events
        }
    except Exception as e:
        print(f'[DISRUPTION] Stats error: {e}')
        return {'error': str(e)}
    finally:
        conn.close()


def buffer_signal_data(raw_data):
    """Add parsed signal data to buffer for batch insert"""
    parsed = parse_signal_data(raw_data)
    if parsed:
        with db_buffer_lock:
            db_buffer.append(parsed)

    # Also run disruption detection
    detect_disruption_realtime(raw_data)


def flush_buffer_to_db():
    """Batch insert buffered data to SQLite"""
    with db_buffer_lock:
        if not db_buffer:
            return 0
        records = list(db_buffer)
        db_buffer.clear()

    if not records:
        return 0

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        cursor.executemany('''
            INSERT INTO signal_history (
                timestamp, timestamp_unix,
                nr_sinr, nr_rsrp, nr_rsrq, nr_rssi, nr_bands, nr_gnb_id, nr_cid,
                lte_sinr, lte_rsrp, lte_rsrq, lte_rssi, lte_bands, lte_enb_id, lte_cid,
                registration_status, device_uptime
            ) VALUES (
                :timestamp, :timestamp_unix,
                :nr_sinr, :nr_rsrp, :nr_rsrq, :nr_rssi, :nr_bands, :nr_gnb_id, :nr_cid,
                :lte_sinr, :lte_rsrp, :lte_rsrq, :lte_rssi, :lte_bands, :lte_enb_id, :lte_cid,
                :registration_status, :device_uptime
            )
        ''', records)
        conn.commit()
        return len(records)
    except Exception as e:
        print(f'[DATABASE] Batch insert error: {e}')
        return 0
    finally:
        conn.close()

def cleanup_old_data():
    """Remove records older than retention period"""
    cutoff = time.time() - (DB_RETENTION_DAYS * 86400)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        cursor.execute('DELETE FROM signal_history WHERE timestamp_unix < ?', (cutoff,))
        deleted = cursor.rowcount
        conn.commit()
        if deleted > 0:
            print(f'[DATABASE] Cleaned up {deleted} old records')
            # Vacuum to reclaim space (run periodically)
            cursor.execute('VACUUM')
        return deleted
    except Exception as e:
        print(f'[DATABASE] Cleanup error: {e}')
        return 0
    finally:
        conn.close()

def db_worker():
    """Background thread for batch inserts, cleanup, and hourly aggregation"""
    last_cleanup = time.time()
    last_aggregation = time.time()
    cleanup_interval = 3600  # Check for cleanup every hour
    aggregation_interval = 3600  # Run hourly aggregation every hour

    while True:
        time.sleep(DB_BATCH_INTERVAL)

        # Batch insert buffered data
        count = flush_buffer_to_db()
        if count > 0:
            print(f'[DATABASE] Inserted {count} records')

        # Periodic cleanup
        if time.time() - last_cleanup > cleanup_interval:
            cleanup_old_data()
            last_cleanup = time.time()

        # Periodic congestion aggregation
        if time.time() - last_aggregation > aggregation_interval:
            try:
                congestion.aggregate_hourly_metrics()
            except Exception as e:
                print(f'[CONGESTION] Aggregation error in worker: {e}')
            last_aggregation = time.time()

def query_history(duration_minutes=60, resolution='auto'):
    """Query historical data with optional downsampling

    Args:
        duration_minutes: How far back to query (default 60 minutes)
        resolution: 'auto', 'full', or seconds between points

    Returns:
        List of signal records
    """
    cutoff = time.time() - (duration_minutes * 60)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # Determine resolution
        if resolution == 'full':
            # Return all data points
            cursor.execute('''
                SELECT * FROM signal_history
                WHERE timestamp_unix >= ?
                ORDER BY timestamp_unix ASC
            ''', (cutoff,))
        elif resolution == 'auto':
            # Auto-select resolution based on duration
            if duration_minutes <= 5:
                interval = 1  # 1 second
            elif duration_minutes <= 60:
                interval = 5  # 5 seconds
            elif duration_minutes <= 360:
                interval = 30  # 30 seconds
            else:
                interval = 300  # 5 minutes

            # Group by time bucket and average
            cursor.execute('''
                SELECT
                    CAST(timestamp_unix / ? AS INTEGER) * ? as bucket,
                    AVG(nr_sinr) as nr_sinr,
                    AVG(nr_rsrp) as nr_rsrp,
                    AVG(nr_rsrq) as nr_rsrq,
                    AVG(nr_rssi) as nr_rssi,
                    AVG(lte_sinr) as lte_sinr,
                    AVG(lte_rsrp) as lte_rsrp,
                    AVG(lte_rsrq) as lte_rsrq,
                    AVG(lte_rssi) as lte_rssi,
                    MAX(nr_bands) as nr_bands,
                    MAX(nr_gnb_id) as nr_gnb_id,
                    MAX(lte_bands) as lte_bands,
                    MAX(lte_enb_id) as lte_enb_id,
                    COUNT(*) as sample_count
                FROM signal_history
                WHERE timestamp_unix >= ?
                GROUP BY bucket
                ORDER BY bucket ASC
            ''', (interval, interval, cutoff))
        else:
            # Custom interval
            interval = int(resolution)
            cursor.execute('''
                SELECT
                    CAST(timestamp_unix / ? AS INTEGER) * ? as bucket,
                    AVG(nr_sinr) as nr_sinr,
                    AVG(nr_rsrp) as nr_rsrp,
                    AVG(nr_rsrq) as nr_rsrq,
                    AVG(nr_rssi) as nr_rssi,
                    AVG(lte_sinr) as lte_sinr,
                    AVG(lte_rsrp) as lte_rsrp,
                    AVG(lte_rsrq) as lte_rsrq,
                    AVG(lte_rssi) as lte_rssi,
                    MAX(nr_bands) as nr_bands,
                    MAX(nr_gnb_id) as nr_gnb_id,
                    MAX(lte_bands) as lte_bands,
                    MAX(lte_enb_id) as lte_enb_id,
                    COUNT(*) as sample_count
                FROM signal_history
                WHERE timestamp_unix >= ?
                GROUP BY bucket
                ORDER BY bucket ASC
            ''', (interval, interval, cutoff))

        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f'[DATABASE] Query error: {e}')
        return []
    finally:
        conn.close()

def get_db_stats():
    """Get database statistics"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        cursor.execute('SELECT COUNT(*) FROM signal_history')
        total_records = cursor.fetchone()[0]

        cursor.execute('SELECT MIN(timestamp_unix), MAX(timestamp_unix) FROM signal_history')
        row = cursor.fetchone()
        oldest = row[0]
        newest = row[1]

        # Get file size
        db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0

        return {
            'total_records': total_records,
            'oldest_timestamp': oldest,
            'newest_timestamp': newest,
            'duration_hours': round((newest - oldest) / 3600, 2) if oldest and newest else 0,
            'db_size_mb': round(db_size / (1024 * 1024), 2),
            'retention_days': DB_RETENTION_DAYS
        }
    except Exception as e:
        print(f'[DATABASE] Stats error: {e}')
        return {'error': str(e)}
    finally:
        conn.close()

def poll_gateway():
    """Continuously poll the gateway and cache the result"""
    while True:
        now = time.time()
        try:
            req = urllib.request.Request(
                'http://192.168.12.1/TMI/v1/gateway?get=all',
                headers={'Connection': 'close'}
            )
            with urllib.request.urlopen(req, timeout=2) as response:
                data = response.read()
                if data and len(data) > 10:  # Sanity check
                    with cache_lock:
                        cache['data'] = data
                        cache['last_success'] = now
                        cache['last_attempt'] = now
                        cache['success_count'] += 1
                    # Buffer data for historical storage
                    buffer_signal_data(data)
                else:
                    with cache_lock:
                        cache['last_attempt'] = now
                        cache['error_count'] += 1
                        cache['last_error'] = 'Empty response'
                    print(f'[GATEWAY] Empty response')
        except Exception as e:
            with cache_lock:
                cache['last_attempt'] = now
                cache['error_count'] += 1
                cache['last_error'] = str(e)
            # Only log occasionally to avoid spam
            if cache['error_count'] % 10 == 1:
                print(f'[GATEWAY] Error #{cache["error_count"]}: {e}')
        time.sleep(GATEWAY_POLL_INTERVAL)

# Track file modification times for hot reload
file_mtimes = {}
reload_flag = threading.Event()

def watch_files():
    """Watch for file changes and set reload flag"""
    global file_mtimes
    watch_extensions = {'.html', '.css', '.js'}
    watch_dir = os.path.dirname(os.path.abspath(__file__)) or '.'

    while True:
        try:
            for filename in os.listdir(watch_dir):
                if any(filename.endswith(ext) for ext in watch_extensions):
                    filepath = os.path.join(watch_dir, filename)
                    mtime = os.stat(filepath).st_mtime
                    if filepath in file_mtimes:
                        if mtime > file_mtimes[filepath]:
                            print(f'[HOT RELOAD] {filename} changed')
                            reload_flag.set()
                    file_mtimes[filepath] = mtime
        except Exception as e:
            pass
        time.sleep(0.5)

def calculate_correlation(x_values, y_values):
    """Calculate Pearson correlation coefficient between two lists of values.

    Returns:
        Tuple of (correlation coefficient, p-value approximation, n samples)
        Returns (None, None, 0) if insufficient data
    """
    # Filter out None/null pairs
    pairs = [(x, y) for x, y in zip(x_values, y_values) if x is not None and y is not None]
    n = len(pairs)

    if n < 3:  # Need at least 3 points for meaningful correlation
        return None, None, n

    x = [p[0] for p in pairs]
    y = [p[1] for p in pairs]

    # Calculate means
    mean_x = sum(x) / n
    mean_y = sum(y) / n

    # Calculate standard deviations and covariance
    var_x = sum((xi - mean_x) ** 2 for xi in x) / n
    var_y = sum((yi - mean_y) ** 2 for yi in y) / n

    if var_x == 0 or var_y == 0:
        return None, None, n  # No variance means correlation undefined

    std_x = math.sqrt(var_x)
    std_y = math.sqrt(var_y)

    # Calculate Pearson correlation
    covariance = sum((xi - mean_x) * (yi - mean_y) for xi, yi in pairs) / n
    r = covariance / (std_x * std_y)

    # Approximate p-value using t-distribution (simplified)
    if abs(r) >= 1:
        p_value = 0
    else:
        t = r * math.sqrt(n - 2) / math.sqrt(1 - r * r)
        # Rough p-value approximation (two-tailed)
        p_value = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))

    return round(r, 4), round(p_value, 6), n


def find_threshold(metric_values, speed_values, percentile=25):
    """Find the threshold value where performance drops.

    Identifies the metric value below which speeds are in the bottom percentile.

    Returns:
        Dict with threshold info
    """
    pairs = [(m, s) for m, s in zip(metric_values, speed_values) if m is not None and s is not None]

    if len(pairs) < 5:
        return None

    # Sort by speed to find bottom percentile
    pairs_by_speed = sorted(pairs, key=lambda p: p[1])
    bottom_count = max(1, int(len(pairs) * percentile / 100))

    # Get metric values for bottom performers
    bottom_metrics = [p[0] for p in pairs_by_speed[:bottom_count]]
    top_metrics = [p[0] for p in pairs_by_speed[bottom_count:]]

    if not bottom_metrics or not top_metrics:
        return None

    # Threshold is the max of bottom performers (where good performance starts)
    threshold = max(bottom_metrics)

    # Calculate averages for context
    avg_below = sum(p[1] for p in pairs_by_speed[:bottom_count]) / bottom_count
    avg_above = sum(p[1] for p in pairs_by_speed[bottom_count:]) / len(pairs_by_speed[bottom_count:])

    return {
        'threshold': round(threshold, 2),
        'avg_speed_below': round(avg_below, 2),
        'avg_speed_above': round(avg_above, 2),
        'samples_below': bottom_count,
        'samples_above': len(pairs) - bottom_count
    }


def get_metric_distribution(metric_values, speed_values, speed_threshold):
    """Get distribution of metric values for good vs bad performance.

    Args:
        metric_values: List of signal metric values
        speed_values: List of corresponding speed values
        speed_threshold: Speed (Mbps) below which is considered "bad"

    Returns:
        Dict with distributions for good and bad performance
    """
    good_metrics = []
    bad_metrics = []

    for m, s in zip(metric_values, speed_values):
        if m is not None and s is not None:
            if s >= speed_threshold:
                good_metrics.append(m)
            else:
                bad_metrics.append(m)

    def calc_stats(values):
        if not values:
            return None
        n = len(values)
        mean = sum(values) / n
        sorted_vals = sorted(values)
        median = sorted_vals[n // 2] if n % 2 == 1 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
        return {
            'count': n,
            'mean': round(mean, 2),
            'median': round(median, 2),
            'min': round(min(values), 2),
            'max': round(max(values), 2),
            'p25': round(sorted_vals[int(n * 0.25)], 2) if n >= 4 else round(sorted_vals[0], 2),
            'p75': round(sorted_vals[int(n * 0.75)], 2) if n >= 4 else round(sorted_vals[-1], 2)
        }

    return {
        'good_performance': calc_stats(good_metrics),
        'bad_performance': calc_stats(bad_metrics)
    }


def analyze_signal_speed_correlation():
    """Analyze correlation between signal metrics and speed test results.

    Returns comprehensive correlation analysis including:
    - Pearson correlation coefficients for each metric vs download/upload speed
    - Identified threshold values where performance degrades
    - Distribution of metrics for good vs bad performance
    - Scatter plot data for visualization
    """
    with speedtest_results_lock:
        results = list(speedtest_results)

    if len(results) < 3:
        return {
            'error': 'Insufficient data - need at least 3 speed tests with signal data',
            'tests_available': len(results),
            'tests_with_signal': sum(1 for r in results if r.get('signal_at_test'))
        }

    # Extract signal metrics and speeds from each test
    data = {
        '5g': {'sinr': [], 'rsrp': [], 'rsrq': []},
        '4g': {'sinr': [], 'rsrp': [], 'rsrq': []},
        'download_mbps': [],
        'upload_mbps': [],
        'ping_ms': [],
        'timestamps': [],
        'bands_5g': [],
        'bands_4g': []
    }

    for result in results:
        signal = result.get('signal_at_test')
        if not signal:
            continue

        # Extract signal metrics
        s5g = signal.get('5g', {})
        s4g = signal.get('4g', {})

        data['5g']['sinr'].append(s5g.get('sinr'))
        data['5g']['rsrp'].append(s5g.get('rsrp'))
        data['5g']['rsrq'].append(s5g.get('rsrq'))

        data['4g']['sinr'].append(s4g.get('sinr'))
        data['4g']['rsrp'].append(s4g.get('rsrp'))
        data['4g']['rsrq'].append(s4g.get('rsrq'))

        data['download_mbps'].append(result.get('download_mbps'))
        data['upload_mbps'].append(result.get('upload_mbps'))
        data['ping_ms'].append(result.get('ping_ms'))
        data['timestamps'].append(result.get('timestamp'))
        data['bands_5g'].append(s5g.get('bands', []))
        data['bands_4g'].append(s4g.get('bands', []))

    if len(data['download_mbps']) < 3:
        return {
            'error': 'Insufficient tests with signal data',
            'tests_with_signal': len(data['download_mbps'])
        }

    # Calculate correlations for each metric
    correlations = {
        'download': {},
        'upload': {},
        'ping': {}
    }

    metrics = [
        ('5g_sinr', data['5g']['sinr']),
        ('5g_rsrp', data['5g']['rsrp']),
        ('5g_rsrq', data['5g']['rsrq']),
        ('4g_sinr', data['4g']['sinr']),
        ('4g_rsrp', data['4g']['rsrp']),
        ('4g_rsrq', data['4g']['rsrq'])
    ]

    for metric_name, metric_values in metrics:
        # Correlation with download speed
        r, p, n = calculate_correlation(metric_values, data['download_mbps'])
        if r is not None:
            correlations['download'][metric_name] = {
                'r': r,
                'p_value': p,
                'n': n,
                'strength': 'strong' if abs(r) >= 0.7 else 'moderate' if abs(r) >= 0.4 else 'weak',
                'direction': 'positive' if r > 0 else 'negative'
            }

        # Correlation with upload speed
        r, p, n = calculate_correlation(metric_values, data['upload_mbps'])
        if r is not None:
            correlations['upload'][metric_name] = {
                'r': r,
                'p_value': p,
                'n': n,
                'strength': 'strong' if abs(r) >= 0.7 else 'moderate' if abs(r) >= 0.4 else 'weak',
                'direction': 'positive' if r > 0 else 'negative'
            }

        # Correlation with ping (expected negative - lower ping is better)
        r, p, n = calculate_correlation(metric_values, data['ping_ms'])
        if r is not None:
            correlations['ping'][metric_name] = {
                'r': r,
                'p_value': p,
                'n': n,
                'strength': 'strong' if abs(r) >= 0.7 else 'moderate' if abs(r) >= 0.4 else 'weak',
                'direction': 'positive' if r > 0 else 'negative'
            }

    # Find thresholds where performance degrades
    thresholds = {}
    for metric_name, metric_values in metrics:
        threshold_info = find_threshold(metric_values, data['download_mbps'])
        if threshold_info:
            thresholds[metric_name] = threshold_info

    # Calculate speed thresholds for good/bad performance
    valid_speeds = [s for s in data['download_mbps'] if s is not None]
    if valid_speeds:
        speed_median = sorted(valid_speeds)[len(valid_speeds) // 2]
        speed_threshold = speed_median * 0.5  # Below 50% of median = bad
    else:
        speed_threshold = 50  # Default 50 Mbps

    # Get metric distributions for good vs bad performance
    distributions = {}
    for metric_name, metric_values in metrics:
        dist = get_metric_distribution(metric_values, data['download_mbps'], speed_threshold)
        if dist['good_performance'] or dist['bad_performance']:
            distributions[metric_name] = dist

    # Prepare scatter plot data
    scatter_data = []
    for i in range(len(data['download_mbps'])):
        point = {
            'download_mbps': data['download_mbps'][i],
            'upload_mbps': data['upload_mbps'][i],
            'ping_ms': data['ping_ms'][i],
            'timestamp': data['timestamps'][i],
            '5g_sinr': data['5g']['sinr'][i],
            '5g_rsrp': data['5g']['rsrp'][i],
            '5g_rsrq': data['5g']['rsrq'][i],
            '4g_sinr': data['4g']['sinr'][i],
            '4g_rsrp': data['4g']['rsrp'][i],
            '4g_rsrq': data['4g']['rsrq'][i],
            'bands_5g': data['bands_5g'][i],
            'bands_4g': data['bands_4g'][i]
        }
        scatter_data.append(point)

    # Summary statistics
    summary = {
        'total_tests': len(results),
        'tests_with_signal': len(data['download_mbps']),
        'speed_threshold_used': round(speed_threshold, 2),
        'avg_download': round(sum(s for s in data['download_mbps'] if s) / len([s for s in data['download_mbps'] if s]), 2) if any(data['download_mbps']) else None,
        'avg_upload': round(sum(s for s in data['upload_mbps'] if s) / len([s for s in data['upload_mbps'] if s]), 2) if any(data['upload_mbps']) else None,
        'best_correlating_metric': None
    }

    # Find best correlating metric
    best_r = 0
    best_metric = None
    for metric_name, corr_info in correlations['download'].items():
        if abs(corr_info['r']) > abs(best_r):
            best_r = corr_info['r']
            best_metric = metric_name
    if best_metric:
        summary['best_correlating_metric'] = {
            'metric': best_metric,
            'r': best_r,
            'interpretation': f"{'Higher' if best_r > 0 else 'Lower'} {best_metric.replace('_', ' ').upper()} values correlate with faster download speeds"
        }

    return {
        'summary': summary,
        'correlations': correlations,
        'thresholds': thresholds,
        'distributions': distributions,
        'scatter_data': scatter_data
    }


class ProxyHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/signal':
            with cache_lock:
                data = cache['data']
                age = time.time() - cache['last_success'] if cache['last_success'] else 999

            if data:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('X-Data-Age-Ms', str(int(age * 1000)))
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(503)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'No data yet'}).encode())

        elif self.path == '/api/stats':
            with cache_lock:
                stats = {
                    'last_success_ago_ms': int((time.time() - cache['last_success']) * 1000) if cache['last_success'] else None,
                    'last_attempt_ago_ms': int((time.time() - cache['last_attempt']) * 1000) if cache['last_attempt'] else None,
                    'success_count': cache['success_count'],
                    'error_count': cache['error_count'],
                    'last_error': cache['last_error'],
                    'poll_interval_ms': int(GATEWAY_POLL_INTERVAL * 1000)
                }
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(stats).encode())

        elif self.path.startswith('/api/history'):
            # Parse query parameters
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            duration = int(params.get('duration', ['60'])[0])  # minutes
            resolution = params.get('resolution', ['auto'])[0]

            # Limit duration to 30 days max
            duration = min(duration, DB_RETENTION_DAYS * 24 * 60)

            history_data = query_history(duration, resolution)

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'duration_minutes': duration,
                'resolution': resolution,
                'count': len(history_data),
                'data': history_data
            }).encode())

        elif self.path == '/api/db-stats':
            stats = get_db_stats()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(stats).encode())

        elif self.path == '/api/advanced':
            # Parse and return advanced metrics from cached gateway data
            with cache_lock:
                data = cache['data']

            if data:
                try:
                    gw_data = json.loads(data)
                    signal = gw_data.get('signal', {})
                    s5g = signal.get('5g', {})
                    s4g = signal.get('4g', {})
                    generic = signal.get('generic', {})

                    # Calculate estimated throughput capacity based on SINR
                    def estimate_capacity(sinr):
                        if sinr is None:
                            return None
                        if sinr >= 25:
                            return {'tier': 'Excellent', 'est_mod': '256QAM', 'efficiency': 'High'}
                        elif sinr >= 15:
                            return {'tier': 'Good', 'est_mod': '64QAM', 'efficiency': 'Good'}
                        elif sinr >= 5:
                            return {'tier': 'Fair', 'est_mod': '16QAM', 'efficiency': 'Moderate'}
                        elif sinr >= 0:
                            return {'tier': 'Poor', 'est_mod': 'QPSK', 'efficiency': 'Low'}
                        else:
                            return {'tier': 'Very Poor', 'est_mod': 'BPSK', 'efficiency': 'Very Low'}

                    # Determine connection mode (NSA vs SA)
                    has_4g = bool(s4g.get('bands', []))
                    has_5g = bool(s5g.get('bands', []))
                    if has_5g and has_4g:
                        conn_mode = 'NSA'
                    elif has_5g:
                        conn_mode = 'SA'
                    elif has_4g:
                        conn_mode = 'LTE'
                    else:
                        conn_mode = 'No Signal'

                    # Band frequency mapping
                    band_info = {
                        'n41': {'freq': '2.5 GHz', 'type': 'Mid-band', 'note': 'Ultra Capacity'},
                        'n71': {'freq': '600 MHz', 'type': 'Low-band', 'note': 'Extended Range'},
                        'n77': {'freq': '3.7 GHz', 'type': 'C-band', 'note': 'High Capacity'},
                        'n260': {'freq': '39 GHz', 'type': 'mmWave', 'note': 'Ultra High Speed'},
                        'n261': {'freq': '28 GHz', 'type': 'mmWave', 'note': 'Ultra High Speed'},
                        'b2': {'freq': '1900 MHz', 'type': 'PCS', 'note': 'Legacy LTE'},
                        'b4': {'freq': '1700/2100 MHz', 'type': 'AWS', 'note': 'Primary LTE'},
                        'b66': {'freq': '1700/2100 MHz', 'type': 'Extended AWS', 'note': 'Primary LTE'},
                        'b12': {'freq': '700 MHz', 'type': 'Lower 700', 'note': 'Extended Range'},
                        'b71': {'freq': '600 MHz', 'type': 'Low-band', 'note': 'Extended Range'},
                        'b41': {'freq': '2.5 GHz', 'type': 'BRS/EBS', 'note': 'High Capacity'},
                    }

                    def get_band_details(bands):
                        details = []
                        for b in bands:
                            b_lower = b.lower()
                            if b_lower in band_info:
                                details.append({**band_info[b_lower], 'band': b})
                            else:
                                details.append({'band': b, 'freq': 'Unknown', 'type': 'Unknown', 'note': ''})
                        return details

                    advanced = {
                        'connectionMode': conn_mode,
                        'registration': generic.get('registration', 'unknown'),
                        'apn': generic.get('apn', 'unknown'),
                        'roaming': generic.get('roaming', False),
                        'hasIPv6': generic.get('hasIPv6', False),
                        '5g': {
                            'bars': s5g.get('bars'),
                            'gNBID': s5g.get('gNBID'),
                            'cid': s5g.get('cid'),
                            'bands': s5g.get('bands', []),
                            'bandDetails': get_band_details(s5g.get('bands', [])),
                            'capacity': estimate_capacity(s5g.get('sinr')),
                        },
                        '4g': {
                            'bars': s4g.get('bars'),
                            'eNBID': s4g.get('eNBID'),
                            'cid': s4g.get('cid'),
                            'bands': s4g.get('bands', []),
                            'bandDetails': get_band_details(s4g.get('bands', [])),
                            'capacity': estimate_capacity(s4g.get('sinr')),
                        },
                        'note': 'CQI/MCS/MIMO metrics not available via gateway API'
                    }

                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps(advanced).encode())
                except Exception as e:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': str(e)}).encode())
            else:
                self.send_response(503)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'No data yet'}).encode())

        elif self.path == '/api/speedtest':
            # Run a speed test (blocking - takes 30-60 seconds)
            result = run_speedtest()
            status_code = 200 if result.get('status') == 'success' else 500 if result.get('status') == 'error' else 409

            self.send_response(status_code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        elif self.path == '/api/speedtest/status':
            # Check if speed test is running
            with speedtest_running_lock:
                running = speedtest_running

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'running': running}).encode())

        elif self.path.startswith('/api/speedtest/history'):
            # Get speed test history
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            limit = int(params.get('limit', ['20'])[0])

            with speedtest_results_lock:
                # Return most recent results first
                results = list(reversed(speedtest_results[-limit:]))

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'count': len(results),
                'results': results
            }).encode())

        elif self.path == '/api/correlation':
            # Signal-to-speed correlation analysis
            analysis = analyze_signal_speed_correlation()
            status_code = 200 if 'error' not in analysis else 400

            self.send_response(status_code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(analysis).encode())

        # ============ DIAGNOSTIC REPORTS ENDPOINTS ============
        elif self.path.startswith('/api/report'):
            # Generate diagnostic report
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            duration = int(params.get('duration', ['24'])[0])  # hours
            format_type = params.get('format', ['json'])[0].lower()

            # Limit duration to 30 days
            duration = min(duration, DB_RETENTION_DAYS * 24)

            try:
                if format_type == 'json':
                    report_data = diagnostic_reports.get_report_json(duration)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Disposition', f'attachment; filename="tmobile_diagnostic_report_{int(time.time())}.json"')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(report_data.encode())

                elif format_type == 'csv':
                    report_data = diagnostic_reports.get_report_csv(duration)
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/csv')
                    self.send_header('Content-Disposition', f'attachment; filename="tmobile_diagnostic_report_{int(time.time())}.csv"')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(report_data.encode())

                elif format_type == 'pdf':
                    pdf_data = diagnostic_reports.get_report_pdf(duration)
                    if pdf_data:
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/pdf')
                        self.send_header('Content-Disposition', f'attachment; filename="tmobile_diagnostic_report_{int(time.time())}.pdf"')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(pdf_data)
                    else:
                        self.send_response(501)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            'error': 'PDF generation not available. Install reportlab: pip install reportlab'
                        }).encode())

                else:
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'error': f'Unknown format: {format_type}. Supported: json, csv, pdf'
                    }).encode())

            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path.startswith('/api/report/summary'):
            # Get quick report summary (no file download)
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            duration = int(params.get('duration', ['24'])[0])
            duration = min(duration, DB_RETENTION_DAYS * 24)

            try:
                report = diagnostic_reports.generate_full_report(duration)
                # Return a lightweight summary
                summary = {
                    'generated_at': report['generated_at'],
                    'duration_hours': report['duration_hours'],
                    'health_score': report['health_score'],
                    'signal_summary': {
                        '5g': {k: v.get('avg') for k, v in report['signal_summary'].get('5g', {}).items() if isinstance(v, dict)},
                        '4g': {k: v.get('avg') for k, v in report['signal_summary'].get('4g', {}).items() if isinstance(v, dict)}
                    },
                    'disruptions': {
                        'total': report['disruptions']['total_disruptions'],
                        'critical': report['disruptions']['critical_count']
                    },
                    'tower_changes': report['tower_history']['total_changes'],
                    'speedtest_count': report['speedtest_history']['count']
                }
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(summary).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path.startswith('/api/disruptions'):
            # Get disruption events
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            duration = int(params.get('duration', ['24'])[0])
            duration = min(duration, DB_RETENTION_DAYS * 24)

            try:
                disruptions = diagnostic_reports.detect_disruptions(duration)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(disruptions).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path.startswith('/api/time-patterns'):
            # Get time-of-day performance patterns
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            duration = int(params.get('duration', ['168'])[0])  # Default 7 days
            duration = min(duration, 168)  # Max 7 days for patterns

            try:
                patterns = diagnostic_reports.get_time_of_day_patterns(duration)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(patterns).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path.startswith('/api/tower-history'):
            # Get tower connection history
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            duration = int(params.get('duration', ['24'])[0])
            duration = min(duration, DB_RETENTION_DAYS * 24)

            try:
                history = diagnostic_reports.get_tower_connection_history(duration)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(history).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        # =================================================================
        # Disruption Detection API Endpoint
        # =================================================================

        elif self.path.startswith('/api/disruption-stats'):
            # Get disruption event statistics
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            duration = int(params.get('duration', ['24'])[0])
            duration = min(duration, DB_RETENTION_DAYS * 24)

            try:
                stats = get_disruption_stats(duration)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(stats).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        # =================================================================
        # Congestion Analysis API Endpoints
        # =================================================================

        elif self.path.startswith('/api/congestion/heatmap'):
            # Get hourly congestion heatmap data
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            days = int(params.get('days', ['7'])[0])
            days = min(days, DB_RETENTION_DAYS)

            try:
                heatmap_data = congestion.get_congestion_heatmap(days)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'period_days': days,
                    'data': heatmap_data
                }).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path.startswith('/api/congestion/daily'):
            # Get daily congestion trends
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            days = int(params.get('days', ['30'])[0])
            days = min(days, DB_RETENTION_DAYS)

            try:
                daily_data = congestion.get_congestion_by_day(days)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'period_days': days,
                    'data': daily_data
                }).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path.startswith('/api/congestion/peaks'):
            # Get peak congestion periods (best/worst times)
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            days = int(params.get('days', ['7'])[0])
            top_n = int(params.get('top', ['5'])[0])
            days = min(days, DB_RETENTION_DAYS)

            try:
                peaks_data = congestion.get_peak_congestion_periods(days, top_n)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'period_days': days,
                    'top_n': top_n,
                    **peaks_data
                }).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path.startswith('/api/congestion/weekday-weekend'):
            # Get weekday vs weekend comparison
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            days = int(params.get('days', ['30'])[0])
            days = min(days, DB_RETENTION_DAYS)

            try:
                comparison_data = congestion.get_weekday_vs_weekend_stats(days)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'period_days': days,
                    **comparison_data
                }).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path.startswith('/api/congestion/summary'):
            # Get comprehensive congestion summary
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            days = int(params.get('days', ['7'])[0])
            days = min(days, DB_RETENTION_DAYS)

            try:
                summary_data = congestion.get_congestion_summary(days)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(summary_data).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path == '/api/congestion/aggregate':
            # Manually trigger hourly aggregation
            try:
                inserted = congestion.aggregate_hourly_metrics()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'status': 'success',
                    'records_aggregated': inserted
                }).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        # =================================================================
        # Support Interaction Logging API Endpoints
        # =================================================================

        elif self.path == '/api/support/interactions':
            # Get all support interactions
            with support_interactions_lock:
                interactions = list(support_interactions)

            # Sort by date descending (most recent first)
            sorted_interactions = sorted(
                interactions,
                key=lambda x: (x.get('contact_date', ''), x.get('contact_time', '')),
                reverse=True
            )

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'count': len(sorted_interactions),
                'interactions': sorted_interactions
            }).encode())

        elif self.path == '/api/support/summary':
            # Get summary statistics
            summary = get_support_interactions_summary()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(summary).encode())

        elif self.path == '/api/support/export':
            # Export for FCC complaint
            export_data = export_support_interactions_for_fcc()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Disposition', f'attachment; filename="support_interactions_fcc_{int(time.time())}.json"')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(export_data, indent=2).encode())

        elif self.path.startswith('/api/support/interaction/'):
            # Get single interaction by ID
            interaction_id = self.path.split('/')[-1].split('?')[0]
            found = None
            with support_interactions_lock:
                for interaction in support_interactions:
                    if interaction['id'] == interaction_id:
                        found = interaction
                        break

            if found:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(found).encode())
            else:
                self.send_response(404)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Interaction not found'}).encode())

        # =================================================================
        # Scheduled Speed Test API Endpoints
        # =================================================================

        elif self.path == '/api/scheduler/status':
            # Get scheduler status and stats
            try:
                status = {
                    'config': scheduler.get_config(),
                    'stats': scheduler.get_stats()
                }
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(status).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path == '/api/scheduler/start':
            # Start the scheduler
            try:
                started = scheduler.start_scheduler()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'status': 'started' if started else 'already_running',
                    'config': scheduler.get_config()
                }).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path == '/api/scheduler/stop':
            # Stop the scheduler
            try:
                stopped = scheduler.stop_scheduler()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'status': 'stopped' if stopped else 'already_stopped',
                    'config': scheduler.get_config()
                }).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path == '/api/scheduler/trigger':
            # Manually trigger a test now
            try:
                result = scheduler.trigger_test_now()
                status_code = 200 if result.get('status') == 'success' else 500
                self.send_response(status_code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path.startswith('/api/scheduler/history'):
            # Get scheduled test history
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            limit = int(params.get('limit', ['100'])[0])
            offset = int(params.get('offset', ['0'])[0])
            status_filter = params.get('status', [None])[0]
            hour_filter = params.get('hour', [None])[0]
            if hour_filter:
                hour_filter = int(hour_filter)

            try:
                history = scheduler.get_scheduled_history(
                    limit=limit,
                    offset=offset,
                    status_filter=status_filter,
                    hour_filter=hour_filter
                )
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(history).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path == '/api/scheduler/hourly-stats':
            # Get hourly statistics for FCC evidence
            try:
                stats = scheduler.get_hourly_stats()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(stats).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path == '/api/scheduler/evidence':
            # Get FCC complaint evidence summary
            try:
                evidence = scheduler.get_evidence_summary()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(evidence).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        # =================================================================
        # FCC Complaint Report API Endpoints
        # =================================================================

        elif self.path.startswith('/api/fcc-report'):
            # Generate FCC complaint report
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            days = int(params.get('days', ['30'])[0])
            days = min(days, DB_RETENTION_DAYS)
            format_type = params.get('format', ['json'])[0].lower()

            try:
                if format_type == 'json':
                    report_data = fcc_complaint_report.get_fcc_report_json(days)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Disposition', f'attachment; filename="fcc_complaint_report_{int(time.time())}.json"')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(report_data.encode())

                elif format_type == 'csv':
                    report_data = fcc_complaint_report.get_fcc_report_csv(days)
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/csv')
                    self.send_header('Content-Disposition', f'attachment; filename="fcc_complaint_report_{int(time.time())}.csv"')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(report_data.encode())

                elif format_type == 'pdf':
                    pdf_data = fcc_complaint_report.get_fcc_report_pdf(days)
                    if pdf_data:
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/pdf')
                        self.send_header('Content-Disposition', f'attachment; filename="fcc_complaint_report_{int(time.time())}.pdf"')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(pdf_data)
                    else:
                        self.send_response(501)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            'error': 'PDF generation not available. Install reportlab: pip install reportlab'
                        }).encode())

                else:
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'error': f'Unknown format: {format_type}. Supported: json, csv, pdf'
                    }).encode())

            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path == '/api/fcc-readiness':
            # Check FCC complaint readiness
            try:
                readiness = fcc_complaint_report.get_fcc_readiness_check()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(readiness).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        # =================================================================
        # Service Terms Documentation API Endpoints
        # =================================================================

        elif self.path == '/api/service-terms':
            # Get service terms documentation
            try:
                terms = service_terms.get_service_terms()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(terms).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path == '/api/service-terms/summary':
            # Get service terms summary for FCC complaint
            try:
                summary = service_terms.get_service_terms_summary()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(summary).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path == '/api/service-terms/fcc-export':
            # Export service terms in FCC complaint format
            try:
                export = service_terms.get_fcc_export()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Disposition', f'attachment; filename="service_terms_fcc_{int(time.time())}.json"')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(export, indent=2).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        # =================================================================
        # Alerting System API Endpoints
        # =================================================================

        elif self.path == '/api/alerts/config' or self.path.startswith('/api/alerts/config?'):
            # Get alert configuration
            try:
                config = alerting.load_alert_config()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(config).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path == '/api/alerts/active':
            # Get active (uncleared) alerts
            try:
                active = alerting.get_active_alerts()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'alerts': active, 'count': len(active)}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path.startswith('/api/alerts/history'):
            # Get alert history
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            limit = int(params.get('limit', ['100'])[0])
            offset = int(params.get('offset', ['0'])[0])

            try:
                history = alerting.get_alert_history(limit=limit, offset=offset)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'alerts': history,
                    'count': len(history),
                    'limit': limit,
                    'offset': offset
                }).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path == '/api/alerts/subscribe':
            # SSE endpoint for real-time alerts
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

            subscriber = alerting.subscribe_sse()
            try:
                # Send initial connection message
                self.wfile.write(b'data: {"type": "connected"}\n\n')
                self.wfile.flush()

                while True:
                    # Check for new messages
                    while subscriber['queue']:
                        message = subscriber['queue'].popleft()
                        self.wfile.write(message.encode() if isinstance(message, str) else message)
                        self.wfile.flush()

                    # Heartbeat
                    self.wfile.write(b': heartbeat\n\n')
                    self.wfile.flush()
                    time.sleep(1)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                alerting.unsubscribe_sse(subscriber)

        elif self.path == '/api/alerts/test':
            # Trigger a test alert
            try:
                alert = alerting.trigger_alert(
                    alert_type='test_alert',
                    severity='info',
                    message='This is a test alert',
                    details={'test': True}
                )
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                if alert:
                    self.wfile.write(json.dumps({'status': 'triggered', 'alert': alert}).encode())
                else:
                    self.wfile.write(json.dumps({'status': 'cooldown', 'message': 'Test alert in cooldown period'}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path == '/api/reload-check':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

            try:
                while True:
                    if reload_flag.is_set():
                        reload_flag.clear()
                        self.wfile.write(b'data: reload\n\n')
                        self.wfile.flush()
                    else:
                        self.wfile.write(b': heartbeat\n\n')
                        self.wfile.flush()
                    time.sleep(1)
            except (BrokenPipeError, ConnectionResetError):
                pass

        else:
            super().do_GET()

    def do_OPTIONS(self):
        """Handle CORS preflight requests"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        """Handle POST requests"""
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)

        if self.path == '/api/support/interactions':
            # Create new support interaction
            try:
                data = json.loads(post_data.decode('utf-8'))
                interaction = add_support_interaction(data)

                self.send_response(201)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(interaction).encode())
            except json.JSONDecodeError as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': f'Invalid JSON: {e}'}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path == '/api/scheduler/config':
            # Update scheduler configuration
            try:
                data = json.loads(post_data.decode('utf-8'))
                updated_config = scheduler.update_config(data)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'status': 'updated',
                    'config': updated_config
                }).encode())
            except json.JSONDecodeError as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': f'Invalid JSON: {e}'}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        # Service Terms POST endpoint
        elif self.path == '/api/service-terms':
            # Update service terms documentation
            try:
                data = json.loads(post_data.decode('utf-8'))
                updated = service_terms.update_service_terms(data)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'updated', 'terms': updated}).encode())
            except json.JSONDecodeError as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': f'Invalid JSON: {e}'}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        # Alerting POST endpoints
        elif self.path == '/api/alerts/config':
            # Update alert configuration
            try:
                data = json.loads(post_data.decode('utf-8'))
                if alerting.save_alert_config(data):
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'status': 'updated', 'config': data}).encode())
                else:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Failed to save configuration'}).encode())
            except json.JSONDecodeError as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': f'Invalid JSON: {e}'}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path.startswith('/api/alerts/acknowledge/'):
            # Acknowledge an alert
            alert_id = self.path.split('/')[-1].split('?')[0]
            try:
                if alerting.acknowledge_alert(alert_id):
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'status': 'acknowledged', 'alert_id': alert_id}).encode())
                else:
                    self.send_response(404)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Alert not found'}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path == '/api/alerts/clear/all':
            # Clear all active alerts
            try:
                count = alerting.clear_all_alerts()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'cleared', 'count': count}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif self.path.startswith('/api/alerts/clear/'):
            # Clear a specific alert
            alert_id = self.path.split('/')[-1].split('?')[0]
            try:
                if alerting.clear_alert(alert_id):
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'status': 'cleared', 'alert_id': alert_id}).encode())
                else:
                    self.send_response(404)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Alert not found'}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        else:
            self.send_response(404)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Not found'}).encode())

    def do_PUT(self):
        """Handle PUT requests"""
        content_length = int(self.headers.get('Content-Length', 0))
        put_data = self.rfile.read(content_length)

        if self.path.startswith('/api/support/interaction/'):
            # Update support interaction
            interaction_id = self.path.split('/')[-1].split('?')[0]
            try:
                data = json.loads(put_data.decode('utf-8'))
                updated = update_support_interaction(interaction_id, data)

                if updated:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps(updated).encode())
                else:
                    self.send_response(404)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Interaction not found'}).encode())
            except json.JSONDecodeError as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': f'Invalid JSON: {e}'}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        else:
            self.send_response(404)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Not found'}).encode())

    def do_DELETE(self):
        """Handle DELETE requests"""
        if self.path.startswith('/api/support/interaction/'):
            # Delete support interaction
            interaction_id = self.path.split('/')[-1].split('?')[0]
            deleted = delete_support_interaction(interaction_id)

            if deleted:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'deleted', 'id': interaction_id}).encode())
            else:
                self.send_response(404)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Interaction not found'}).encode())

        else:
            self.send_response(404)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Not found'}).encode())

    def end_headers(self):
        if not self.path.startswith('/api/'):
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
        super().end_headers()

    def log_message(self, format, *args):
        if '/api/reload-check' not in args[0] and '/api/signal' not in args[0]:
            super().log_message(format, *args)

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

if __name__ == '__main__':
    # Initialize database
    init_database()

    # Load speed test history
    load_speedtest_history()

    # Load support interactions
    load_support_interactions()

    # Initialize scheduler for automated speed tests
    scheduler.init_scheduler(DB_PATH, SCHEDULER_CONFIG_FILE, run_speedtest)

    # Initialize alerting system with data callbacks
    alerting.set_data_callbacks(get_signal_data_for_alerting, get_speedtest_results_for_alerting)
    alerting.start_alert_monitor()
    print('[ALERTING] Alert monitoring started')

    # Start database worker for batch inserts
    db_thread = threading.Thread(target=db_worker, daemon=True)
    db_thread.start()
    print(f'[DATABASE] Batch insert every {DB_BATCH_INTERVAL}s, {DB_RETENTION_DAYS}-day retention')

    gateway_poller = threading.Thread(target=poll_gateway, daemon=True)
    gateway_poller.start()
    print(f'[GATEWAY] Polling every {int(GATEWAY_POLL_INTERVAL * 1000)}ms')

    watcher = threading.Thread(target=watch_files, daemon=True)
    watcher.start()
    print('[HOT RELOAD] File watcher started')

    local_ip = get_local_ip()
    server = ThreadingHTTPServer(('0.0.0.0', 8080), ProxyHandler)
    print(f'Dashboard running at http://localhost:8080')
    print(f'Access from other devices: http://{local_ip}:8080')
    print(f'API endpoints:')
    print(f'  /api/history?duration=60&resolution=auto  - Historical data')
    print(f'  /api/db-stats                             - Database statistics')
    print(f'  /api/advanced                             - Advanced metrics (mode, bands, capacity)')
    print(f'  /api/speedtest                            - Run speed test (POST-like GET)')
    print(f'  /api/speedtest/status                     - Check if test is running')
    print(f'  /api/speedtest/history?limit=20           - Speed test history')
    print(f'  /api/correlation                          - Signal-speed correlation analysis')
    print(f'  /api/report?format=json|csv|pdf&duration=24 - Generate diagnostic report')
    print(f'  /api/report/summary?duration=24           - Quick report summary')
    print(f'  /api/disruptions?duration=24              - Signal disruption events')
    print(f'  /api/time-patterns?duration=168           - Time-of-day performance')
    print(f'  /api/tower-history?duration=24            - Tower connection history')
    print(f'  /api/congestion/heatmap?days=7            - Hourly congestion heatmap')
    print(f'  /api/congestion/peaks?days=7              - Peak congestion periods')
    print(f'  /api/congestion/weekday-weekend?days=30   - Weekday vs weekend patterns')
    print(f'  /api/congestion/summary?days=7            - Comprehensive congestion summary')
    print(f'  /api/support/interactions                 - List/Create support interactions')
    print(f'  /api/support/summary                      - Support interaction statistics')
    print(f'  /api/support/export                       - Export for FCC complaint')
    print(f'  /api/scheduler/status                     - Scheduler status and config')
    print(f'  /api/scheduler/start                      - Start automated speed tests')
    print(f'  /api/scheduler/stop                       - Stop automated speed tests')
    print(f'  /api/scheduler/trigger                    - Run a test now')
    print(f'  /api/scheduler/history                    - Scheduled test history')
    print(f'  /api/scheduler/evidence                   - FCC complaint evidence summary')
    print(f'  POST /api/scheduler/config                - Update scheduler configuration')
    print(f'  /api/fcc-report?format=json|csv|pdf&days=30 - Generate FCC complaint report')
    print(f'  /api/fcc-readiness                        - Check FCC complaint readiness')
    print(f'  /api/service-terms                        - Get/POST service terms documentation')
    print(f'  /api/service-terms/summary                - Service terms summary for FCC')
    print(f'  /api/service-terms/fcc-export             - Export service terms for FCC complaint')
    print(f'  /api/alerts/config                        - Get/POST alert configuration')
    print(f'  /api/alerts/active                        - Active (uncleared) alerts')
    print(f'  /api/alerts/history                       - Alert history')
    print(f'  /api/alerts/subscribe                     - SSE endpoint for real-time alerts')
    print(f'  /api/alerts/test                          - Trigger a test alert')
    print(f'  POST /api/alerts/acknowledge/{id}         - Acknowledge an alert')
    print(f'  POST /api/alerts/clear/{id}               - Clear an alert')
    print(f'  POST /api/alerts/clear/all                - Clear all alerts')
    server.serve_forever()
