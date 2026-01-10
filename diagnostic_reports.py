"""
Diagnostic Reports Module for T-Mobile Home Internet Dashboard

Generates comprehensive diagnostic reports for T-Mobile support including:
- Signal metrics summary (avg, min, max, std dev)
- Disruption event log
- Speed test history
- Time-of-day performance patterns
- Tower/cell connection history

Export formats: PDF, CSV, JSON
"""

import sqlite3
import json
import csv
import io
import statistics
from datetime import datetime, timedelta
from collections import defaultdict
import os

# Try to import optional PDF dependencies
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics.charts.linecharts import HorizontalLineChart
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'signal_history.db')
SPEEDTEST_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'speedtest_history.json')

# Signal quality thresholds for disruption detection
DISRUPTION_THRESHOLDS = {
    'nr_sinr': {'poor': 0, 'critical': -5},
    'nr_rsrp': {'poor': -100, 'critical': -110},
    'lte_sinr': {'poor': 0, 'critical': -5},
    'lte_rsrp': {'poor': -100, 'critical': -110},
}


def get_db_connection():
    """Get SQLite database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def calculate_statistics(values):
    """Calculate statistical summary for a list of values"""
    clean_values = [v for v in values if v is not None]
    if not clean_values:
        return {
            'count': 0,
            'avg': None,
            'min': None,
            'max': None,
            'std_dev': None,
            'median': None
        }

    return {
        'count': len(clean_values),
        'avg': round(statistics.mean(clean_values), 2),
        'min': round(min(clean_values), 2),
        'max': round(max(clean_values), 2),
        'std_dev': round(statistics.stdev(clean_values), 2) if len(clean_values) > 1 else 0,
        'median': round(statistics.median(clean_values), 2)
    }


def get_signal_metrics_summary(duration_hours=24):
    """
    Get signal metrics summary with statistics

    Args:
        duration_hours: How many hours of data to analyze

    Returns:
        Dictionary with 5G and 4G signal statistics
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cutoff = datetime.utcnow() - timedelta(hours=duration_hours)
    cutoff_unix = cutoff.timestamp()

    cursor.execute('''
        SELECT
            nr_sinr, nr_rsrp, nr_rsrq, nr_rssi,
            lte_sinr, lte_rsrp, lte_rsrq, lte_rssi
        FROM signal_history
        WHERE timestamp_unix >= ?
        ORDER BY timestamp_unix ASC
    ''', (cutoff_unix,))

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return {
            'duration_hours': duration_hours,
            'sample_count': 0,
            '5g': {},
            '4g': {}
        }

    # Collect values for each metric
    metrics = {
        '5g': {
            'sinr': [r['nr_sinr'] for r in rows],
            'rsrp': [r['nr_rsrp'] for r in rows],
            'rsrq': [r['nr_rsrq'] for r in rows],
            'rssi': [r['nr_rssi'] for r in rows],
        },
        '4g': {
            'sinr': [r['lte_sinr'] for r in rows],
            'rsrp': [r['lte_rsrp'] for r in rows],
            'rsrq': [r['lte_rsrq'] for r in rows],
            'rssi': [r['lte_rssi'] for r in rows],
        }
    }

    # Calculate statistics for each metric
    summary = {
        'duration_hours': duration_hours,
        'sample_count': len(rows),
        'start_time': datetime.fromtimestamp(rows[0]['timestamp_unix'] if hasattr(rows[0], '__getitem__') else cutoff_unix).isoformat(),
        'end_time': datetime.fromtimestamp(rows[-1]['timestamp_unix'] if hasattr(rows[-1], '__getitem__') else datetime.utcnow().timestamp()).isoformat(),
        '5g': {name: calculate_statistics(values) for name, values in metrics['5g'].items()},
        '4g': {name: calculate_statistics(values) for name, values in metrics['4g'].items()},
    }

    return summary


def detect_disruptions(duration_hours=24):
    """
    Detect signal disruption events based on thresholds

    Args:
        duration_hours: How many hours of data to analyze

    Returns:
        List of disruption events with timestamps and details
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cutoff = datetime.utcnow() - timedelta(hours=duration_hours)
    cutoff_unix = cutoff.timestamp()

    cursor.execute('''
        SELECT
            timestamp, timestamp_unix,
            nr_sinr, nr_rsrp, nr_rsrq,
            lte_sinr, lte_rsrp, lte_rsrq,
            nr_bands, lte_bands, nr_gnb_id, lte_enb_id
        FROM signal_history
        WHERE timestamp_unix >= ?
        ORDER BY timestamp_unix ASC
    ''', (cutoff_unix,))

    rows = cursor.fetchall()
    conn.close()

    disruptions = []
    in_disruption = False
    disruption_start = None
    disruption_metrics = {}

    for row in rows:
        is_poor = False
        severity = 'poor'
        affected_metrics = []

        # Check 5G metrics
        if row['nr_sinr'] is not None:
            if row['nr_sinr'] <= DISRUPTION_THRESHOLDS['nr_sinr']['critical']:
                is_poor = True
                severity = 'critical'
                affected_metrics.append(f"5G SINR: {row['nr_sinr']}dB")
            elif row['nr_sinr'] <= DISRUPTION_THRESHOLDS['nr_sinr']['poor']:
                is_poor = True
                affected_metrics.append(f"5G SINR: {row['nr_sinr']}dB")

        if row['nr_rsrp'] is not None:
            if row['nr_rsrp'] <= DISRUPTION_THRESHOLDS['nr_rsrp']['critical']:
                is_poor = True
                severity = 'critical'
                affected_metrics.append(f"5G RSRP: {row['nr_rsrp']}dBm")
            elif row['nr_rsrp'] <= DISRUPTION_THRESHOLDS['nr_rsrp']['poor']:
                is_poor = True
                affected_metrics.append(f"5G RSRP: {row['nr_rsrp']}dBm")

        # Check 4G metrics
        if row['lte_sinr'] is not None:
            if row['lte_sinr'] <= DISRUPTION_THRESHOLDS['lte_sinr']['critical']:
                is_poor = True
                severity = 'critical'
                affected_metrics.append(f"4G SINR: {row['lte_sinr']}dB")
            elif row['lte_sinr'] <= DISRUPTION_THRESHOLDS['lte_sinr']['poor']:
                is_poor = True
                affected_metrics.append(f"4G SINR: {row['lte_sinr']}dB")

        if row['lte_rsrp'] is not None:
            if row['lte_rsrp'] <= DISRUPTION_THRESHOLDS['lte_rsrp']['critical']:
                is_poor = True
                severity = 'critical'
                affected_metrics.append(f"4G RSRP: {row['lte_rsrp']}dBm")
            elif row['lte_rsrp'] <= DISRUPTION_THRESHOLDS['lte_rsrp']['poor']:
                is_poor = True
                affected_metrics.append(f"4G RSRP: {row['lte_rsrp']}dBm")

        if is_poor and not in_disruption:
            # Start of disruption
            in_disruption = True
            disruption_start = row['timestamp_unix']
            disruption_metrics = {
                'start_time': row['timestamp'],
                'start_unix': row['timestamp_unix'],
                'severity': severity,
                'affected_metrics': affected_metrics,
                'tower_5g': row['nr_gnb_id'],
                'tower_4g': row['lte_enb_id'],
                'bands_5g': row['nr_bands'],
                'bands_4g': row['lte_bands'],
            }
        elif not is_poor and in_disruption:
            # End of disruption
            in_disruption = False
            disruption_metrics['end_time'] = row['timestamp']
            disruption_metrics['end_unix'] = row['timestamp_unix']
            disruption_metrics['duration_seconds'] = round(row['timestamp_unix'] - disruption_start, 1)
            disruptions.append(disruption_metrics)
            disruption_metrics = {}
        elif is_poor and in_disruption:
            # Update severity if worse
            if severity == 'critical':
                disruption_metrics['severity'] = 'critical'

    # Handle ongoing disruption
    if in_disruption and disruption_metrics:
        disruption_metrics['end_time'] = 'ongoing'
        disruption_metrics['end_unix'] = None
        disruption_metrics['duration_seconds'] = round(datetime.utcnow().timestamp() - disruption_start, 1)
        disruptions.append(disruption_metrics)

    return {
        'duration_hours': duration_hours,
        'total_disruptions': len(disruptions),
        'critical_count': sum(1 for d in disruptions if d.get('severity') == 'critical'),
        'events': disruptions
    }


def get_time_of_day_patterns(duration_hours=168):  # Default 7 days
    """
    Analyze signal performance patterns by time of day

    Args:
        duration_hours: How many hours of data to analyze

    Returns:
        Dictionary with hourly performance statistics
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cutoff = datetime.utcnow() - timedelta(hours=duration_hours)
    cutoff_unix = cutoff.timestamp()

    cursor.execute('''
        SELECT
            timestamp, timestamp_unix,
            nr_sinr, nr_rsrp, lte_sinr, lte_rsrp
        FROM signal_history
        WHERE timestamp_unix >= ?
        ORDER BY timestamp_unix ASC
    ''', (cutoff_unix,))

    rows = cursor.fetchall()
    conn.close()

    # Group by hour of day
    hourly_data = defaultdict(lambda: {'nr_sinr': [], 'nr_rsrp': [], 'lte_sinr': [], 'lte_rsrp': []})

    for row in rows:
        hour = datetime.fromtimestamp(row['timestamp_unix']).hour
        if row['nr_sinr'] is not None:
            hourly_data[hour]['nr_sinr'].append(row['nr_sinr'])
        if row['nr_rsrp'] is not None:
            hourly_data[hour]['nr_rsrp'].append(row['nr_rsrp'])
        if row['lte_sinr'] is not None:
            hourly_data[hour]['lte_sinr'].append(row['lte_sinr'])
        if row['lte_rsrp'] is not None:
            hourly_data[hour]['lte_rsrp'].append(row['lte_rsrp'])

    # Calculate statistics per hour
    patterns = {}
    for hour in range(24):
        data = hourly_data[hour]
        patterns[hour] = {
            'hour_label': f"{hour:02d}:00",
            'sample_count': len(data['nr_sinr']),
            '5g_sinr_avg': round(statistics.mean(data['nr_sinr']), 2) if data['nr_sinr'] else None,
            '5g_rsrp_avg': round(statistics.mean(data['nr_rsrp']), 2) if data['nr_rsrp'] else None,
            '4g_sinr_avg': round(statistics.mean(data['lte_sinr']), 2) if data['lte_sinr'] else None,
            '4g_rsrp_avg': round(statistics.mean(data['lte_rsrp']), 2) if data['lte_rsrp'] else None,
        }

    # Find best and worst hours
    valid_hours = [(h, p['5g_sinr_avg']) for h, p in patterns.items() if p['5g_sinr_avg'] is not None]
    if valid_hours:
        best_hour = max(valid_hours, key=lambda x: x[1])
        worst_hour = min(valid_hours, key=lambda x: x[1])
    else:
        best_hour = (None, None)
        worst_hour = (None, None)

    return {
        'duration_hours': duration_hours,
        'hourly_patterns': patterns,
        'best_hour': {'hour': best_hour[0], '5g_sinr_avg': best_hour[1]},
        'worst_hour': {'hour': worst_hour[0], '5g_sinr_avg': worst_hour[1]},
    }


def get_tower_connection_history(duration_hours=24):
    """
    Get history of tower/cell connections

    Args:
        duration_hours: How many hours of data to analyze

    Returns:
        Dictionary with tower connection events and statistics
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cutoff = datetime.utcnow() - timedelta(hours=duration_hours)
    cutoff_unix = cutoff.timestamp()

    cursor.execute('''
        SELECT
            timestamp, timestamp_unix,
            nr_gnb_id, nr_cid, nr_bands,
            lte_enb_id, lte_cid, lte_bands
        FROM signal_history
        WHERE timestamp_unix >= ?
        ORDER BY timestamp_unix ASC
    ''', (cutoff_unix,))

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return {
            'duration_hours': duration_hours,
            'tower_changes': [],
            'tower_summary': {}
        }

    # Track tower changes
    changes = []
    last_5g_tower = None
    last_4g_tower = None
    tower_durations = defaultdict(float)
    last_timestamp = None

    for row in rows:
        current_5g = (row['nr_gnb_id'], row['nr_cid'], row['nr_bands'])
        current_4g = (row['lte_enb_id'], row['lte_cid'], row['lte_bands'])

        # Track duration on current tower
        if last_timestamp is not None:
            duration = row['timestamp_unix'] - last_timestamp
            if last_5g_tower and last_5g_tower[0]:
                tower_durations[f"5G-{last_5g_tower[0]}"] += duration
            if last_4g_tower and last_4g_tower[0]:
                tower_durations[f"4G-{last_4g_tower[0]}"] += duration

        # Check for 5G tower change
        if current_5g != last_5g_tower and current_5g[0] is not None:
            if last_5g_tower is not None:
                changes.append({
                    'timestamp': row['timestamp'],
                    'timestamp_unix': row['timestamp_unix'],
                    'type': '5G',
                    'from_tower': last_5g_tower[0],
                    'from_cell': last_5g_tower[1],
                    'to_tower': current_5g[0],
                    'to_cell': current_5g[1],
                    'bands': current_5g[2],
                })
            last_5g_tower = current_5g

        # Check for 4G tower change
        if current_4g != last_4g_tower and current_4g[0] is not None:
            if last_4g_tower is not None:
                changes.append({
                    'timestamp': row['timestamp'],
                    'timestamp_unix': row['timestamp_unix'],
                    'type': '4G',
                    'from_tower': last_4g_tower[0],
                    'from_cell': last_4g_tower[1],
                    'to_tower': current_4g[0],
                    'to_cell': current_4g[1],
                    'bands': current_4g[2],
                })
            last_4g_tower = current_4g

        last_timestamp = row['timestamp_unix']

    # Calculate tower summary
    tower_summary = {}
    for tower_id, duration in tower_durations.items():
        tower_summary[tower_id] = {
            'duration_seconds': round(duration, 1),
            'duration_formatted': format_duration(duration),
            'percentage': round(duration / (duration_hours * 3600) * 100, 1) if duration_hours > 0 else 0
        }

    return {
        'duration_hours': duration_hours,
        'total_changes': len(changes),
        'tower_changes': changes[-50:],  # Last 50 changes
        'tower_summary': tower_summary,
        'unique_5g_towers': len([k for k in tower_summary if k.startswith('5G')]),
        'unique_4g_towers': len([k for k in tower_summary if k.startswith('4G')]),
    }


def get_speedtest_history():
    """
    Get speed test history

    Returns:
        List of speed test results with signal correlation
    """
    try:
        if os.path.exists(SPEEDTEST_HISTORY_FILE):
            with open(SPEEDTEST_HISTORY_FILE, 'r') as f:
                results = json.load(f)
                return {
                    'count': len(results),
                    'results': results
                }
    except Exception as e:
        return {'error': str(e), 'count': 0, 'results': []}

    return {'count': 0, 'results': []}


def format_duration(seconds):
    """Format duration in seconds to human readable string"""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"


def generate_full_report(duration_hours=24):
    """
    Generate comprehensive diagnostic report

    Args:
        duration_hours: How many hours of data to analyze

    Returns:
        Dictionary with all diagnostic data
    """
    report = {
        'generated_at': datetime.utcnow().isoformat(),
        'duration_hours': duration_hours,
        'signal_summary': get_signal_metrics_summary(duration_hours),
        'disruptions': detect_disruptions(duration_hours),
        'time_patterns': get_time_of_day_patterns(min(duration_hours, 168)),  # Max 7 days for patterns
        'tower_history': get_tower_connection_history(duration_hours),
        'speedtest_history': get_speedtest_history(),
    }

    # Add overall health score
    report['health_score'] = calculate_health_score(report)

    return report


def calculate_health_score(report):
    """
    Calculate overall connection health score (0-100)

    Args:
        report: Full diagnostic report

    Returns:
        Dictionary with score and breakdown
    """
    scores = []
    breakdown = {}

    # Signal quality score (based on averages)
    signal_summary = report.get('signal_summary', {})

    # 5G SINR score
    sinr_5g = signal_summary.get('5g', {}).get('sinr', {}).get('avg')
    if sinr_5g is not None:
        if sinr_5g >= 20:
            sinr_score = 100
        elif sinr_5g >= 10:
            sinr_score = 70
        elif sinr_5g >= 0:
            sinr_score = 40
        else:
            sinr_score = 20
        scores.append(sinr_score)
        breakdown['5g_sinr'] = sinr_score

    # 5G RSRP score
    rsrp_5g = signal_summary.get('5g', {}).get('rsrp', {}).get('avg')
    if rsrp_5g is not None:
        if rsrp_5g >= -80:
            rsrp_score = 100
        elif rsrp_5g >= -90:
            rsrp_score = 70
        elif rsrp_5g >= -100:
            rsrp_score = 40
        else:
            rsrp_score = 20
        scores.append(rsrp_score)
        breakdown['5g_rsrp'] = rsrp_score

    # Disruption penalty
    disruptions = report.get('disruptions', {})
    total_disruptions = disruptions.get('total_disruptions', 0)
    critical_disruptions = disruptions.get('critical_count', 0)

    if total_disruptions == 0:
        stability_score = 100
    elif total_disruptions <= 5:
        stability_score = 80 - (critical_disruptions * 10)
    elif total_disruptions <= 20:
        stability_score = 50 - (critical_disruptions * 5)
    else:
        stability_score = max(0, 30 - (critical_disruptions * 5))

    scores.append(stability_score)
    breakdown['stability'] = stability_score

    # Tower stability score
    tower_history = report.get('tower_history', {})
    tower_changes = tower_history.get('total_changes', 0)

    if tower_changes <= 2:
        tower_score = 100
    elif tower_changes <= 10:
        tower_score = 70
    elif tower_changes <= 50:
        tower_score = 40
    else:
        tower_score = 20

    scores.append(tower_score)
    breakdown['tower_stability'] = tower_score

    overall = round(statistics.mean(scores)) if scores else 0

    # Determine grade
    if overall >= 90:
        grade = 'A'
    elif overall >= 80:
        grade = 'B'
    elif overall >= 70:
        grade = 'C'
    elif overall >= 60:
        grade = 'D'
    else:
        grade = 'F'

    return {
        'overall': overall,
        'grade': grade,
        'breakdown': breakdown
    }


def export_to_json(report):
    """Export report to JSON format"""
    return json.dumps(report, indent=2, default=str)


def export_to_csv(report):
    """Export report data to CSV format (multiple sections)"""
    output = io.StringIO()

    # Signal Summary Section
    output.write("=== T-MOBILE DIAGNOSTIC REPORT ===\n")
    output.write(f"Generated: {report['generated_at']}\n")
    output.write(f"Duration: {report['duration_hours']} hours\n")
    output.write(f"Health Score: {report['health_score']['overall']}/100 ({report['health_score']['grade']})\n\n")

    # Signal metrics
    output.write("=== SIGNAL METRICS SUMMARY ===\n")
    writer = csv.writer(output)
    writer.writerow(['Network', 'Metric', 'Average', 'Min', 'Max', 'Std Dev', 'Median', 'Samples'])

    for network in ['5g', '4g']:
        for metric, stats in report['signal_summary'].get(network, {}).items():
            if isinstance(stats, dict):
                writer.writerow([
                    network.upper(),
                    metric.upper(),
                    stats.get('avg'),
                    stats.get('min'),
                    stats.get('max'),
                    stats.get('std_dev'),
                    stats.get('median'),
                    stats.get('count')
                ])

    output.write("\n")

    # Disruptions
    output.write("=== DISRUPTION EVENTS ===\n")
    writer.writerow(['Start Time', 'End Time', 'Duration (s)', 'Severity', 'Tower 5G', 'Tower 4G', 'Affected Metrics'])

    for event in report['disruptions'].get('events', []):
        writer.writerow([
            event.get('start_time'),
            event.get('end_time'),
            event.get('duration_seconds'),
            event.get('severity'),
            event.get('tower_5g'),
            event.get('tower_4g'),
            '; '.join(event.get('affected_metrics', []))
        ])

    output.write("\n")

    # Time of Day Patterns
    output.write("=== TIME OF DAY PATTERNS ===\n")
    writer.writerow(['Hour', 'Samples', '5G SINR Avg', '5G RSRP Avg', '4G SINR Avg', '4G RSRP Avg'])

    for hour, pattern in sorted(report['time_patterns'].get('hourly_patterns', {}).items()):
        writer.writerow([
            pattern.get('hour_label'),
            pattern.get('sample_count'),
            pattern.get('5g_sinr_avg'),
            pattern.get('5g_rsrp_avg'),
            pattern.get('4g_sinr_avg'),
            pattern.get('4g_rsrp_avg')
        ])

    output.write("\n")

    # Tower History
    output.write("=== TOWER CONNECTION SUMMARY ===\n")
    writer.writerow(['Tower ID', 'Duration', 'Percentage'])

    for tower_id, stats in report['tower_history'].get('tower_summary', {}).items():
        writer.writerow([
            tower_id,
            stats.get('duration_formatted'),
            f"{stats.get('percentage')}%"
        ])

    output.write("\n")

    # Speed Tests
    output.write("=== SPEED TEST HISTORY ===\n")
    writer.writerow(['Timestamp', 'Download (Mbps)', 'Upload (Mbps)', 'Ping (ms)', 'Server', '5G SINR', '5G RSRP'])

    for test in report['speedtest_history'].get('results', []):
        signal = test.get('signal_at_test', {})
        sig_5g = signal.get('5g', {}) if signal else {}
        writer.writerow([
            test.get('timestamp'),
            test.get('download_mbps'),
            test.get('upload_mbps'),
            test.get('ping_ms'),
            test.get('server', {}).get('name') if test.get('server') else 'N/A',
            sig_5g.get('sinr'),
            sig_5g.get('rsrp')
        ])

    return output.getvalue()


def export_to_pdf(report):
    """Export report to PDF format with charts"""
    if not REPORTLAB_AVAILABLE:
        return None

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                          rightMargin=72, leftMargin=72,
                          topMargin=72, bottomMargin=72)

    styles = getSampleStyleSheet()
    story = []

    # Title
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        spaceAfter=30,
        textColor=colors.HexColor('#e20074')
    )
    story.append(Paragraph("T-Mobile Signal Diagnostic Report", title_style))
    story.append(Spacer(1, 12))

    # Report Info
    info_data = [
        ['Generated:', report['generated_at']],
        ['Duration:', f"{report['duration_hours']} hours"],
        ['Health Score:', f"{report['health_score']['overall']}/100 ({report['health_score']['grade']})"],
    ]
    info_table = Table(info_data, colWidths=[100, 300])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('TEXTCOLOR', (1, 2), (1, 2), colors.HexColor('#e20074')),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 20))

    # Signal Summary Section
    story.append(Paragraph("Signal Metrics Summary", styles['Heading2']))
    story.append(Spacer(1, 10))

    # 5G Metrics Table
    story.append(Paragraph("5G NR Metrics", styles['Heading3']))
    metrics_5g = report['signal_summary'].get('5g', {})
    if metrics_5g:
        data = [['Metric', 'Average', 'Min', 'Max', 'Std Dev']]
        for metric, stats in metrics_5g.items():
            if isinstance(stats, dict):
                data.append([
                    metric.upper(),
                    str(stats.get('avg', 'N/A')),
                    str(stats.get('min', 'N/A')),
                    str(stats.get('max', 'N/A')),
                    str(stats.get('std_dev', 'N/A'))
                ])

        table = Table(data, colWidths=[80, 80, 80, 80, 80])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e20074')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8f8f8')),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#cccccc')),
        ]))
        story.append(table)
    story.append(Spacer(1, 15))

    # 4G Metrics Table
    story.append(Paragraph("4G LTE Metrics", styles['Heading3']))
    metrics_4g = report['signal_summary'].get('4g', {})
    if metrics_4g:
        data = [['Metric', 'Average', 'Min', 'Max', 'Std Dev']]
        for metric, stats in metrics_4g.items():
            if isinstance(stats, dict):
                data.append([
                    metric.upper(),
                    str(stats.get('avg', 'N/A')),
                    str(stats.get('min', 'N/A')),
                    str(stats.get('max', 'N/A')),
                    str(stats.get('std_dev', 'N/A'))
                ])

        table = Table(data, colWidths=[80, 80, 80, 80, 80])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e20074')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8f8f8')),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#cccccc')),
        ]))
        story.append(table)
    story.append(Spacer(1, 20))

    # Disruptions Section
    story.append(Paragraph("Signal Disruptions", styles['Heading2']))
    disruptions = report['disruptions']
    story.append(Paragraph(f"Total Events: {disruptions['total_disruptions']} (Critical: {disruptions['critical_count']})", styles['Normal']))
    story.append(Spacer(1, 10))

    if disruptions['events']:
        data = [['Start Time', 'Duration', 'Severity', 'Details']]
        for event in disruptions['events'][:20]:  # Limit to 20 events
            data.append([
                event.get('start_time', 'N/A')[:19] if event.get('start_time') else 'N/A',
                f"{event.get('duration_seconds', 0)}s",
                event.get('severity', 'N/A').upper(),
                '; '.join(event.get('affected_metrics', [])[:2])
            ])

        table = Table(data, colWidths=[120, 60, 60, 200])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#333333')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
        ]))
        story.append(table)
    else:
        story.append(Paragraph("No disruption events detected.", styles['Normal']))

    story.append(Spacer(1, 20))

    # Tower History Section
    story.append(Paragraph("Tower Connection Summary", styles['Heading2']))
    tower_history = report['tower_history']
    story.append(Paragraph(f"Total Handoffs: {tower_history['total_changes']} | Unique 5G Towers: {tower_history['unique_5g_towers']} | Unique 4G Towers: {tower_history['unique_4g_towers']}", styles['Normal']))
    story.append(Spacer(1, 10))

    if tower_history['tower_summary']:
        data = [['Tower ID', 'Duration', 'Percentage']]
        for tower_id, stats in sorted(tower_history['tower_summary'].items(),
                                     key=lambda x: x[1]['duration_seconds'], reverse=True)[:10]:
            data.append([
                tower_id,
                stats.get('duration_formatted', 'N/A'),
                f"{stats.get('percentage', 0)}%"
            ])

        table = Table(data, colWidths=[150, 100, 80])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#333333')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
        ]))
        story.append(table)

    story.append(Spacer(1, 20))

    # Speed Test History
    story.append(Paragraph("Speed Test History", styles['Heading2']))
    speedtests = report['speedtest_history'].get('results', [])

    if speedtests:
        data = [['Date', 'Download', 'Upload', 'Ping', '5G SINR']]
        for test in speedtests[:10]:
            signal = test.get('signal_at_test', {})
            sig_5g = signal.get('5g', {}) if signal else {}
            timestamp = test.get('timestamp', '')[:10] if test.get('timestamp') else 'N/A'
            data.append([
                timestamp,
                f"{test.get('download_mbps', 'N/A')} Mbps",
                f"{test.get('upload_mbps', 'N/A')} Mbps",
                f"{test.get('ping_ms', 'N/A')} ms",
                f"{sig_5g.get('sinr', 'N/A')} dB" if sig_5g.get('sinr') else 'N/A'
            ])

        table = Table(data, colWidths=[80, 90, 90, 70, 70])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#333333')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
        ]))
        story.append(table)
    else:
        story.append(Paragraph("No speed test results available.", styles['Normal']))

    story.append(Spacer(1, 20))

    # Time of Day Patterns
    story.append(Paragraph("Time of Day Performance", styles['Heading2']))
    patterns = report['time_patterns']
    best = patterns.get('best_hour', {})
    worst = patterns.get('worst_hour', {})

    if best.get('hour') is not None:
        story.append(Paragraph(f"Best Performance: {best['hour']:02d}:00 (5G SINR avg: {best['5g_sinr_avg']} dB)", styles['Normal']))
    if worst.get('hour') is not None:
        story.append(Paragraph(f"Worst Performance: {worst['hour']:02d}:00 (5G SINR avg: {worst['5g_sinr_avg']} dB)", styles['Normal']))

    # Build PDF
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


# API helper functions
def get_report_json(duration_hours=24):
    """Generate report and return as JSON string"""
    report = generate_full_report(duration_hours)
    return export_to_json(report)


def get_report_csv(duration_hours=24):
    """Generate report and return as CSV string"""
    report = generate_full_report(duration_hours)
    return export_to_csv(report)


def get_report_pdf(duration_hours=24):
    """Generate report and return as PDF bytes"""
    report = generate_full_report(duration_hours)
    return export_to_pdf(report)


if __name__ == '__main__':
    # Test report generation
    print("Generating diagnostic report...")
    report = generate_full_report(24)
    print(f"\nHealth Score: {report['health_score']['overall']}/100 ({report['health_score']['grade']})")
    print(f"Disruptions: {report['disruptions']['total_disruptions']}")
    print(f"Tower changes: {report['tower_history']['total_changes']}")
    print(f"Speed tests: {report['speedtest_history']['count']}")

    # Test exports
    print("\nTesting CSV export...")
    csv_data = export_to_csv(report)
    print(f"CSV length: {len(csv_data)} chars")

    print("\nTesting JSON export...")
    json_data = export_to_json(report)
    print(f"JSON length: {len(json_data)} chars")

    if REPORTLAB_AVAILABLE:
        print("\nTesting PDF export...")
        pdf_data = export_to_pdf(report)
        if pdf_data:
            print(f"PDF size: {len(pdf_data)} bytes")
    else:
        print("\nPDF export not available (install reportlab)")
