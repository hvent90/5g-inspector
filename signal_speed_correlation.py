"""
Signal Quality vs Speed Correlation Analysis Module

This module provides comprehensive analysis to prove network congestion,
not signal quality issues. Key analyses include:

1. Signal Quality Assessment - Show signal is acceptable
2. Speed vs Signal Correlation - Show good signal but poor speeds
3. Time Pattern Analysis - Show 2am vs daytime with same signal
4. Congestion Conclusion - Generate evidence for FCC complaint
"""

import sqlite3
import os
import json
import math
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Tuple

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'signal_history.db')
SPEEDTEST_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'speedtest_history.json')


# Signal quality thresholds for 5G
SIGNAL_THRESHOLDS = {
    'sinr': {
        'excellent': 20,    # >= 20 dB
        'good': 10,         # >= 10 dB
        'fair': 0,          # >= 0 dB
        'poor': -5,         # >= -5 dB
        'critical': -10     # < -5 dB
    },
    'rsrp': {
        'excellent': -80,   # >= -80 dBm
        'good': -90,        # >= -90 dBm
        'fair': -100,       # >= -100 dBm
        'poor': -110,       # >= -110 dBm
        'critical': -120    # < -110 dBm
    }
}

# Speed thresholds
SPEED_THRESHOLDS = {
    'minimum_advertised': 133,  # T-Mobile advertises 133-415 Mbps
    'usable': 25,               # Minimum for HD streaming
    'poor': 10,                 # Below this is unusable
    'critical': 5               # Below this is severely degraded
}


def load_speedtest_history() -> List[Dict[str, Any]]:
    """Load speedtest history from file."""
    try:
        if os.path.exists(SPEEDTEST_HISTORY_FILE):
            with open(SPEEDTEST_HISTORY_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f'[CORRELATION] Error loading speedtest history: {e}')
    return []


def get_signal_at_time(timestamp_unix: float, tolerance_minutes: int = 5) -> Optional[Dict]:
    """Get signal metrics closest to a given timestamp."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        tolerance_seconds = tolerance_minutes * 60
        cursor.execute('''
            SELECT * FROM signal_history
            WHERE timestamp_unix BETWEEN ? AND ?
            ORDER BY ABS(timestamp_unix - ?)
            LIMIT 1
        ''', (timestamp_unix - tolerance_seconds, timestamp_unix + tolerance_seconds, timestamp_unix))

        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def classify_signal_quality(sinr: Optional[float], rsrp: Optional[float]) -> Dict[str, Any]:
    """Classify signal quality based on SINR and RSRP values."""
    result = {
        'sinr_quality': 'unknown',
        'rsrp_quality': 'unknown',
        'overall_quality': 'unknown',
        'is_acceptable': False,
        'description': ''
    }

    if sinr is not None:
        if sinr >= SIGNAL_THRESHOLDS['sinr']['excellent']:
            result['sinr_quality'] = 'excellent'
        elif sinr >= SIGNAL_THRESHOLDS['sinr']['good']:
            result['sinr_quality'] = 'good'
        elif sinr >= SIGNAL_THRESHOLDS['sinr']['fair']:
            result['sinr_quality'] = 'fair'
        elif sinr >= SIGNAL_THRESHOLDS['sinr']['poor']:
            result['sinr_quality'] = 'poor'
        else:
            result['sinr_quality'] = 'critical'

    if rsrp is not None:
        if rsrp >= SIGNAL_THRESHOLDS['rsrp']['excellent']:
            result['rsrp_quality'] = 'excellent'
        elif rsrp >= SIGNAL_THRESHOLDS['rsrp']['good']:
            result['rsrp_quality'] = 'good'
        elif rsrp >= SIGNAL_THRESHOLDS['rsrp']['fair']:
            result['rsrp_quality'] = 'fair'
        elif rsrp >= SIGNAL_THRESHOLDS['rsrp']['poor']:
            result['rsrp_quality'] = 'poor'
        else:
            result['rsrp_quality'] = 'critical'

    # Determine overall quality (use worst of the two)
    quality_order = ['excellent', 'good', 'fair', 'poor', 'critical', 'unknown']
    sinr_idx = quality_order.index(result['sinr_quality'])
    rsrp_idx = quality_order.index(result['rsrp_quality'])
    result['overall_quality'] = quality_order[max(sinr_idx, rsrp_idx)]

    # Signal is acceptable if fair or better
    result['is_acceptable'] = result['overall_quality'] in ['excellent', 'good', 'fair']

    # Generate description
    if result['overall_quality'] in ['excellent', 'good']:
        result['description'] = f"Signal quality is {result['overall_quality']} (SINR: {sinr} dB, RSRP: {rsrp} dBm)"
    elif result['overall_quality'] == 'fair':
        result['description'] = f"Signal quality is acceptable/fair (SINR: {sinr} dB, RSRP: {rsrp} dBm)"
    else:
        result['description'] = f"Signal quality is {result['overall_quality']} (SINR: {sinr} dB, RSRP: {rsrp} dBm)"

    return result


def analyze_signal_quality_summary(days: int = 7) -> Dict[str, Any]:
    """
    Analyze overall signal quality to prove it's NOT the issue.

    Returns statistics showing signal quality is acceptable.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cutoff = (datetime.now() - timedelta(days=days)).timestamp()

        cursor.execute('''
            SELECT
                AVG(nr_sinr) as avg_sinr_5g,
                MIN(nr_sinr) as min_sinr_5g,
                MAX(nr_sinr) as max_sinr_5g,
                AVG(nr_rsrp) as avg_rsrp_5g,
                MIN(nr_rsrp) as min_rsrp_5g,
                MAX(nr_rsrp) as max_rsrp_5g,
                AVG(lte_sinr) as avg_sinr_4g,
                AVG(lte_rsrp) as avg_rsrp_4g,
                COUNT(*) as sample_count
            FROM signal_history
            WHERE timestamp_unix >= ?
        ''', (cutoff,))

        row = cursor.fetchone()

        if not row or row['sample_count'] == 0:
            return {'error': 'No signal data available', 'sample_count': 0}

        result = dict(row)

        # Classify the average signal quality
        quality = classify_signal_quality(
            result['avg_sinr_5g'],
            result['avg_rsrp_5g']
        )

        # Count samples by quality level
        cursor.execute('''
            SELECT
                CASE
                    WHEN nr_sinr >= 20 THEN 'excellent'
                    WHEN nr_sinr >= 10 THEN 'good'
                    WHEN nr_sinr >= 0 THEN 'fair'
                    WHEN nr_sinr >= -5 THEN 'poor'
                    ELSE 'critical'
                END as quality,
                COUNT(*) as count
            FROM signal_history
            WHERE timestamp_unix >= ? AND nr_sinr IS NOT NULL
            GROUP BY quality
        ''', (cutoff,))

        quality_distribution = {row['quality']: row['count'] for row in cursor.fetchall()}

        # Calculate percentage in acceptable range
        total = sum(quality_distribution.values())
        acceptable_count = sum(quality_distribution.get(q, 0)
                              for q in ['excellent', 'good', 'fair'])
        acceptable_percentage = round((acceptable_count / total) * 100, 1) if total > 0 else 0

        return {
            'period_days': days,
            'sample_count': result['sample_count'],
            'metrics_5g': {
                'sinr': {
                    'avg': round(result['avg_sinr_5g'], 1) if result['avg_sinr_5g'] else None,
                    'min': round(result['min_sinr_5g'], 1) if result['min_sinr_5g'] else None,
                    'max': round(result['max_sinr_5g'], 1) if result['max_sinr_5g'] else None,
                    'quality': quality['sinr_quality']
                },
                'rsrp': {
                    'avg': round(result['avg_rsrp_5g'], 1) if result['avg_rsrp_5g'] else None,
                    'min': round(result['min_rsrp_5g'], 1) if result['min_rsrp_5g'] else None,
                    'max': round(result['max_rsrp_5g'], 1) if result['max_rsrp_5g'] else None,
                    'quality': quality['rsrp_quality']
                }
            },
            'quality_assessment': quality,
            'quality_distribution': quality_distribution,
            'acceptable_percentage': acceptable_percentage,
            'conclusion': f"Signal quality is acceptable {acceptable_percentage}% of the time, indicating signal is NOT the issue."
        }
    finally:
        conn.close()


def analyze_speed_vs_signal_correlation() -> Dict[str, Any]:
    """
    Analyze correlation between signal quality and speed.

    Key insight: If signal is good but speeds are poor, it proves congestion.
    """
    speedtests = load_speedtest_history()

    if len(speedtests) < 3:
        return {
            'error': 'Insufficient speed test data',
            'tests_available': len(speedtests),
            'minimum_required': 3
        }

    # Build correlation data
    data_points = []
    for test in speedtests:
        signal = test.get('signal_at_test', {})
        s5g = signal.get('5g', {})

        sinr = s5g.get('sinr')
        rsrp = s5g.get('rsrp')
        download = test.get('download_mbps')

        if sinr is not None and download is not None:
            quality = classify_signal_quality(sinr, rsrp)
            hour = datetime.fromisoformat(test['timestamp']).hour

            data_points.append({
                'timestamp': test['timestamp'],
                'hour': hour,
                'sinr': sinr,
                'rsrp': rsrp,
                'download_mbps': download,
                'upload_mbps': test.get('upload_mbps'),
                'signal_quality': quality['overall_quality'],
                'is_acceptable_signal': quality['is_acceptable'],
                'is_poor_speed': download < SPEED_THRESHOLDS['poor']
            })

    if len(data_points) < 3:
        return {
            'error': 'Insufficient tests with signal data',
            'tests_with_signal': len(data_points)
        }

    # Key analysis: Tests with GOOD signal but POOR speed
    good_signal_poor_speed = [p for p in data_points
                              if p['is_acceptable_signal'] and p['is_poor_speed']]

    # Tests with good signal
    good_signal_tests = [p for p in data_points if p['is_acceptable_signal']]

    # Calculate statistics for good-signal tests
    if good_signal_tests:
        avg_download_good_signal = sum(p['download_mbps'] for p in good_signal_tests) / len(good_signal_tests)
        min_download_good_signal = min(p['download_mbps'] for p in good_signal_tests)
        max_download_good_signal = max(p['download_mbps'] for p in good_signal_tests)
    else:
        avg_download_good_signal = None
        min_download_good_signal = None
        max_download_good_signal = None

    # Calculate Pearson correlation
    sinr_values = [p['sinr'] for p in data_points]
    speed_values = [p['download_mbps'] for p in data_points]

    correlation = calculate_pearson_correlation(sinr_values, speed_values)

    # Scatter plot data for visualization
    scatter_data = [{
        'x': p['sinr'],
        'y': p['download_mbps'],
        'hour': p['hour'],
        'timestamp': p['timestamp'],
        'signal_quality': p['signal_quality']
    } for p in data_points]

    # Generate conclusion
    if len(good_signal_poor_speed) > 0:
        poor_speed_percentage = (len(good_signal_poor_speed) / len(good_signal_tests)) * 100 if good_signal_tests else 0
        conclusion = (
            f"CONGESTION DETECTED: {len(good_signal_poor_speed)} of {len(good_signal_tests)} tests "
            f"({poor_speed_percentage:.0f}%) had acceptable signal quality but speeds below {SPEED_THRESHOLDS['poor']} Mbps. "
            f"This proves the issue is network congestion, NOT signal quality."
        )
    else:
        conclusion = "No clear congestion pattern detected - speed issues may correlate with signal quality."

    return {
        'total_tests': len(data_points),
        'tests_with_acceptable_signal': len(good_signal_tests),
        'tests_with_poor_speed_despite_good_signal': len(good_signal_poor_speed),
        'statistics': {
            'avg_download_with_good_signal': round(avg_download_good_signal, 2) if avg_download_good_signal else None,
            'min_download_with_good_signal': round(min_download_good_signal, 2) if min_download_good_signal else None,
            'max_download_with_good_signal': round(max_download_good_signal, 2) if max_download_good_signal else None,
        },
        'correlation': correlation,
        'scatter_data': scatter_data,
        'poor_speed_threshold_mbps': SPEED_THRESHOLDS['poor'],
        'conclusion': conclusion
    }


def analyze_time_patterns() -> Dict[str, Any]:
    """
    Analyze speed patterns by time of day.

    Key insight: Compare 2am speeds vs peak hours with same signal quality.
    This proves tower congestion during peak hours.
    """
    speedtests = load_speedtest_history()

    if len(speedtests) < 3:
        return {
            'error': 'Insufficient speed test data',
            'tests_available': len(speedtests)
        }

    # Group tests by hour
    hourly_data = {h: {'speeds': [], 'sinr_values': []} for h in range(24)}

    for test in speedtests:
        try:
            timestamp = datetime.fromisoformat(test['timestamp'])
            hour = timestamp.hour
            download = test.get('download_mbps')
            signal = test.get('signal_at_test', {}).get('5g', {})
            sinr = signal.get('sinr')

            if download is not None:
                hourly_data[hour]['speeds'].append(download)
            if sinr is not None:
                hourly_data[hour]['sinr_values'].append(sinr)
        except (ValueError, KeyError):
            continue

    # Calculate hourly statistics
    hourly_stats = []
    for hour in range(24):
        data = hourly_data[hour]
        if data['speeds']:
            stats = {
                'hour': hour,
                'hour_label': f"{hour:02d}:00",
                'test_count': len(data['speeds']),
                'avg_speed': round(sum(data['speeds']) / len(data['speeds']), 2),
                'min_speed': round(min(data['speeds']), 2),
                'max_speed': round(max(data['speeds']), 2),
            }
            if data['sinr_values']:
                stats['avg_sinr'] = round(sum(data['sinr_values']) / len(data['sinr_values']), 1)
            else:
                stats['avg_sinr'] = None
            hourly_stats.append(stats)

    if not hourly_stats:
        return {'error': 'No hourly data available'}

    # Define time periods
    off_peak_hours = list(range(0, 7)) + [23]  # 11pm-7am
    peak_hours = list(range(17, 23))           # 5pm-11pm

    off_peak_stats = [s for s in hourly_stats if s['hour'] in off_peak_hours]
    peak_stats = [s for s in hourly_stats if s['hour'] in peak_hours]

    # Calculate period averages
    def calc_period_avg(stats_list):
        if not stats_list:
            return None, None
        speeds = [s['avg_speed'] for s in stats_list]
        sinrs = [s['avg_sinr'] for s in stats_list if s['avg_sinr'] is not None]
        return (
            round(sum(speeds) / len(speeds), 2) if speeds else None,
            round(sum(sinrs) / len(sinrs), 1) if sinrs else None
        )

    off_peak_avg_speed, off_peak_avg_sinr = calc_period_avg(off_peak_stats)
    peak_avg_speed, peak_avg_sinr = calc_period_avg(peak_stats)

    # Calculate speed ratio (off-peak vs peak)
    speed_ratio = None
    if off_peak_avg_speed and peak_avg_speed and peak_avg_speed > 0:
        speed_ratio = round(off_peak_avg_speed / peak_avg_speed, 2)

    # Find best and worst hours
    best_hour = max(hourly_stats, key=lambda x: x['avg_speed']) if hourly_stats else None
    worst_hour = min(hourly_stats, key=lambda x: x['avg_speed']) if hourly_stats else None

    # Generate conclusion
    conclusion_parts = []

    if off_peak_avg_speed and peak_avg_speed:
        if speed_ratio and speed_ratio > 3:
            conclusion_parts.append(
                f"Off-peak speeds ({off_peak_avg_speed} Mbps) are {speed_ratio}x faster than peak hours ({peak_avg_speed} Mbps)."
            )

        if off_peak_avg_sinr and peak_avg_sinr:
            sinr_diff = abs(off_peak_avg_sinr - peak_avg_sinr)
            if sinr_diff < 5:
                conclusion_parts.append(
                    f"Signal quality is similar during both periods (off-peak SINR: {off_peak_avg_sinr} dB, peak SINR: {peak_avg_sinr} dB)."
                )

        if speed_ratio and speed_ratio > 2 and off_peak_avg_sinr and peak_avg_sinr and abs(off_peak_avg_sinr - peak_avg_sinr) < 5:
            conclusion_parts.append(
                "This proves NETWORK CONGESTION: speeds vary dramatically while signal quality remains stable."
            )

    return {
        'hourly_stats': hourly_stats,
        'period_comparison': {
            'off_peak': {
                'hours': '11pm-7am',
                'avg_speed': off_peak_avg_speed,
                'avg_sinr': off_peak_avg_sinr,
                'test_count': sum(s['test_count'] for s in off_peak_stats) if off_peak_stats else 0
            },
            'peak': {
                'hours': '5pm-11pm',
                'avg_speed': peak_avg_speed,
                'avg_sinr': peak_avg_sinr,
                'test_count': sum(s['test_count'] for s in peak_stats) if peak_stats else 0
            },
            'speed_ratio': speed_ratio
        },
        'extremes': {
            'best_hour': best_hour,
            'worst_hour': worst_hour
        },
        'conclusion': ' '.join(conclusion_parts) if conclusion_parts else 'Insufficient data for time pattern analysis.'
    }


def calculate_pearson_correlation(x: List[float], y: List[float]) -> Dict[str, Any]:
    """Calculate Pearson correlation coefficient between two lists."""
    # Filter out None values
    pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]

    if len(pairs) < 3:
        return {'r': None, 'strength': 'insufficient_data', 'n': len(pairs)}

    n = len(pairs)
    x_vals = [p[0] for p in pairs]
    y_vals = [p[1] for p in pairs]

    # Calculate means
    x_mean = sum(x_vals) / n
    y_mean = sum(y_vals) / n

    # Calculate correlation
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    x_std = math.sqrt(sum((x - x_mean) ** 2 for x in x_vals))
    y_std = math.sqrt(sum((y - y_mean) ** 2 for y in y_vals))

    if x_std == 0 or y_std == 0:
        return {'r': 0, 'strength': 'no_variance', 'n': n}

    r = numerator / (x_std * y_std)

    # Classify strength
    if abs(r) >= 0.7:
        strength = 'strong'
    elif abs(r) >= 0.4:
        strength = 'moderate'
    elif abs(r) >= 0.2:
        strength = 'weak'
    else:
        strength = 'negligible'

    return {
        'r': round(r, 3),
        'strength': strength,
        'direction': 'positive' if r > 0 else 'negative',
        'n': n,
        'interpretation': (
            f"{'Strong' if strength == 'strong' else strength.capitalize()} "
            f"{'positive' if r > 0 else 'negative'} correlation "
            f"({'higher' if r > 0 else 'lower'} signal = {'faster' if r > 0 else 'slower'} speeds)"
        )
    }


def generate_congestion_proof_report(days: int = 7) -> Dict[str, Any]:
    """
    Generate comprehensive report proving network congestion.

    This is the main entry point for FCC complaint evidence.
    """
    signal_analysis = analyze_signal_quality_summary(days)
    correlation_analysis = analyze_speed_vs_signal_correlation()
    time_analysis = analyze_time_patterns()

    # Build the evidence summary
    evidence = []

    # Evidence 1: Signal quality is acceptable
    if signal_analysis.get('acceptable_percentage', 0) >= 70:
        evidence.append({
            'claim': 'Signal quality is NOT the issue',
            'data': f"Signal is acceptable {signal_analysis['acceptable_percentage']}% of the time",
            'metric': f"Average SINR: {signal_analysis.get('metrics_5g', {}).get('sinr', {}).get('avg')} dB"
        })

    # Evidence 2: Good signal + poor speed
    poor_speed_count = correlation_analysis.get('tests_with_poor_speed_despite_good_signal', 0)
    if poor_speed_count > 0:
        evidence.append({
            'claim': 'Speed is poor despite good signal',
            'data': f"{poor_speed_count} tests showed acceptable signal but speeds below {SPEED_THRESHOLDS['poor']} Mbps",
            'metric': correlation_analysis.get('statistics', {})
        })

    # Evidence 3: Time-based patterns
    speed_ratio = time_analysis.get('period_comparison', {}).get('speed_ratio')
    if speed_ratio and speed_ratio > 2:
        evidence.append({
            'claim': 'Speeds vary by time of day',
            'data': f"Off-peak speeds are {speed_ratio}x faster than peak hours",
            'metric': time_analysis.get('period_comparison', {})
        })

    # Generate overall conclusion
    if len(evidence) >= 2:
        overall_conclusion = (
            "NETWORK CONGESTION CONFIRMED: Multiple lines of evidence demonstrate that "
            "T-Mobile Home Internet speeds are severely degraded despite acceptable signal quality. "
            "This indicates tower congestion or deliberate QoS deprioritization, NOT a coverage issue. "
            "T-Mobile is failing to deliver advertised speeds of 133-415 Mbps."
        )
    elif len(evidence) == 1:
        overall_conclusion = (
            "Evidence suggests possible network congestion. More speed tests during different "
            "hours of the day would strengthen the case for an FCC complaint."
        )
    else:
        overall_conclusion = (
            "Insufficient evidence to conclusively prove network congestion. "
            "Continue collecting speed test data during both peak and off-peak hours."
        )

    return {
        'generated_at': datetime.now().isoformat(),
        'period_days': days,
        'signal_analysis': signal_analysis,
        'speed_vs_signal': correlation_analysis,
        'time_patterns': time_analysis,
        'evidence_summary': evidence,
        'overall_conclusion': overall_conclusion,
        'advertised_speed_range': '133-415 Mbps',
        'thresholds': SPEED_THRESHOLDS
    }


def get_scatter_plot_data() -> Dict[str, Any]:
    """
    Get data formatted for scatter plot visualization.

    Returns data for:
    - Speed vs SINR scatter plot
    - Speed vs RSRP scatter plot
    - Time-colored speed distribution
    """
    speedtests = load_speedtest_history()

    scatter_sinr_speed = []
    scatter_rsrp_speed = []

    for test in speedtests:
        signal = test.get('signal_at_test', {}).get('5g', {})
        sinr = signal.get('sinr')
        rsrp = signal.get('rsrp')
        download = test.get('download_mbps')

        try:
            timestamp = datetime.fromisoformat(test['timestamp'])
            hour = timestamp.hour
        except (ValueError, KeyError):
            hour = None

        if sinr is not None and download is not None:
            scatter_sinr_speed.append({
                'x': sinr,
                'y': download,
                'hour': hour,
                'timestamp': test.get('timestamp')
            })

        if rsrp is not None and download is not None:
            scatter_rsrp_speed.append({
                'x': rsrp,
                'y': download,
                'hour': hour,
                'timestamp': test.get('timestamp')
            })

    return {
        'sinr_vs_speed': {
            'data': scatter_sinr_speed,
            'x_label': '5G SINR (dB)',
            'y_label': 'Download Speed (Mbps)',
            'reference_lines': {
                'y': [
                    {'value': SPEED_THRESHOLDS['minimum_advertised'], 'label': 'Minimum Advertised (133 Mbps)'},
                    {'value': SPEED_THRESHOLDS['poor'], 'label': 'Poor Speed Threshold (10 Mbps)'}
                ],
                'x': [
                    {'value': SIGNAL_THRESHOLDS['sinr']['good'], 'label': 'Good Signal (10 dB)'},
                    {'value': SIGNAL_THRESHOLDS['sinr']['fair'], 'label': 'Fair Signal (0 dB)'}
                ]
            }
        },
        'rsrp_vs_speed': {
            'data': scatter_rsrp_speed,
            'x_label': '5G RSRP (dBm)',
            'y_label': 'Download Speed (Mbps)',
            'reference_lines': {
                'y': [
                    {'value': SPEED_THRESHOLDS['minimum_advertised'], 'label': 'Minimum Advertised (133 Mbps)'},
                    {'value': SPEED_THRESHOLDS['poor'], 'label': 'Poor Speed Threshold (10 Mbps)'}
                ],
                'x': [
                    {'value': SIGNAL_THRESHOLDS['rsrp']['good'], 'label': 'Good Signal (-90 dBm)'},
                    {'value': SIGNAL_THRESHOLDS['rsrp']['fair'], 'label': 'Fair Signal (-100 dBm)'}
                ]
            }
        }
    }
