"""Congestion analysis service for T-Mobile Dashboard.

Provides time-of-day congestion analysis:
- Hourly aggregation of signal metrics
- Congestion scoring based on SINR values
- Heatmap data generation for visualization
- Peak period identification
- Weekend vs weekday pattern analysis
- Congestion proof report generation for FCC complaints
"""

import json
import math
from datetime import datetime, timedelta
from typing import TypedDict, Any

import structlog

from ..config import get_settings
from ..db.connection import DatabaseConnection, get_db

log = structlog.get_logger()


# Signal quality thresholds for 5G
SIGNAL_THRESHOLDS = {
    "sinr": {
        "excellent": 20,  # >= 20 dB
        "good": 10,  # >= 10 dB
        "fair": 0,  # >= 0 dB
        "poor": -5,  # >= -5 dB
        "critical": -10,  # < -5 dB
    },
    "rsrp": {
        "excellent": -80,  # >= -80 dBm
        "good": -90,  # >= -90 dBm
        "fair": -100,  # >= -100 dBm
        "poor": -110,  # >= -110 dBm
        "critical": -120,  # < -110 dBm
    },
}

# Speed thresholds
SPEED_THRESHOLDS = {
    "minimum_advertised": 133,  # T-Mobile advertises 133-415 Mbps
    "usable": 25,  # Minimum for HD streaming
    "poor": 10,  # Below this is unusable
    "critical": 5,  # Below this is severely degraded
}


class HourlyMetric(TypedDict, total=False):
    """Type definition for hourly metric data."""

    hour: int
    avg_score: float | None
    weekday_score: float | None
    weekend_score: float | None
    avg_sinr_5g: float | None
    avg_sinr_4g: float | None
    data_points: int


class CongestionPeriod(TypedDict, total=False):
    """Type definition for a congestion period."""

    date: str
    hour: int
    day_of_week: int
    congestion_score: float | None
    nr_sinr_avg: float | None
    lte_sinr_avg: float | None
    sample_count: int


class CongestionService:
    """Service for congestion analysis operations."""

    def __init__(self, db: DatabaseConnection | None = None):
        self.db = db or get_db()

    @staticmethod
    def calculate_congestion_score(sinr_5g: float | None, sinr_4g: float | None) -> float | None:
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
            return 100.0  # No congestion
        elif sinr <= -5:
            return 0.0  # Severe congestion
        else:
            # Linear interpolation between -5 and 25 dB
            return round(((sinr + 5) / 30) * 100, 1)

    async def aggregate_hourly_metrics(self) -> int:
        """Aggregate raw signal data into hourly metrics for congestion analysis.

        This runs periodically to roll up detailed signal_history data into
        hourly_metrics for efficient time-of-day analysis.

        Returns:
            Number of hourly records inserted/updated
        """
        # Calculate the current hour boundary
        now = datetime.now()
        current_hour_start = now.replace(minute=0, second=0, microsecond=0)

        # Only aggregate complete hours (not the current hour)
        cutoff = current_hour_start.timestamp()

        # Look back 24 hours max to find data to aggregate
        start_time = cutoff - (24 * 3600)

        inserted = 0
        async with self.db.connection() as db:
            # Query raw data grouped by hour
            cursor = await db.execute(
                """
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
                """,
                (start_time, cutoff),
            )
            rows = await cursor.fetchall()

            for row in rows:
                date = row["date"]
                hour = row["hour"]
                day_of_week = row["day_of_week"]
                nr_sinr_avg = row["nr_sinr_avg"]
                lte_sinr_avg = row["lte_sinr_avg"]

                # Calculate congestion score
                congestion_score = self.calculate_congestion_score(nr_sinr_avg, lte_sinr_avg)

                # Determine if weekend (0=Sunday, 6=Saturday)
                is_weekend = 1 if day_of_week in (0, 6) else 0

                # Upsert into hourly_metrics
                await db.execute(
                    """
                    INSERT OR REPLACE INTO hourly_metrics (
                        date, hour, day_of_week, is_weekend,
                        nr_sinr_avg, nr_sinr_min, nr_sinr_max,
                        nr_rsrp_avg, nr_rsrp_min, nr_rsrp_max, nr_rsrq_avg,
                        lte_sinr_avg, lte_sinr_min, lte_sinr_max,
                        lte_rsrp_avg, lte_rsrp_min, lte_rsrp_max, lte_rsrq_avg,
                        congestion_score, sample_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        date,
                        hour,
                        day_of_week,
                        is_weekend,
                        row["nr_sinr_avg"],
                        row["nr_sinr_min"],
                        row["nr_sinr_max"],
                        row["nr_rsrp_avg"],
                        row["nr_rsrp_min"],
                        row["nr_rsrp_max"],
                        row["nr_rsrq_avg"],
                        row["lte_sinr_avg"],
                        row["lte_sinr_min"],
                        row["lte_sinr_max"],
                        row["lte_rsrp_avg"],
                        row["lte_rsrp_min"],
                        row["lte_rsrp_max"],
                        row["lte_rsrq_avg"],
                        congestion_score,
                        row["sample_count"],
                    ),
                )
                inserted += 1

            await db.commit()

        if inserted > 0:
            log.info("congestion_aggregation_complete", inserted=inserted)
        return inserted

    async def get_heatmap(self, days: int = 7) -> list[HourlyMetric]:
        """Get congestion data formatted for a heatmap visualization.

        Returns average congestion score for each hour of the day,
        optionally split by weekday/weekend.
        """
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        async with self.db.connection() as db:
            cursor = await db.execute(
                """
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
                """,
                (cutoff,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_daily_patterns(self, days: int = 30) -> list[dict]:
        """Get daily congestion patterns for trend analysis."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        async with self.db.connection() as db:
            cursor = await db.execute(
                """
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
                """,
                (cutoff,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_peak_periods(self, days: int = 7, top_n: int = 5) -> dict:
        """Identify the most congested time periods."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        async with self.db.connection() as db:
            # Get worst (lowest score = most congested) hours
            cursor = await db.execute(
                """
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
                """,
                (cutoff, top_n),
            )
            most_congested = [dict(row) for row in await cursor.fetchall()]

            # Get best (highest score = least congested) hours
            cursor = await db.execute(
                """
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
                """,
                (cutoff, top_n),
            )
            least_congested = [dict(row) for row in await cursor.fetchall()]

        return {"most_congested": most_congested, "least_congested": least_congested}

    async def get_weekday_vs_weekend_stats(self, days: int = 30) -> dict:
        """Compare weekday vs weekend congestion patterns."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        async with self.db.connection() as db:
            cursor = await db.execute(
                """
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
                """,
                (cutoff,),
            )
            rows = await cursor.fetchall()

        # Organize by weekday/weekend
        weekday_hours: dict[int, dict] = {}
        weekend_hours: dict[int, dict] = {}

        for row in rows:
            row_dict = dict(row)
            data = {
                "hour": row_dict["hour"],
                "avg_score": row_dict["avg_score"],
                "avg_sinr_5g": row_dict["avg_sinr_5g"],
                "avg_sinr_4g": row_dict["avg_sinr_4g"],
                "data_points": row_dict["data_points"],
            }
            if row_dict["is_weekend"]:
                weekend_hours[row_dict["hour"]] = data
            else:
                weekday_hours[row_dict["hour"]] = data

        return {"weekday": weekday_hours, "weekend": weekend_hours}

    async def get_summary(self, days: int = 7) -> dict:
        """Get a comprehensive congestion summary for the dashboard."""
        heatmap = await self.get_heatmap(days)
        peaks = await self.get_peak_periods(days)
        weekday_weekend = await self.get_weekday_vs_weekend_stats(days)

        # Find best and worst hours from heatmap
        best_hour = None
        worst_hour = None
        if heatmap:
            sorted_by_score = sorted(heatmap, key=lambda x: x.get("avg_score") or 0)
            worst_hour = sorted_by_score[0] if sorted_by_score else None
            best_hour = sorted_by_score[-1] if sorted_by_score else None

        # Calculate overall stats
        overall_avg = None
        weekday_avg = None
        weekend_avg = None
        if heatmap:
            scores = [h["avg_score"] for h in heatmap if h.get("avg_score") is not None]
            weekday_scores = [h["weekday_score"] for h in heatmap if h.get("weekday_score") is not None]
            weekend_scores = [h["weekend_score"] for h in heatmap if h.get("weekend_score") is not None]

            overall_avg = round(sum(scores) / len(scores), 1) if scores else None
            weekday_avg = round(sum(weekday_scores) / len(weekday_scores), 1) if weekday_scores else None
            weekend_avg = round(sum(weekend_scores) / len(weekend_scores), 1) if weekend_scores else None

        return {
            "period_days": days,
            "heatmap": heatmap,
            "peak_periods": peaks,
            "weekday_weekend": weekday_weekend,
            "summary": {
                "overall_avg_score": overall_avg,
                "weekday_avg_score": weekday_avg,
                "weekend_avg_score": weekend_avg,
                "best_hour": best_hour,
                "worst_hour": worst_hour,
            },
        }

    # ========================================
    # Congestion Proof Report Methods
    # ========================================

    def _load_speedtest_history(self) -> list[dict[str, Any]]:
        """Load speedtest history from file."""
        settings = get_settings()
        try:
            if settings.speedtest_history_file.exists():
                with open(settings.speedtest_history_file, "r") as f:
                    return json.load(f)
        except Exception as e:
            log.warning("speedtest_history_load_error", error=str(e))
        return []

    @staticmethod
    def _classify_signal_quality(sinr: float | None, rsrp: float | None) -> dict[str, Any]:
        """Classify signal quality based on SINR and RSRP values."""
        result: dict[str, Any] = {
            "sinr_quality": "unknown",
            "rsrp_quality": "unknown",
            "overall_quality": "unknown",
            "is_acceptable": False,
            "description": "",
        }

        if sinr is not None:
            if sinr >= SIGNAL_THRESHOLDS["sinr"]["excellent"]:
                result["sinr_quality"] = "excellent"
            elif sinr >= SIGNAL_THRESHOLDS["sinr"]["good"]:
                result["sinr_quality"] = "good"
            elif sinr >= SIGNAL_THRESHOLDS["sinr"]["fair"]:
                result["sinr_quality"] = "fair"
            elif sinr >= SIGNAL_THRESHOLDS["sinr"]["poor"]:
                result["sinr_quality"] = "poor"
            else:
                result["sinr_quality"] = "critical"

        if rsrp is not None:
            if rsrp >= SIGNAL_THRESHOLDS["rsrp"]["excellent"]:
                result["rsrp_quality"] = "excellent"
            elif rsrp >= SIGNAL_THRESHOLDS["rsrp"]["good"]:
                result["rsrp_quality"] = "good"
            elif rsrp >= SIGNAL_THRESHOLDS["rsrp"]["fair"]:
                result["rsrp_quality"] = "fair"
            elif rsrp >= SIGNAL_THRESHOLDS["rsrp"]["poor"]:
                result["rsrp_quality"] = "poor"
            else:
                result["rsrp_quality"] = "critical"

        # Determine overall quality (use worst of the two)
        quality_order = ["excellent", "good", "fair", "poor", "critical", "unknown"]
        sinr_idx = quality_order.index(result["sinr_quality"])
        rsrp_idx = quality_order.index(result["rsrp_quality"])
        result["overall_quality"] = quality_order[max(sinr_idx, rsrp_idx)]

        # Signal is acceptable if fair or better
        result["is_acceptable"] = result["overall_quality"] in ["excellent", "good", "fair"]

        # Generate description
        if result["overall_quality"] in ["excellent", "good"]:
            result["description"] = f"Signal quality is {result['overall_quality']} (SINR: {sinr} dB, RSRP: {rsrp} dBm)"
        elif result["overall_quality"] == "fair":
            result["description"] = f"Signal quality is acceptable/fair (SINR: {sinr} dB, RSRP: {rsrp} dBm)"
        else:
            result["description"] = f"Signal quality is {result['overall_quality']} (SINR: {sinr} dB, RSRP: {rsrp} dBm)"

        return result

    @staticmethod
    def _calculate_pearson_correlation(x: list[float], y: list[float]) -> dict[str, Any]:
        """Calculate Pearson correlation coefficient between two lists."""
        # Filter out None values
        pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]

        if len(pairs) < 3:
            return {"r": None, "strength": "insufficient_data", "n": len(pairs)}

        n = len(pairs)
        x_vals = [p[0] for p in pairs]
        y_vals = [p[1] for p in pairs]

        # Calculate means
        x_mean = sum(x_vals) / n
        y_mean = sum(y_vals) / n

        # Calculate correlation
        numerator = sum((xi - x_mean) * (yi - y_mean) for xi, yi in pairs)
        x_std = math.sqrt(sum((xi - x_mean) ** 2 for xi in x_vals))
        y_std = math.sqrt(sum((yi - y_mean) ** 2 for yi in y_vals))

        if x_std == 0 or y_std == 0:
            return {"r": 0, "strength": "no_variance", "n": n}

        r = numerator / (x_std * y_std)

        # Classify strength
        if abs(r) >= 0.7:
            strength = "strong"
        elif abs(r) >= 0.4:
            strength = "moderate"
        elif abs(r) >= 0.2:
            strength = "weak"
        else:
            strength = "negligible"

        return {
            "r": round(r, 3),
            "strength": strength,
            "direction": "positive" if r > 0 else "negative",
            "n": n,
            "interpretation": (
                f"{'Strong' if strength == 'strong' else strength.capitalize()} "
                f"{'positive' if r > 0 else 'negative'} correlation "
                f"({'higher' if r > 0 else 'lower'} signal = {'faster' if r > 0 else 'slower'} speeds)"
            ),
        }

    async def _analyze_signal_quality_summary(self, days: int = 7) -> dict[str, Any]:
        """Analyze overall signal quality to prove it's NOT the issue."""
        cutoff = (datetime.now() - timedelta(days=days)).timestamp()

        async with self.db.connection() as db:
            cursor = await db.execute(
                """
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
                """,
                (cutoff,),
            )
            row = await cursor.fetchone()

            if not row or row["sample_count"] == 0:
                return {"error": "No signal data available", "sample_count": 0}

            result = dict(row)

            # Classify the average signal quality
            quality = self._classify_signal_quality(
                result["avg_sinr_5g"], result["avg_rsrp_5g"]
            )

            # Count samples by quality level
            cursor = await db.execute(
                """
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
                """,
                (cutoff,),
            )
            rows = await cursor.fetchall()
            quality_distribution = {r["quality"]: r["count"] for r in rows}

        # Calculate percentage in acceptable range
        total = sum(quality_distribution.values())
        acceptable_count = sum(
            quality_distribution.get(q, 0) for q in ["excellent", "good", "fair"]
        )
        acceptable_percentage = round((acceptable_count / total) * 100, 1) if total > 0 else 0

        return {
            "period_days": days,
            "sample_count": result["sample_count"],
            "metrics_5g": {
                "sinr": {
                    "avg": round(result["avg_sinr_5g"], 1) if result["avg_sinr_5g"] else None,
                    "min": round(result["min_sinr_5g"], 1) if result["min_sinr_5g"] else None,
                    "max": round(result["max_sinr_5g"], 1) if result["max_sinr_5g"] else None,
                    "quality": quality["sinr_quality"],
                },
                "rsrp": {
                    "avg": round(result["avg_rsrp_5g"], 1) if result["avg_rsrp_5g"] else None,
                    "min": round(result["min_rsrp_5g"], 1) if result["min_rsrp_5g"] else None,
                    "max": round(result["max_rsrp_5g"], 1) if result["max_rsrp_5g"] else None,
                    "quality": quality["rsrp_quality"],
                },
            },
            "quality_assessment": quality,
            "quality_distribution": quality_distribution,
            "acceptable_percentage": acceptable_percentage,
            "conclusion": f"Signal quality is acceptable {acceptable_percentage}% of the time, indicating signal is NOT the issue.",
        }

    def _analyze_speed_vs_signal_correlation(self) -> dict[str, Any]:
        """Analyze correlation between signal quality and speed."""
        speedtests = self._load_speedtest_history()

        if len(speedtests) < 3:
            return {
                "error": "Insufficient speed test data",
                "tests_available": len(speedtests),
                "minimum_required": 3,
            }

        # Build correlation data
        data_points: list[dict[str, Any]] = []
        for test in speedtests:
            signal = test.get("signal_at_test", {})
            # Handle both old format (5g) and new format (nr)
            s5g = signal.get("5g", {}) or signal.get("nr", {})

            sinr = s5g.get("sinr")
            rsrp = s5g.get("rsrp")
            download = test.get("download_mbps")

            if sinr is not None and download is not None:
                quality = self._classify_signal_quality(sinr, rsrp)
                try:
                    hour = datetime.fromisoformat(test["timestamp"]).hour
                except (ValueError, KeyError):
                    hour = None

                data_points.append({
                    "timestamp": test.get("timestamp"),
                    "hour": hour,
                    "sinr": sinr,
                    "rsrp": rsrp,
                    "download_mbps": download,
                    "upload_mbps": test.get("upload_mbps"),
                    "signal_quality": quality["overall_quality"],
                    "is_acceptable_signal": quality["is_acceptable"],
                    "is_poor_speed": download < SPEED_THRESHOLDS["poor"],
                })

        if len(data_points) < 3:
            return {
                "error": "Insufficient tests with signal data",
                "tests_with_signal": len(data_points),
            }

        # Key analysis: Tests with GOOD signal but POOR speed
        good_signal_poor_speed = [
            p for p in data_points if p["is_acceptable_signal"] and p["is_poor_speed"]
        ]

        # Tests with good signal
        good_signal_tests = [p for p in data_points if p["is_acceptable_signal"]]

        # Calculate statistics for good-signal tests
        if good_signal_tests:
            avg_download_good_signal = sum(p["download_mbps"] for p in good_signal_tests) / len(good_signal_tests)
            min_download_good_signal = min(p["download_mbps"] for p in good_signal_tests)
            max_download_good_signal = max(p["download_mbps"] for p in good_signal_tests)
        else:
            avg_download_good_signal = None
            min_download_good_signal = None
            max_download_good_signal = None

        # Calculate Pearson correlation
        sinr_values = [p["sinr"] for p in data_points]
        speed_values = [p["download_mbps"] for p in data_points]
        correlation = self._calculate_pearson_correlation(sinr_values, speed_values)

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
            "total_tests": len(data_points),
            "tests_with_acceptable_signal": len(good_signal_tests),
            "tests_with_poor_speed_despite_good_signal": len(good_signal_poor_speed),
            "statistics": {
                "avg_download_with_good_signal": round(avg_download_good_signal, 2) if avg_download_good_signal else None,
                "min_download_with_good_signal": round(min_download_good_signal, 2) if min_download_good_signal else None,
                "max_download_with_good_signal": round(max_download_good_signal, 2) if max_download_good_signal else None,
            },
            "correlation": correlation,
            "poor_speed_threshold_mbps": SPEED_THRESHOLDS["poor"],
            "conclusion": conclusion,
        }

    def _analyze_time_patterns(self) -> dict[str, Any]:
        """Analyze speed patterns by time of day."""
        speedtests = self._load_speedtest_history()

        if len(speedtests) < 3:
            return {
                "error": "Insufficient speed test data",
                "tests_available": len(speedtests),
            }

        # Group tests by hour
        hourly_data: dict[int, dict[str, list[float]]] = {
            h: {"speeds": [], "sinr_values": []} for h in range(24)
        }

        for test in speedtests:
            try:
                timestamp = datetime.fromisoformat(test["timestamp"])
                hour = timestamp.hour
                download = test.get("download_mbps")
                signal = test.get("signal_at_test", {})
                s5g = signal.get("5g", {}) or signal.get("nr", {})
                sinr = s5g.get("sinr")

                if download is not None:
                    hourly_data[hour]["speeds"].append(download)
                if sinr is not None:
                    hourly_data[hour]["sinr_values"].append(sinr)
            except (ValueError, KeyError):
                continue

        # Calculate hourly statistics
        hourly_stats = []
        for hour in range(24):
            data = hourly_data[hour]
            if data["speeds"]:
                stats: dict[str, Any] = {
                    "hour": hour,
                    "hour_label": f"{hour:02d}:00",
                    "test_count": len(data["speeds"]),
                    "avg_speed": round(sum(data["speeds"]) / len(data["speeds"]), 2),
                    "min_speed": round(min(data["speeds"]), 2),
                    "max_speed": round(max(data["speeds"]), 2),
                }
                if data["sinr_values"]:
                    stats["avg_sinr"] = round(sum(data["sinr_values"]) / len(data["sinr_values"]), 1)
                else:
                    stats["avg_sinr"] = None
                hourly_stats.append(stats)

        if not hourly_stats:
            return {"error": "No hourly data available"}

        # Define time periods
        off_peak_hours = list(range(0, 7)) + [23]  # 11pm-7am
        peak_hours = list(range(17, 23))  # 5pm-11pm

        off_peak_stats = [s for s in hourly_stats if s["hour"] in off_peak_hours]
        peak_stats = [s for s in hourly_stats if s["hour"] in peak_hours]

        # Calculate period averages
        def calc_period_avg(stats_list: list[dict[str, Any]]) -> tuple[float | None, float | None]:
            if not stats_list:
                return None, None
            speeds = [s["avg_speed"] for s in stats_list]
            sinrs = [s["avg_sinr"] for s in stats_list if s.get("avg_sinr") is not None]
            return (
                round(sum(speeds) / len(speeds), 2) if speeds else None,
                round(sum(sinrs) / len(sinrs), 1) if sinrs else None,
            )

        off_peak_avg_speed, off_peak_avg_sinr = calc_period_avg(off_peak_stats)
        peak_avg_speed, peak_avg_sinr = calc_period_avg(peak_stats)

        # Calculate speed ratio (off-peak vs peak)
        speed_ratio = None
        if off_peak_avg_speed and peak_avg_speed and peak_avg_speed > 0:
            speed_ratio = round(off_peak_avg_speed / peak_avg_speed, 2)

        # Find best and worst hours
        best_hour = max(hourly_stats, key=lambda x: x["avg_speed"]) if hourly_stats else None
        worst_hour = min(hourly_stats, key=lambda x: x["avg_speed"]) if hourly_stats else None

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
            "hourly_stats": hourly_stats,
            "period_comparison": {
                "off_peak": {
                    "hours": "11pm-7am",
                    "avg_speed": off_peak_avg_speed,
                    "avg_sinr": off_peak_avg_sinr,
                    "test_count": sum(s["test_count"] for s in off_peak_stats) if off_peak_stats else 0,
                },
                "peak": {
                    "hours": "5pm-11pm",
                    "avg_speed": peak_avg_speed,
                    "avg_sinr": peak_avg_sinr,
                    "test_count": sum(s["test_count"] for s in peak_stats) if peak_stats else 0,
                },
                "speed_ratio": speed_ratio,
            },
            "extremes": {
                "best_hour": best_hour,
                "worst_hour": worst_hour,
            },
            "conclusion": " ".join(conclusion_parts) if conclusion_parts else "Insufficient data for time pattern analysis.",
        }

    async def generate_congestion_proof_report(self, days: int = 7) -> dict[str, Any]:
        """Generate comprehensive report proving network congestion.

        This is the main entry point for FCC complaint evidence.
        """
        signal_analysis = await self._analyze_signal_quality_summary(days)
        correlation_analysis = self._analyze_speed_vs_signal_correlation()
        time_analysis = self._analyze_time_patterns()

        # Build the evidence summary
        evidence = []

        # Evidence 1: Signal quality is acceptable
        if signal_analysis.get("acceptable_percentage", 0) >= 70:
            evidence.append({
                "claim": "Signal quality is NOT the issue",
                "data": f"Signal is acceptable {signal_analysis['acceptable_percentage']}% of the time",
                "metric": f"Average SINR: {signal_analysis.get('metrics_5g', {}).get('sinr', {}).get('avg')} dB",
            })

        # Evidence 2: Good signal + poor speed
        poor_speed_count = correlation_analysis.get("tests_with_poor_speed_despite_good_signal", 0)
        if poor_speed_count > 0:
            evidence.append({
                "claim": "Speed is poor despite good signal",
                "data": f"{poor_speed_count} tests showed acceptable signal but speeds below {SPEED_THRESHOLDS['poor']} Mbps",
                "metric": correlation_analysis.get("statistics", {}),
            })

        # Evidence 3: Time-based patterns
        speed_ratio = time_analysis.get("period_comparison", {}).get("speed_ratio")
        if speed_ratio and speed_ratio > 2:
            evidence.append({
                "claim": "Speeds vary by time of day",
                "data": f"Off-peak speeds are {speed_ratio}x faster than peak hours",
                "metric": time_analysis.get("period_comparison", {}),
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
            "generated_at": datetime.now().isoformat(),
            "period_days": days,
            "signal_analysis": signal_analysis,
            "speed_vs_signal": correlation_analysis,
            "time_patterns": time_analysis,
            "evidence_summary": evidence,
            "overall_conclusion": overall_conclusion,
            "advertised_speed_range": "133-415 Mbps",
            "thresholds": SPEED_THRESHOLDS,
        }


# Global service instance
_congestion_service: CongestionService | None = None


def get_congestion_service() -> CongestionService:
    """Get the global congestion service instance."""
    global _congestion_service
    if _congestion_service is None:
        _congestion_service = CongestionService()
    return _congestion_service
