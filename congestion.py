"""
Congestion Analysis Module for T-Mobile Dashboard

This module provides time-of-day congestion analysis functionality:
- Hourly aggregation of signal metrics
- Congestion scoring based on SINR values
- Heatmap data generation for visualization
- Peak period identification
- Weekend vs weekday pattern analysis
"""

import sqlite3
import os
from datetime import datetime, timedelta

# Use the same DB path as the main server
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'signal_history.db')


def calculate_congestion_score(sinr_5g, sinr_4g):
    """Calculate congestion score from SINR values (0=congested, 100=clear).

    Lower SINR indicates more interference/congestion on the network.
    Score is based on typical SINR thresholds:
    - Excellent: SINR > 20 dB
    - Good: SINR 10-20 dB
    - Fair: SINR 0-10 dB
    - Poor: SINR < 0 dB
    """
    # Use 5G SINR as primary, fall back to 4G
    sinr = sinr_5g if sinr_5g is not None else sinr_4g
    if sinr is None:
        return None

    # Map SINR to 0-100 congestion score (inverted - higher SINR = less congested)
    # SINR range typically -10 to 30 dB
    if sinr >= 25:
        return 100  # No congestion
    elif sinr <= -5:
        return 0  # Severe congestion
    else:
        # Linear interpolation between -5 and 25 dB
        return round(((sinr + 5) / 30) * 100, 1)


def aggregate_hourly_metrics():
    """Aggregate raw signal data into hourly metrics for congestion analysis.

    This runs periodically to roll up detailed signal_history data into
    hourly_metrics for efficient time-of-day analysis.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # Calculate the current hour boundary
        now = datetime.now()
        current_hour_start = now.replace(minute=0, second=0, microsecond=0)

        # Only aggregate complete hours (not the current hour)
        cutoff = current_hour_start.timestamp()

        # Look back 24 hours max to find data to aggregate
        start_time = cutoff - (24 * 3600)

        # Query raw data grouped by hour
        cursor.execute('''
            SELECT
                strftime('%Y-%m-%d', datetime(timestamp_unix, 'unixepoch', 'localtime')) as date,
                CAST(strftime('%H', datetime(timestamp_unix, 'unixepoch', 'localtime')) AS INTEGER) as hour,
                CAST(strftime('%w', datetime(timestamp_unix, 'unixepoch', 'localtime')) AS INTEGER) as day_of_week,
                AVG(nr_sinr) as nr_sinr_avg,
                MIN(nr_sinr) as nr_sinr_min,
                MAX(nr_sinr) as nr_sinr_max,
                AVG(nr_rsrp) as nr_rsrp_avg,
                MIN(nr_rsrp) as nr_rsrp_min,
                MAX(nr_rsrp) as nr_rsrp_max,
                AVG(nr_rsrq) as nr_rsrq_avg,
                AVG(lte_sinr) as lte_sinr_avg,
                MIN(lte_sinr) as lte_sinr_min,
                MAX(lte_sinr) as lte_sinr_max,
                AVG(lte_rsrp) as lte_rsrp_avg,
                MIN(lte_rsrp) as lte_rsrp_min,
                MAX(lte_rsrp) as lte_rsrp_max,
                AVG(lte_rsrq) as lte_rsrq_avg,
                COUNT(*) as sample_count
            FROM signal_history
            WHERE timestamp_unix >= ? AND timestamp_unix < ?
            GROUP BY date, hour
            HAVING sample_count > 10
        ''', (start_time, cutoff))

        rows = cursor.fetchall()
        inserted = 0

        for row in rows:
            date, hour, day_of_week = row[0], row[1], row[2]
            nr_sinr_avg = row[3]
            lte_sinr_avg = row[10]

            # Calculate congestion score
            congestion_score = calculate_congestion_score(nr_sinr_avg, lte_sinr_avg)

            # Determine if weekend (0=Sunday, 6=Saturday)
            is_weekend = 1 if day_of_week in (0, 6) else 0

            # Upsert into hourly_metrics
            cursor.execute('''
                INSERT OR REPLACE INTO hourly_metrics (
                    date, hour, day_of_week, is_weekend,
                    nr_sinr_avg, nr_sinr_min, nr_sinr_max,
                    nr_rsrp_avg, nr_rsrp_min, nr_rsrp_max, nr_rsrq_avg,
                    lte_sinr_avg, lte_sinr_min, lte_sinr_max,
                    lte_rsrp_avg, lte_rsrp_min, lte_rsrp_max, lte_rsrq_avg,
                    congestion_score, sample_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                date, hour, day_of_week, is_weekend,
                row[3], row[4], row[5],  # nr_sinr
                row[6], row[7], row[8], row[9],  # nr_rsrp, nr_rsrq
                row[10], row[11], row[12],  # lte_sinr
                row[13], row[14], row[15], row[16],  # lte_rsrp, lte_rsrq
                congestion_score, row[17]  # sample_count
            ))
            inserted += 1

        conn.commit()
        if inserted > 0:
            print(f'[CONGESTION] Aggregated {inserted} hourly records')
        return inserted
    except Exception as e:
        print(f'[CONGESTION] Aggregation error: {e}')
        return 0
    finally:
        conn.close()


def get_congestion_heatmap(days=7):
    """Get congestion data formatted for a heatmap visualization.

    Returns average congestion score for each hour of the day,
    optionally split by weekday/weekend.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        # Get hourly averages across all days
        cursor.execute('''
            SELECT
                hour,
                AVG(congestion_score) as avg_score,
                AVG(CASE WHEN is_weekend = 0 THEN congestion_score END) as weekday_score,
                AVG(CASE WHEN is_weekend = 1 THEN congestion_score END) as weekend_score,
                AVG(nr_sinr_avg) as avg_sinr_5g,
                AVG(lte_sinr_avg) as avg_sinr_4g,
                COUNT(*) as data_points
            FROM hourly_metrics
            WHERE date >= ?
            GROUP BY hour
            ORDER BY hour
        ''', (cutoff,))

        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f'[CONGESTION] Heatmap query error: {e}')
        return []
    finally:
        conn.close()


def get_congestion_by_day(days=30):
    """Get daily congestion patterns for trend analysis."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        cursor.execute('''
            SELECT
                date,
                day_of_week,
                is_weekend,
                AVG(congestion_score) as avg_score,
                MIN(congestion_score) as min_score,
                MAX(congestion_score) as max_score,
                AVG(nr_sinr_avg) as avg_sinr_5g,
                AVG(lte_sinr_avg) as avg_sinr_4g,
                SUM(sample_count) as total_samples
            FROM hourly_metrics
            WHERE date >= ?
            GROUP BY date
            ORDER BY date
        ''', (cutoff,))

        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f'[CONGESTION] Daily query error: {e}')
        return []
    finally:
        conn.close()


def get_peak_congestion_periods(days=7, top_n=5):
    """Identify the most congested time periods."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        # Get worst (lowest score = most congested) hours
        cursor.execute('''
            SELECT
                date,
                hour,
                day_of_week,
                congestion_score,
                nr_sinr_avg,
                lte_sinr_avg,
                sample_count
            FROM hourly_metrics
            WHERE date >= ? AND congestion_score IS NOT NULL
            ORDER BY congestion_score ASC
            LIMIT ?
        ''', (cutoff, top_n))

        worst = [dict(row) for row in cursor.fetchall()]

        # Get best (highest score = least congested) hours
        cursor.execute('''
            SELECT
                date,
                hour,
                day_of_week,
                congestion_score,
                nr_sinr_avg,
                lte_sinr_avg,
                sample_count
            FROM hourly_metrics
            WHERE date >= ? AND congestion_score IS NOT NULL
            ORDER BY congestion_score DESC
            LIMIT ?
        ''', (cutoff, top_n))

        best = [dict(row) for row in cursor.fetchall()]

        return {'most_congested': worst, 'least_congested': best}
    except Exception as e:
        print(f'[CONGESTION] Peak periods query error: {e}')
        return {'most_congested': [], 'least_congested': []}
    finally:
        conn.close()


def get_weekday_vs_weekend_stats(days=30):
    """Compare weekday vs weekend congestion patterns."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        cursor.execute('''
            SELECT
                is_weekend,
                hour,
                AVG(congestion_score) as avg_score,
                AVG(nr_sinr_avg) as avg_sinr_5g,
                AVG(lte_sinr_avg) as avg_sinr_4g,
                COUNT(*) as data_points
            FROM hourly_metrics
            WHERE date >= ?
            GROUP BY is_weekend, hour
            ORDER BY is_weekend, hour
        ''', (cutoff,))

        rows = cursor.fetchall()

        # Organize by weekday/weekend
        weekday_hours = {}
        weekend_hours = {}

        for row in rows:
            data = {
                'hour': row['hour'],
                'avg_score': row['avg_score'],
                'avg_sinr_5g': row['avg_sinr_5g'],
                'avg_sinr_4g': row['avg_sinr_4g'],
                'data_points': row['data_points']
            }
            if row['is_weekend']:
                weekend_hours[row['hour']] = data
            else:
                weekday_hours[row['hour']] = data

        return {
            'weekday': weekday_hours,
            'weekend': weekend_hours
        }
    except Exception as e:
        print(f'[CONGESTION] Weekday/weekend query error: {e}')
        return {'weekday': {}, 'weekend': {}}
    finally:
        conn.close()


def get_congestion_summary(days=7):
    """Get a comprehensive congestion summary for the dashboard."""
    heatmap = get_congestion_heatmap(days)
    peaks = get_peak_congestion_periods(days)
    weekday_weekend = get_weekday_vs_weekend_stats(days)

    # Find best and worst hours from heatmap
    best_hour = None
    worst_hour = None
    if heatmap:
        sorted_by_score = sorted(heatmap, key=lambda x: x['avg_score'] or 0)
        worst_hour = sorted_by_score[0] if sorted_by_score else None
        best_hour = sorted_by_score[-1] if sorted_by_score else None

    # Calculate overall stats
    overall_avg = None
    weekday_avg = None
    weekend_avg = None
    if heatmap:
        scores = [h['avg_score'] for h in heatmap if h['avg_score'] is not None]
        weekday_scores = [h['weekday_score'] for h in heatmap if h['weekday_score'] is not None]
        weekend_scores = [h['weekend_score'] for h in heatmap if h['weekend_score'] is not None]

        overall_avg = round(sum(scores) / len(scores), 1) if scores else None
        weekday_avg = round(sum(weekday_scores) / len(weekday_scores), 1) if weekday_scores else None
        weekend_avg = round(sum(weekend_scores) / len(weekend_scores), 1) if weekend_scores else None

    return {
        'period_days': days,
        'heatmap': heatmap,
        'peak_periods': peaks,
        'weekday_weekend': weekday_weekend,
        'summary': {
            'overall_avg_score': overall_avg,
            'weekday_avg_score': weekday_avg,
            'weekend_avg_score': weekend_avg,
            'best_hour': best_hour,
            'worst_hour': worst_hour
        }
    }
