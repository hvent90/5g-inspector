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
from datetime import datetime, timedelta
from collections import deque

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
GATEWAY_POLL_INTERVAL = 0.2  # 200ms
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'signal_history.db')
DB_BATCH_INTERVAL = 5  # seconds between batch inserts
DB_RETENTION_DAYS = 30  # how long to keep data

# Buffer for batch inserts
db_buffer = deque(maxlen=1000)
db_buffer_lock = threading.Lock()

# Speed test state
speedtest_results = []  # In-memory storage for speed test results
speedtest_results_lock = threading.Lock()
speedtest_running = False
speedtest_running_lock = threading.Lock()
SPEEDTEST_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'speedtest_history.json')

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
            result = subprocess.run(
                ['speedtest-cli', '--json'],
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

def buffer_signal_data(raw_data):
    """Add parsed signal data to buffer for batch insert"""
    parsed = parse_signal_data(raw_data)
    if parsed:
        with db_buffer_lock:
            db_buffer.append(parsed)

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
    """Background thread for batch inserts and cleanup"""
    last_cleanup = time.time()
    cleanup_interval = 3600  # Check for cleanup every hour

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
    server.serve_forever()
