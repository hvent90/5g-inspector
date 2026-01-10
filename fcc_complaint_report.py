"""
FCC Complaint Report Generator for T-Mobile Home Internet Dashboard

Generates comprehensive reports specifically formatted for FCC complaint submission.
Aggregates evidence from multiple sources:
- Speed test history (advertised vs actual speeds)
- Support interaction logs (documentation of T-Mobile response)
- Signal metrics (proving good signal with poor performance)
- Congestion analysis (time-of-day patterns)

Output formats:
- JSON: For online form submission
- CSV: Raw data attachment
- PDF: Formatted narrative for submission
"""

import json
import csv
import io
import os
import sqlite3
import statistics
from datetime import datetime, timedelta
from collections import defaultdict

# Try to import optional PDF dependencies
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, ListFlowable, ListItem
    )
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

# Paths
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'signal_history.db')
SPEEDTEST_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'speedtest_history.json')
SUPPORT_INTERACTIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'support_interactions.json')

# T-Mobile advertised speeds (user reported)
ADVERTISED_SPEEDS = {
    'download_min': 133,
    'download_max': 415,
    'upload_min': 12,
    'upload_max': 55,
    'latency_min': 16,
    'latency_max': 28,
    'source': 'T-Mobile website speed check for service address'
}

# FCC complaint threshold - speeds below this are considered unacceptable
SPEED_THRESHOLD_MBPS = 10


def get_db_connection():
    """Get SQLite database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_speedtest_history():
    """Load speed test history from file."""
    try:
        if os.path.exists(SPEEDTEST_HISTORY_FILE):
            with open(SPEEDTEST_HISTORY_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f'[FCC_REPORT] Error loading speedtest history: {e}')
    return []


def get_scheduled_speedtests(days=30):
    """Get scheduled speed test results from database."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cutoff = (datetime.now() - timedelta(days=days)).timestamp()

        cursor.execute('''
            SELECT
                timestamp, timestamp_unix, download_mbps, upload_mbps, ping_ms,
                server_name, server_location, client_ip, client_isp,
                nr_sinr, nr_rsrp, nr_bands, lte_sinr, lte_rsrp, lte_bands,
                status, below_threshold, hour_of_day, day_of_week, is_weekend
            FROM scheduled_speedtests
            WHERE status = 'success' AND timestamp_unix >= ?
            ORDER BY timestamp_unix ASC
        ''', (cutoff,))

        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        print(f'[FCC_REPORT] Error loading scheduled tests: {e}')
        return []
    finally:
        conn.close()


def get_support_interactions():
    """Load support interaction history."""
    try:
        if os.path.exists(SUPPORT_INTERACTIONS_FILE):
            with open(SUPPORT_INTERACTIONS_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f'[FCC_REPORT] Error loading support interactions: {e}')
    return []


def get_signal_summary(days=30):
    """Get signal metrics summary from database."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cutoff = (datetime.now() - timedelta(days=days)).timestamp()

        cursor.execute('''
            SELECT
                AVG(nr_sinr) as avg_5g_sinr,
                MIN(nr_sinr) as min_5g_sinr,
                MAX(nr_sinr) as max_5g_sinr,
                AVG(nr_rsrp) as avg_5g_rsrp,
                MIN(nr_rsrp) as min_5g_rsrp,
                MAX(nr_rsrp) as max_5g_rsrp,
                AVG(lte_sinr) as avg_4g_sinr,
                MIN(lte_sinr) as min_4g_sinr,
                MAX(lte_sinr) as max_4g_sinr,
                AVG(lte_rsrp) as avg_4g_rsrp,
                MIN(lte_rsrp) as min_4g_rsrp,
                MAX(lte_rsrp) as max_4g_rsrp,
                COUNT(*) as sample_count
            FROM signal_history
            WHERE timestamp_unix >= ?
        ''', (cutoff,))

        row = cursor.fetchone()
        return dict(row) if row else {}
    except Exception as e:
        print(f'[FCC_REPORT] Error getting signal summary: {e}')
        return {}
    finally:
        conn.close()


def get_time_of_day_speed_analysis(speedtests):
    """Analyze speed patterns by time of day."""
    hourly_speeds = defaultdict(list)

    for test in speedtests:
        hour = test.get('hour_of_day')
        if hour is None:
            # Parse from timestamp if not stored
            try:
                ts = datetime.fromisoformat(test.get('timestamp', ''))
                hour = ts.hour
            except:
                continue

        download = test.get('download_mbps')
        if download is not None:
            hourly_speeds[hour].append(download)

    analysis = {}
    for hour in range(24):
        speeds = hourly_speeds.get(hour, [])
        if speeds:
            analysis[hour] = {
                'hour': hour,
                'hour_label': f'{hour:02d}:00',
                'test_count': len(speeds),
                'avg_download': round(statistics.mean(speeds), 2),
                'min_download': round(min(speeds), 2),
                'max_download': round(max(speeds), 2),
                'below_threshold_count': sum(1 for s in speeds if s < SPEED_THRESHOLD_MBPS),
                'below_threshold_pct': round(sum(1 for s in speeds if s < SPEED_THRESHOLD_MBPS) / len(speeds) * 100, 1)
            }

    return analysis


def assess_signal_quality(signal_summary):
    """
    Assess whether signal quality is acceptable.

    SINR thresholds:
    - Excellent: > 20 dB
    - Good: 10-20 dB
    - Fair: 0-10 dB
    - Poor: < 0 dB

    RSRP thresholds:
    - Excellent: > -80 dBm
    - Good: -80 to -90 dBm
    - Fair: -90 to -100 dBm
    - Poor: < -100 dBm
    """
    assessment = {
        'is_acceptable': False,
        'sinr_quality': 'unknown',
        'rsrp_quality': 'unknown',
        'conclusion': ''
    }

    sinr_5g = signal_summary.get('avg_5g_sinr')
    rsrp_5g = signal_summary.get('avg_5g_rsrp')

    # Assess SINR
    if sinr_5g is not None:
        if sinr_5g > 20:
            assessment['sinr_quality'] = 'excellent'
        elif sinr_5g > 10:
            assessment['sinr_quality'] = 'good'
        elif sinr_5g > 0:
            assessment['sinr_quality'] = 'fair'
        else:
            assessment['sinr_quality'] = 'poor'

    # Assess RSRP
    if rsrp_5g is not None:
        if rsrp_5g > -80:
            assessment['rsrp_quality'] = 'excellent'
        elif rsrp_5g > -90:
            assessment['rsrp_quality'] = 'good'
        elif rsrp_5g > -100:
            assessment['rsrp_quality'] = 'fair'
        else:
            assessment['rsrp_quality'] = 'poor'

    # Determine if signal is acceptable (fair or better)
    acceptable_levels = ['excellent', 'good', 'fair']
    assessment['is_acceptable'] = (
        assessment['sinr_quality'] in acceptable_levels or
        assessment['rsrp_quality'] in acceptable_levels
    )

    # Generate conclusion
    if assessment['is_acceptable']:
        assessment['conclusion'] = (
            f"Signal quality is {assessment['sinr_quality']} (SINR: {sinr_5g:.1f} dB) "
            f"with {assessment['rsrp_quality']} signal strength (RSRP: {rsrp_5g:.1f} dBm). "
            "This indicates the poor speeds are NOT due to signal/coverage issues, "
            "but rather network congestion, capacity limitations, or QoS deprioritization."
        )
    else:
        assessment['conclusion'] = (
            f"Signal quality is {assessment['sinr_quality']} (SINR: {sinr_5g:.1f if sinr_5g else 'N/A'} dB). "
            "Poor signal may be contributing to speed issues, but does not explain "
            "consistent underperformance across all time periods."
        )

    return assessment


def generate_speed_correlation_analysis(speedtests, signal_summary):
    """Analyze correlation between signal quality and speed."""
    # Group tests by signal quality at test time
    tests_with_signal = [t for t in speedtests if t.get('nr_sinr') is not None]

    if not tests_with_signal:
        return None

    # Calculate correlation data
    good_signal_tests = [t for t in tests_with_signal if t.get('nr_sinr', 0) > 10]
    fair_signal_tests = [t for t in tests_with_signal if 0 <= t.get('nr_sinr', 0) <= 10]
    poor_signal_tests = [t for t in tests_with_signal if t.get('nr_sinr', 0) < 0]

    def calc_stats(tests):
        speeds = [t.get('download_mbps', 0) for t in tests if t.get('download_mbps')]
        if not speeds:
            return None
        return {
            'count': len(speeds),
            'avg_speed': round(statistics.mean(speeds), 2),
            'min_speed': round(min(speeds), 2),
            'max_speed': round(max(speeds), 2)
        }

    return {
        'good_signal_tests': calc_stats(good_signal_tests),
        'fair_signal_tests': calc_stats(fair_signal_tests),
        'poor_signal_tests': calc_stats(poor_signal_tests),
        'conclusion': None  # Will be set below
    }


def generate_fcc_complaint_data(days=30):
    """
    Generate comprehensive FCC complaint data package.

    Returns a dictionary with all evidence sections.
    """
    # Collect all data sources
    manual_tests = get_speedtest_history()
    scheduled_tests = get_scheduled_speedtests(days)
    support_interactions = get_support_interactions()
    signal_summary = get_signal_summary(days)

    # Combine all speed tests
    all_tests = scheduled_tests + manual_tests

    # Calculate overall speed statistics
    successful_tests = [t for t in all_tests if t.get('download_mbps') is not None]

    if not successful_tests:
        return {
            'error': 'No speed test data available',
            'recommendation': 'Run scheduled speed tests for at least 7 days before generating FCC complaint'
        }

    download_speeds = [t['download_mbps'] for t in successful_tests]
    upload_speeds = [t['upload_mbps'] for t in successful_tests if t.get('upload_mbps')]
    ping_times = [t['ping_ms'] for t in successful_tests if t.get('ping_ms')]

    # Calculate date range
    timestamps = [t.get('timestamp_unix', 0) for t in successful_tests]
    first_test = min(timestamps) if timestamps else None
    last_test = max(timestamps) if timestamps else None
    collection_days = (last_test - first_test) / 86400 if first_test and last_test else 0

    # Speed statistics
    avg_download = statistics.mean(download_speeds)
    min_download = min(download_speeds)
    max_download = max(download_speeds)
    median_download = statistics.median(download_speeds)
    tests_below_threshold = sum(1 for s in download_speeds if s < SPEED_THRESHOLD_MBPS)

    # Time of day analysis
    tod_analysis = get_time_of_day_speed_analysis(scheduled_tests)

    # Find best performing hour (likely 2am based on issue description)
    best_hour = None
    worst_hour = None
    if tod_analysis:
        sorted_hours = sorted(tod_analysis.values(), key=lambda x: x['avg_download'])
        if sorted_hours:
            worst_hour = sorted_hours[0]
            best_hour = sorted_hours[-1]

    # Signal quality assessment
    signal_assessment = assess_signal_quality(signal_summary)

    # Signal-speed correlation
    correlation = generate_speed_correlation_analysis(scheduled_tests, signal_summary)

    # Support interaction summary
    support_summary = {
        'total_contacts': len(support_interactions),
        'unresolved_count': sum(1 for i in support_interactions
                                if i.get('resolution_status') == 'unresolved'),
        'interactions': support_interactions
    }

    # Calculate advertised vs actual comparison
    actual_pct_of_min = (avg_download / ADVERTISED_SPEEDS['download_min'] * 100) if avg_download else 0
    shortfall = ADVERTISED_SPEEDS['download_min'] - avg_download if avg_download < ADVERTISED_SPEEDS['download_min'] else 0

    return {
        'report_generated': datetime.now().isoformat(),
        'collection_period': {
            'start_date': datetime.fromtimestamp(first_test).isoformat() if first_test else None,
            'end_date': datetime.fromtimestamp(last_test).isoformat() if last_test else None,
            'total_days': round(collection_days, 1),
            'total_tests': len(successful_tests)
        },

        # Section 1: Service Agreement Violation
        'service_violation': {
            'advertised_speeds': ADVERTISED_SPEEDS,
            'actual_speeds': {
                'download_avg_mbps': round(avg_download, 2),
                'download_min_mbps': round(min_download, 2),
                'download_max_mbps': round(max_download, 2),
                'download_median_mbps': round(median_download, 2),
                'upload_avg_mbps': round(statistics.mean(upload_speeds), 2) if upload_speeds else None,
                'ping_avg_ms': round(statistics.mean(ping_times), 1) if ping_times else None
            },
            'comparison': {
                'percent_of_min_advertised': round(actual_pct_of_min, 1),
                'shortfall_mbps': round(shortfall, 2),
                'tests_below_10mbps': tests_below_threshold,
                'tests_below_10mbps_pct': round(tests_below_threshold / len(download_speeds) * 100, 1)
            }
        },

        # Section 2: Duration & Pattern Analysis
        'duration_pattern': {
            'ongoing_months': round(collection_days / 30, 1),
            'affects_all_hours': worst_hour is not None and worst_hour['below_threshold_pct'] > 50 if worst_hour else False,
            'time_of_day_analysis': tod_analysis,
            'best_hour': best_hour,
            'worst_hour': worst_hour,
            'pattern_conclusion': _generate_pattern_conclusion(tod_analysis, best_hour, worst_hour)
        },

        # Section 3: Signal Quality Analysis (proving congestion, not coverage)
        'signal_analysis': {
            'metrics': {
                '5g_sinr_avg_db': round(signal_summary.get('avg_5g_sinr', 0), 1) if signal_summary.get('avg_5g_sinr') else None,
                '5g_rsrp_avg_dbm': round(signal_summary.get('avg_5g_rsrp', 0), 1) if signal_summary.get('avg_5g_rsrp') else None,
                '4g_sinr_avg_db': round(signal_summary.get('avg_4g_sinr', 0), 1) if signal_summary.get('avg_4g_sinr') else None,
                '4g_rsrp_avg_dbm': round(signal_summary.get('avg_4g_rsrp', 0), 1) if signal_summary.get('avg_4g_rsrp') else None,
                'sample_count': signal_summary.get('sample_count', 0)
            },
            'assessment': signal_assessment,
            'correlation': correlation
        },

        # Section 4: Support Interactions
        'support_interactions': support_summary,

        # Section 5: Generated Narrative
        'fcc_narrative': _generate_fcc_narrative(
            collection_days, len(successful_tests), avg_download, tests_below_threshold,
            signal_assessment, support_summary, best_hour, ADVERTISED_SPEEDS
        ),

        # Raw data for CSV export
        'raw_speedtest_data': successful_tests
    }


def _generate_pattern_conclusion(tod_analysis, best_hour, worst_hour):
    """Generate conclusion about time-of-day patterns."""
    if not tod_analysis or not best_hour or not worst_hour:
        return "Insufficient data to determine time-of-day patterns."

    # Check if there's significant variation between best and worst hours
    speed_diff = best_hour['avg_download'] - worst_hour['avg_download']
    speed_ratio = best_hour['avg_download'] / worst_hour['avg_download'] if worst_hour['avg_download'] > 0 else 1

    if speed_ratio > 2 and best_hour['hour'] in [0, 1, 2, 3, 4, 5]:
        return (
            f"Significant time-of-day variation detected. Best performance at {best_hour['hour_label']} "
            f"({best_hour['avg_download']:.1f} Mbps) vs worst at {worst_hour['hour_label']} "
            f"({worst_hour['avg_download']:.1f} Mbps). Performance improves {speed_ratio:.1f}x during "
            "off-peak hours (late night/early morning), strongly indicating network congestion "
            "rather than coverage or equipment issues."
        )
    elif speed_ratio > 1.5:
        return (
            f"Moderate time-of-day variation. Best: {best_hour['avg_download']:.1f} Mbps at "
            f"{best_hour['hour_label']}, Worst: {worst_hour['avg_download']:.1f} Mbps at "
            f"{worst_hour['hour_label']}. This pattern suggests network capacity issues."
        )
    else:
        return (
            f"Speeds consistently poor across all hours (avg {worst_hour['avg_download']:.1f} - "
            f"{best_hour['avg_download']:.1f} Mbps), indicating persistent service degradation "
            "regardless of time of day."
        )


def _generate_fcc_narrative(collection_days, test_count, avg_download, tests_below_threshold,
                            signal_assessment, support_summary, best_hour, advertised):
    """Generate the narrative summary for FCC complaint submission."""
    narrative_parts = []

    # Opening
    narrative_parts.append(
        f"I have documented T-Mobile Home Internet service performance over a period of "
        f"{collection_days:.0f} days, conducting {test_count} speed tests to demonstrate "
        f"consistent underperformance relative to advertised speeds."
    )

    # Speed comparison
    pct_of_min = (avg_download / advertised['download_min'] * 100)
    narrative_parts.append(
        f"\nADVERTISED VS ACTUAL SPEEDS:\n"
        f"T-Mobile advertises download speeds of {advertised['download_min']}-{advertised['download_max']} Mbps "
        f"for my service address. My documented average download speed is {avg_download:.1f} Mbps, "
        f"which is only {pct_of_min:.1f}% of the minimum advertised speed. "
        f"{tests_below_threshold} of {test_count} tests ({tests_below_threshold/test_count*100:.1f}%) "
        f"recorded speeds below 10 Mbps."
    )

    # Signal quality (proving it's not a coverage issue)
    if signal_assessment['is_acceptable']:
        narrative_parts.append(
            f"\nSIGNAL QUALITY ANALYSIS:\n"
            f"{signal_assessment['conclusion']} "
            "The disconnect between acceptable signal metrics and poor speed performance "
            "demonstrates this is a network capacity or policy issue, not a coverage problem."
        )

    # Time of day pattern
    if best_hour and best_hour['avg_download'] > avg_download * 1.5:
        narrative_parts.append(
            f"\nTIME-OF-DAY PATTERN:\n"
            f"Speed tests show that performance improves significantly during off-peak hours "
            f"(best: {best_hour['avg_download']:.1f} Mbps at {best_hour['hour_label']}), "
            "further confirming network congestion as the root cause."
        )

    # Support interactions
    if support_summary['total_contacts'] > 0:
        narrative_parts.append(
            f"\nSUPPORT INTERACTIONS:\n"
            f"I have contacted T-Mobile support {support_summary['total_contacts']} time(s) "
            f"regarding these issues. {support_summary['unresolved_count']} issue(s) remain unresolved."
        )

        # Include notable dismissive responses
        for interaction in support_summary.get('interactions', []):
            response = interaction.get('response_received', '')
            if response and any(phrase in response.lower() for phrase in
                               ["don't matter", "youtube", "streaming", "can't help"]):
                narrative_parts.append(f'  - Agent response: "{response}"')

    # Resolution requested
    narrative_parts.append(
        "\nRESOLUTION REQUESTED:\n"
        "I request that the FCC investigate T-Mobile's failure to deliver advertised service levels "
        "and take appropriate action. Specifically, I seek: (1) service improvement to advertised speeds, "
        "(2) billing adjustment/credit for months of substandard service, and/or (3) release from "
        "service agreement without penalty due to T-Mobile's failure to deliver promised service."
    )

    return '\n'.join(narrative_parts)


def export_to_json(report_data):
    """Export FCC complaint report to JSON format."""
    # Remove raw data for cleaner JSON (it's available separately)
    export_data = {k: v for k, v in report_data.items() if k != 'raw_speedtest_data'}
    return json.dumps(export_data, indent=2, default=str)


def export_to_csv(report_data):
    """Export FCC complaint report data to CSV format."""
    output = io.StringIO()

    # Header info
    output.write("=== FCC COMPLAINT EVIDENCE REPORT ===\n")
    output.write(f"Generated: {report_data['report_generated']}\n")
    output.write(f"Collection Period: {report_data['collection_period']['start_date']} to {report_data['collection_period']['end_date']}\n")
    output.write(f"Total Tests: {report_data['collection_period']['total_tests']}\n")
    output.write(f"Days Collected: {report_data['collection_period']['total_days']}\n\n")

    # Advertised vs Actual
    output.write("=== SERVICE AGREEMENT VIOLATION ===\n")
    sv = report_data['service_violation']
    output.write(f"Advertised Download: {sv['advertised_speeds']['download_min']}-{sv['advertised_speeds']['download_max']} Mbps\n")
    output.write(f"Actual Avg Download: {sv['actual_speeds']['download_avg_mbps']} Mbps\n")
    output.write(f"Percent of Minimum Advertised: {sv['comparison']['percent_of_min_advertised']}%\n")
    output.write(f"Tests Below 10 Mbps: {sv['comparison']['tests_below_10mbps']} ({sv['comparison']['tests_below_10mbps_pct']}%)\n\n")

    # Signal Quality
    output.write("=== SIGNAL QUALITY ===\n")
    sig = report_data['signal_analysis']
    output.write(f"5G SINR Avg: {sig['metrics']['5g_sinr_avg_db']} dB\n")
    output.write(f"5G RSRP Avg: {sig['metrics']['5g_rsrp_avg_dbm']} dBm\n")
    output.write(f"Signal Assessment: {sig['assessment']['sinr_quality']}\n")
    output.write(f"Conclusion: {sig['assessment']['conclusion']}\n\n")

    # Speed Test Data
    output.write("=== SPEED TEST HISTORY ===\n")
    writer = csv.writer(output)
    writer.writerow(['Timestamp', 'Download (Mbps)', 'Upload (Mbps)', 'Ping (ms)',
                    '5G SINR (dB)', '5G RSRP (dBm)', 'Server', 'Hour', 'Day of Week'])

    for test in report_data.get('raw_speedtest_data', []):
        writer.writerow([
            test.get('timestamp', ''),
            test.get('download_mbps', ''),
            test.get('upload_mbps', ''),
            test.get('ping_ms', ''),
            test.get('nr_sinr', ''),
            test.get('nr_rsrp', ''),
            test.get('server_name', ''),
            test.get('hour_of_day', ''),
            test.get('day_of_week', '')
        ])

    output.write("\n")

    # Support Interactions
    if report_data['support_interactions']['interactions']:
        output.write("=== SUPPORT INTERACTION LOG ===\n")
        writer.writerow(['Date', 'Method', 'Agent', 'Complaint', 'Response', 'Status'])
        for interaction in report_data['support_interactions']['interactions']:
            writer.writerow([
                interaction.get('contact_date', ''),
                interaction.get('contact_method', ''),
                interaction.get('agent_name', '') or interaction.get('agent_id', ''),
                interaction.get('complaint_summary', ''),
                interaction.get('response_received', ''),
                interaction.get('resolution_status', '')
            ])

    output.write("\n")

    # Narrative
    output.write("=== FCC COMPLAINT NARRATIVE ===\n")
    output.write(report_data.get('fcc_narrative', ''))

    return output.getvalue()


def export_to_pdf(report_data):
    """Export FCC complaint report to PDF format."""
    if not REPORTLAB_AVAILABLE:
        return None

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        rightMargin=72, leftMargin=72,
        topMargin=72, bottomMargin=72
    )

    styles = getSampleStyleSheet()
    story = []

    # Custom styles
    title_style = ParagraphStyle(
        'FCCTitle',
        parent=styles['Heading1'],
        fontSize=20,
        spaceAfter=20,
        textColor=colors.HexColor('#1a1a1a')
    )

    section_style = ParagraphStyle(
        'FCCSection',
        parent=styles['Heading2'],
        fontSize=14,
        spaceBefore=20,
        spaceAfter=10,
        textColor=colors.HexColor('#e20074')  # T-Mobile magenta
    )

    body_style = ParagraphStyle(
        'FCCBody',
        parent=styles['Normal'],
        fontSize=10,
        spaceAfter=8,
        leading=14
    )

    # Title
    story.append(Paragraph("FCC Consumer Complaint - Evidence Report", title_style))
    story.append(Paragraph("T-Mobile Home Internet Service - Speed Performance Documentation", styles['Heading3']))
    story.append(Spacer(1, 12))

    # Report Info
    info_data = [
        ['Report Generated:', report_data['report_generated'][:19]],
        ['Collection Period:', f"{report_data['collection_period']['total_days']} days"],
        ['Total Speed Tests:', str(report_data['collection_period']['total_tests'])],
    ]
    info_table = Table(info_data, colWidths=[120, 350])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 20))

    # Section 1: Service Agreement Violation
    story.append(Paragraph("1. Service Agreement Violation", section_style))

    sv = report_data['service_violation']
    violation_data = [
        ['Metric', 'Advertised', 'Actual', 'Shortfall'],
        ['Download Speed',
         f"{sv['advertised_speeds']['download_min']}-{sv['advertised_speeds']['download_max']} Mbps",
         f"{sv['actual_speeds']['download_avg_mbps']} Mbps",
         f"{sv['comparison']['shortfall_mbps']} Mbps"],
        ['Upload Speed',
         f"{sv['advertised_speeds']['upload_min']}-{sv['advertised_speeds']['upload_max']} Mbps",
         f"{sv['actual_speeds']['upload_avg_mbps'] or 'N/A'} Mbps",
         '-'],
        ['Latency',
         f"{sv['advertised_speeds']['latency_min']}-{sv['advertised_speeds']['latency_max']} ms",
         f"{sv['actual_speeds']['ping_avg_ms'] or 'N/A'} ms",
         '-'],
    ]

    violation_table = Table(violation_data, colWidths=[100, 150, 100, 100])
    violation_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#333333')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8f8f8')),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#cccccc')),
    ]))
    story.append(violation_table)
    story.append(Spacer(1, 10))

    story.append(Paragraph(
        f"<b>Key Finding:</b> Actual average speed ({sv['actual_speeds']['download_avg_mbps']} Mbps) "
        f"is only <b>{sv['comparison']['percent_of_min_advertised']}%</b> of minimum advertised speed. "
        f"{sv['comparison']['tests_below_10mbps_pct']}% of tests recorded speeds below 10 Mbps.",
        body_style
    ))

    # Section 2: Signal Quality Analysis
    story.append(Paragraph("2. Signal Quality Analysis", section_style))

    sig = report_data['signal_analysis']
    signal_data = [
        ['Metric', 'Value', 'Quality Assessment'],
        ['5G SINR', f"{sig['metrics']['5g_sinr_avg_db']} dB", sig['assessment']['sinr_quality'].title()],
        ['5G RSRP', f"{sig['metrics']['5g_rsrp_avg_dbm']} dBm", sig['assessment']['rsrp_quality'].title()],
    ]

    signal_table = Table(signal_data, colWidths=[100, 150, 150])
    signal_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#333333')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8f8f8')),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#cccccc')),
    ]))
    story.append(signal_table)
    story.append(Spacer(1, 10))

    story.append(Paragraph(
        f"<b>Conclusion:</b> {sig['assessment']['conclusion']}",
        body_style
    ))

    # Section 3: Time-of-Day Pattern
    story.append(Paragraph("3. Time-of-Day Performance Pattern", section_style))

    dp = report_data['duration_pattern']
    if dp['best_hour'] and dp['worst_hour']:
        story.append(Paragraph(
            f"Best Performance: {dp['best_hour']['avg_download']} Mbps at {dp['best_hour']['hour_label']}<br/>"
            f"Worst Performance: {dp['worst_hour']['avg_download']} Mbps at {dp['worst_hour']['hour_label']}",
            body_style
        ))
        story.append(Paragraph(dp['pattern_conclusion'], body_style))

    # Section 4: Support Interactions
    if report_data['support_interactions']['total_contacts'] > 0:
        story.append(Paragraph("4. T-Mobile Support Interactions", section_style))

        sup = report_data['support_interactions']
        story.append(Paragraph(
            f"Total contacts: {sup['total_contacts']}<br/>"
            f"Unresolved issues: {sup['unresolved_count']}",
            body_style
        ))

        if sup['interactions']:
            for interaction in sup['interactions'][:5]:  # Limit to 5 for space
                story.append(Paragraph(
                    f"<b>{interaction.get('contact_date', 'Unknown date')} - {interaction.get('contact_method', 'Unknown method')}</b><br/>"
                    f"Complaint: {interaction.get('complaint_summary', 'N/A')}<br/>"
                    f"Response: {interaction.get('response_received', 'N/A')}<br/>"
                    f"Status: {interaction.get('resolution_status', 'Unknown')}",
                    body_style
                ))
                story.append(Spacer(1, 5))

    # Page break before narrative
    story.append(PageBreak())

    # Section 5: FCC Complaint Narrative
    story.append(Paragraph("FCC Complaint Narrative", section_style))
    story.append(Spacer(1, 10))

    # Split narrative into paragraphs
    narrative = report_data.get('fcc_narrative', '')
    for para in narrative.split('\n\n'):
        if para.strip():
            # Bold section headers
            if para.strip().endswith(':'):
                story.append(Paragraph(f"<b>{para.strip()}</b>", body_style))
            else:
                story.append(Paragraph(para.replace('\n', '<br/>'), body_style))
            story.append(Spacer(1, 6))

    # Footer
    story.append(Spacer(1, 30))
    story.append(Paragraph(
        "This report was generated by the T-Mobile Home Internet Dashboard "
        "to document service performance for FCC complaint submission.",
        ParagraphStyle('Footer', parent=styles['Normal'], fontSize=8, textColor=colors.gray)
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def get_fcc_report_json(days=30):
    """Generate FCC complaint report and return as JSON string."""
    report = generate_fcc_complaint_data(days)
    return export_to_json(report)


def get_fcc_report_csv(days=30):
    """Generate FCC complaint report and return as CSV string."""
    report = generate_fcc_complaint_data(days)
    return export_to_csv(report)


def get_fcc_report_pdf(days=30):
    """Generate FCC complaint report and return as PDF bytes."""
    report = generate_fcc_complaint_data(days)
    return export_to_pdf(report)


# API helper for quick summary
def get_fcc_readiness_check():
    """Check if enough data has been collected for a strong FCC complaint."""
    scheduled_tests = get_scheduled_speedtests(30)
    support_interactions = get_support_interactions()
    signal_summary = get_signal_summary(30)

    issues = []
    recommendations = []

    # Check test count
    if len(scheduled_tests) < 50:
        issues.append(f"Only {len(scheduled_tests)} speed tests recorded (recommend 100+)")
        recommendations.append("Enable scheduled speed tests and collect data for at least 7 more days")

    # Check collection period
    if scheduled_tests:
        timestamps = [t.get('timestamp_unix', 0) for t in scheduled_tests]
        days = (max(timestamps) - min(timestamps)) / 86400 if timestamps else 0
        if days < 7:
            issues.append(f"Collection period only {days:.1f} days (recommend 30+)")
            recommendations.append("Continue collecting data to establish pattern over time")

    # Check support interactions
    if len(support_interactions) == 0:
        issues.append("No support interactions documented")
        recommendations.append("Log any previous T-Mobile support contacts and document future ones")

    # Check signal data
    if signal_summary.get('sample_count', 0) < 1000:
        issues.append("Limited signal quality data")
        recommendations.append("Leave dashboard running to collect more signal metrics")

    # Calculate readiness score
    readiness_score = 100
    readiness_score -= len(issues) * 20

    return {
        'ready': len(issues) == 0,
        'readiness_score': max(0, readiness_score),
        'issues': issues,
        'recommendations': recommendations,
        'data_summary': {
            'speed_tests': len(scheduled_tests),
            'support_interactions': len(support_interactions),
            'signal_samples': signal_summary.get('sample_count', 0)
        }
    }


if __name__ == '__main__':
    # Test report generation
    print("Generating FCC complaint report...")

    # Check readiness
    readiness = get_fcc_readiness_check()
    print(f"\nFCC Complaint Readiness: {readiness['readiness_score']}%")
    if readiness['issues']:
        print("Issues:")
        for issue in readiness['issues']:
            print(f"  - {issue}")
    if readiness['recommendations']:
        print("Recommendations:")
        for rec in readiness['recommendations']:
            print(f"  - {rec}")

    # Generate report
    report = generate_fcc_complaint_data(30)

    if 'error' in report:
        print(f"\nError: {report['error']}")
    else:
        print(f"\nReport Generated: {report['report_generated']}")
        print(f"Collection Period: {report['collection_period']['total_days']} days")
        print(f"Total Tests: {report['collection_period']['total_tests']}")

        sv = report['service_violation']
        print(f"\nActual Avg Speed: {sv['actual_speeds']['download_avg_mbps']} Mbps")
        print(f"Percent of Advertised: {sv['comparison']['percent_of_min_advertised']}%")

        print("\n--- FCC Narrative ---")
        print(report['fcc_narrative'][:500] + "...")

        # Test exports
        print("\nTesting CSV export...")
        csv_data = export_to_csv(report)
        print(f"CSV length: {len(csv_data)} chars")

        print("Testing JSON export...")
        json_data = export_to_json(report)
        print(f"JSON length: {len(json_data)} chars")

        if REPORTLAB_AVAILABLE:
            print("Testing PDF export...")
            pdf_data = export_to_pdf(report)
            if pdf_data:
                print(f"PDF size: {len(pdf_data)} bytes")
        else:
            print("PDF export not available (install reportlab)")
