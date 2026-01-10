"""Diagnostic reports service for T-Mobile Dashboard.

Generates comprehensive diagnostic reports including:
- Signal metrics summary with statistics
- Disruption event detection
- Time-of-day performance patterns
- Tower/cell connection history
- Speed test correlation
- Export to JSON, CSV, PDF formats
"""

import csv
import io
import statistics
import time
from datetime import datetime, timedelta
from typing import Any, TypedDict

import structlog

from ..config import get_settings
from ..db.connection import DatabaseConnection, get_db

log = structlog.get_logger()


# Signal quality thresholds for disruption detection
DISRUPTION_THRESHOLDS = {
    "nr_sinr": {"poor": 0, "critical": -5},
    "nr_rsrp": {"poor": -100, "critical": -110},
    "lte_sinr": {"poor": 0, "critical": -5},
    "lte_rsrp": {"poor": -100, "critical": -110},
}


class SignalStats(TypedDict, total=False):
    """Statistical summary for a metric."""

    count: int
    avg: float | None
    min: float | None
    max: float | None
    std_dev: float | None
    median: float | None


class DisruptionEvent(TypedDict, total=False):
    """A signal disruption event."""

    start_time: str
    start_unix: float
    end_time: str
    end_unix: float | None
    duration_seconds: float
    severity: str
    affected_metrics: list[str]
    tower_5g: int | None
    tower_4g: int | None
    bands_5g: str | None
    bands_4g: str | None


class TowerChange(TypedDict, total=False):
    """A tower/cell handoff event."""

    timestamp: str
    timestamp_unix: float
    type: str  # '5G' or '4G'
    from_tower: int | None
    from_cell: int | None
    to_tower: int | None
    to_cell: int | None
    bands: str | None


def calculate_statistics(values: list[float | None]) -> SignalStats:
    """Calculate statistical summary for a list of values."""
    clean_values = [v for v in values if v is not None]
    if not clean_values:
        return {
            "count": 0,
            "avg": None,
            "min": None,
            "max": None,
            "std_dev": None,
            "median": None,
        }

    return {
        "count": len(clean_values),
        "avg": round(statistics.mean(clean_values), 2),
        "min": round(min(clean_values), 2),
        "max": round(max(clean_values), 2),
        "std_dev": round(statistics.stdev(clean_values), 2) if len(clean_values) > 1 else 0,
        "median": round(statistics.median(clean_values), 2),
    }


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human readable string."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"


class DiagnosticsService:
    """Service for generating diagnostic reports."""

    def __init__(self, db: DatabaseConnection | None = None):
        self.db = db or get_db()

    async def get_signal_metrics_summary(self, duration_hours: int = 24) -> dict[str, Any]:
        """Get signal metrics summary with statistics.

        Args:
            duration_hours: How many hours of data to analyze

        Returns:
            Dictionary with 5G and 4G signal statistics
        """
        cutoff = time.time() - (duration_hours * 3600)

        async with self.db.connection() as db:
            cursor = await db.execute(
                """
                SELECT
                    timestamp_unix,
                    nr_sinr, nr_rsrp, nr_rsrq, nr_rssi,
                    lte_sinr, lte_rsrp, lte_rsrq, lte_rssi
                FROM signal_history
                WHERE timestamp_unix >= ?
                ORDER BY timestamp_unix ASC
                """,
                (cutoff,),
            )
            rows = await cursor.fetchall()

        if not rows:
            return {
                "duration_hours": duration_hours,
                "sample_count": 0,
                "5g": {},
                "4g": {},
            }

        # Collect values for each metric
        metrics = {
            "5g": {
                "sinr": [r["nr_sinr"] for r in rows],
                "rsrp": [r["nr_rsrp"] for r in rows],
                "rsrq": [r["nr_rsrq"] for r in rows],
                "rssi": [r["nr_rssi"] for r in rows],
            },
            "4g": {
                "sinr": [r["lte_sinr"] for r in rows],
                "rsrp": [r["lte_rsrp"] for r in rows],
                "rsrq": [r["lte_rsrq"] for r in rows],
                "rssi": [r["lte_rssi"] for r in rows],
            },
        }

        # Calculate statistics for each metric
        return {
            "duration_hours": duration_hours,
            "sample_count": len(rows),
            "start_time": datetime.fromtimestamp(rows[0]["timestamp_unix"]).isoformat(),
            "end_time": datetime.fromtimestamp(rows[-1]["timestamp_unix"]).isoformat(),
            "5g": {name: calculate_statistics(values) for name, values in metrics["5g"].items()},
            "4g": {name: calculate_statistics(values) for name, values in metrics["4g"].items()},
        }

    async def detect_disruptions(self, duration_hours: int = 24) -> dict[str, Any]:
        """Detect signal disruption events based on thresholds.

        Args:
            duration_hours: How many hours of data to analyze

        Returns:
            Dictionary with disruption events and counts
        """
        cutoff = time.time() - (duration_hours * 3600)

        async with self.db.connection() as db:
            cursor = await db.execute(
                """
                SELECT
                    timestamp, timestamp_unix,
                    nr_sinr, nr_rsrp, nr_rsrq,
                    lte_sinr, lte_rsrp, lte_rsrq,
                    nr_bands, lte_bands, nr_gnb_id, lte_enb_id
                FROM signal_history
                WHERE timestamp_unix >= ?
                ORDER BY timestamp_unix ASC
                """,
                (cutoff,),
            )
            rows = await cursor.fetchall()

        disruptions: list[DisruptionEvent] = []
        in_disruption = False
        disruption_start: float = 0
        disruption_metrics: DisruptionEvent = {}

        for row in rows:
            is_poor = False
            severity = "poor"
            affected_metrics: list[str] = []

            # Check 5G metrics
            if row["nr_sinr"] is not None:
                if row["nr_sinr"] <= DISRUPTION_THRESHOLDS["nr_sinr"]["critical"]:
                    is_poor = True
                    severity = "critical"
                    affected_metrics.append(f"5G SINR: {row['nr_sinr']}dB")
                elif row["nr_sinr"] <= DISRUPTION_THRESHOLDS["nr_sinr"]["poor"]:
                    is_poor = True
                    affected_metrics.append(f"5G SINR: {row['nr_sinr']}dB")

            if row["nr_rsrp"] is not None:
                if row["nr_rsrp"] <= DISRUPTION_THRESHOLDS["nr_rsrp"]["critical"]:
                    is_poor = True
                    severity = "critical"
                    affected_metrics.append(f"5G RSRP: {row['nr_rsrp']}dBm")
                elif row["nr_rsrp"] <= DISRUPTION_THRESHOLDS["nr_rsrp"]["poor"]:
                    is_poor = True
                    affected_metrics.append(f"5G RSRP: {row['nr_rsrp']}dBm")

            # Check 4G metrics
            if row["lte_sinr"] is not None:
                if row["lte_sinr"] <= DISRUPTION_THRESHOLDS["lte_sinr"]["critical"]:
                    is_poor = True
                    severity = "critical"
                    affected_metrics.append(f"4G SINR: {row['lte_sinr']}dB")
                elif row["lte_sinr"] <= DISRUPTION_THRESHOLDS["lte_sinr"]["poor"]:
                    is_poor = True
                    affected_metrics.append(f"4G SINR: {row['lte_sinr']}dB")

            if row["lte_rsrp"] is not None:
                if row["lte_rsrp"] <= DISRUPTION_THRESHOLDS["lte_rsrp"]["critical"]:
                    is_poor = True
                    severity = "critical"
                    affected_metrics.append(f"4G RSRP: {row['lte_rsrp']}dBm")
                elif row["lte_rsrp"] <= DISRUPTION_THRESHOLDS["lte_rsrp"]["poor"]:
                    is_poor = True
                    affected_metrics.append(f"4G RSRP: {row['lte_rsrp']}dBm")

            if is_poor and not in_disruption:
                # Start of disruption
                in_disruption = True
                disruption_start = row["timestamp_unix"]
                disruption_metrics = {
                    "start_time": row["timestamp"],
                    "start_unix": row["timestamp_unix"],
                    "severity": severity,
                    "affected_metrics": affected_metrics,
                    "tower_5g": row["nr_gnb_id"],
                    "tower_4g": row["lte_enb_id"],
                    "bands_5g": row["nr_bands"],
                    "bands_4g": row["lte_bands"],
                }
            elif not is_poor and in_disruption:
                # End of disruption
                in_disruption = False
                disruption_metrics["end_time"] = row["timestamp"]
                disruption_metrics["end_unix"] = row["timestamp_unix"]
                disruption_metrics["duration_seconds"] = round(
                    row["timestamp_unix"] - disruption_start, 1
                )
                disruptions.append(disruption_metrics)
                disruption_metrics = {}
            elif is_poor and in_disruption:
                # Update severity if worse
                if severity == "critical":
                    disruption_metrics["severity"] = "critical"

        # Handle ongoing disruption
        if in_disruption and disruption_metrics:
            disruption_metrics["end_time"] = "ongoing"
            disruption_metrics["end_unix"] = None
            disruption_metrics["duration_seconds"] = round(
                time.time() - disruption_start, 1
            )
            disruptions.append(disruption_metrics)

        return {
            "duration_hours": duration_hours,
            "total_disruptions": len(disruptions),
            "critical_count": sum(1 for d in disruptions if d.get("severity") == "critical"),
            "events": disruptions,
        }

    async def get_time_of_day_patterns(self, duration_hours: int = 168) -> dict[str, Any]:
        """Analyze signal performance patterns by time of day.

        Args:
            duration_hours: How many hours of data to analyze (default 7 days)

        Returns:
            Dictionary with hourly performance statistics
        """
        cutoff = time.time() - (duration_hours * 3600)

        async with self.db.connection() as db:
            cursor = await db.execute(
                """
                SELECT
                    timestamp_unix,
                    nr_sinr, nr_rsrp, lte_sinr, lte_rsrp
                FROM signal_history
                WHERE timestamp_unix >= ?
                ORDER BY timestamp_unix ASC
                """,
                (cutoff,),
            )
            rows = await cursor.fetchall()

        # Group by hour of day
        from collections import defaultdict
        hourly_data: dict[int, dict[str, list]] = defaultdict(
            lambda: {"nr_sinr": [], "nr_rsrp": [], "lte_sinr": [], "lte_rsrp": []}
        )

        for row in rows:
            hour = datetime.fromtimestamp(row["timestamp_unix"]).hour
            if row["nr_sinr"] is not None:
                hourly_data[hour]["nr_sinr"].append(row["nr_sinr"])
            if row["nr_rsrp"] is not None:
                hourly_data[hour]["nr_rsrp"].append(row["nr_rsrp"])
            if row["lte_sinr"] is not None:
                hourly_data[hour]["lte_sinr"].append(row["lte_sinr"])
            if row["lte_rsrp"] is not None:
                hourly_data[hour]["lte_rsrp"].append(row["lte_rsrp"])

        # Calculate statistics per hour
        patterns = {}
        for hour in range(24):
            data = hourly_data[hour]
            patterns[hour] = {
                "hour_label": f"{hour:02d}:00",
                "sample_count": len(data["nr_sinr"]),
                "5g_sinr_avg": round(statistics.mean(data["nr_sinr"]), 2) if data["nr_sinr"] else None,
                "5g_rsrp_avg": round(statistics.mean(data["nr_rsrp"]), 2) if data["nr_rsrp"] else None,
                "4g_sinr_avg": round(statistics.mean(data["lte_sinr"]), 2) if data["lte_sinr"] else None,
                "4g_rsrp_avg": round(statistics.mean(data["lte_rsrp"]), 2) if data["lte_rsrp"] else None,
            }

        # Find best and worst hours
        valid_hours = [(h, p["5g_sinr_avg"]) for h, p in patterns.items() if p["5g_sinr_avg"] is not None]
        if valid_hours:
            best_hour = max(valid_hours, key=lambda x: x[1])
            worst_hour = min(valid_hours, key=lambda x: x[1])
        else:
            best_hour = (None, None)
            worst_hour = (None, None)

        return {
            "duration_hours": duration_hours,
            "hourly_patterns": patterns,
            "best_hour": {"hour": best_hour[0], "5g_sinr_avg": best_hour[1]},
            "worst_hour": {"hour": worst_hour[0], "5g_sinr_avg": worst_hour[1]},
        }

    async def get_tower_connection_history(self, duration_hours: int = 24) -> dict[str, Any]:
        """Get history of tower/cell connections.

        Args:
            duration_hours: How many hours of data to analyze

        Returns:
            Dictionary with tower connection events and statistics
        """
        cutoff = time.time() - (duration_hours * 3600)

        async with self.db.connection() as db:
            cursor = await db.execute(
                """
                SELECT
                    timestamp, timestamp_unix,
                    nr_gnb_id, nr_cid, nr_bands,
                    lte_enb_id, lte_cid, lte_bands
                FROM signal_history
                WHERE timestamp_unix >= ?
                ORDER BY timestamp_unix ASC
                """,
                (cutoff,),
            )
            rows = await cursor.fetchall()

        if not rows:
            return {
                "duration_hours": duration_hours,
                "tower_changes": [],
                "tower_summary": {},
            }

        # Track tower changes
        from collections import defaultdict
        changes: list[TowerChange] = []
        last_5g_tower: tuple | None = None
        last_4g_tower: tuple | None = None
        tower_durations: dict[str, float] = defaultdict(float)
        last_timestamp: float | None = None

        for row in rows:
            current_5g = (row["nr_gnb_id"], row["nr_cid"], row["nr_bands"])
            current_4g = (row["lte_enb_id"], row["lte_cid"], row["lte_bands"])

            # Track duration on current tower
            if last_timestamp is not None:
                duration = row["timestamp_unix"] - last_timestamp
                if last_5g_tower and last_5g_tower[0]:
                    tower_durations[f"5G-{last_5g_tower[0]}"] += duration
                if last_4g_tower and last_4g_tower[0]:
                    tower_durations[f"4G-{last_4g_tower[0]}"] += duration

            # Check for 5G tower change
            if current_5g != last_5g_tower and current_5g[0] is not None:
                if last_5g_tower is not None:
                    changes.append({
                        "timestamp": row["timestamp"],
                        "timestamp_unix": row["timestamp_unix"],
                        "type": "5G",
                        "from_tower": last_5g_tower[0],
                        "from_cell": last_5g_tower[1],
                        "to_tower": current_5g[0],
                        "to_cell": current_5g[1],
                        "bands": current_5g[2],
                    })
                last_5g_tower = current_5g

            # Check for 4G tower change
            if current_4g != last_4g_tower and current_4g[0] is not None:
                if last_4g_tower is not None:
                    changes.append({
                        "timestamp": row["timestamp"],
                        "timestamp_unix": row["timestamp_unix"],
                        "type": "4G",
                        "from_tower": last_4g_tower[0],
                        "from_cell": last_4g_tower[1],
                        "to_tower": current_4g[0],
                        "to_cell": current_4g[1],
                        "bands": current_4g[2],
                    })
                last_4g_tower = current_4g

            last_timestamp = row["timestamp_unix"]

        # Calculate tower summary
        tower_summary = {}
        total_duration = duration_hours * 3600
        for tower_id, duration in tower_durations.items():
            tower_summary[tower_id] = {
                "duration_seconds": round(duration, 1),
                "duration_formatted": format_duration(duration),
                "percentage": round(duration / total_duration * 100, 1) if total_duration > 0 else 0,
            }

        return {
            "duration_hours": duration_hours,
            "total_changes": len(changes),
            "tower_changes": changes[-50:],  # Last 50 changes
            "tower_summary": tower_summary,
            "unique_5g_towers": len([k for k in tower_summary if k.startswith("5G")]),
            "unique_4g_towers": len([k for k in tower_summary if k.startswith("4G")]),
        }

    async def generate_full_report(self, duration_hours: int = 24) -> dict[str, Any]:
        """Generate comprehensive diagnostic report.

        Args:
            duration_hours: How many hours of data to analyze

        Returns:
            Complete diagnostic report
        """
        report = {
            "generated_at": datetime.utcnow().isoformat(),
            "duration_hours": duration_hours,
            "signal_summary": await self.get_signal_metrics_summary(duration_hours),
            "disruptions": await self.detect_disruptions(duration_hours),
            "time_patterns": await self.get_time_of_day_patterns(min(duration_hours, 168)),
            "tower_history": await self.get_tower_connection_history(duration_hours),
        }

        # Add overall health score
        report["health_score"] = self._calculate_health_score(report)

        return report

    def _calculate_health_score(self, report: dict[str, Any]) -> dict[str, Any]:
        """Calculate overall connection health score (0-100).

        Args:
            report: Full diagnostic report

        Returns:
            Dictionary with score and breakdown
        """
        scores = []
        breakdown = {}

        # Signal quality score (based on averages)
        signal_summary = report.get("signal_summary", {})

        # 5G SINR score
        sinr_5g = signal_summary.get("5g", {}).get("sinr", {}).get("avg")
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
            breakdown["5g_sinr"] = sinr_score

        # 5G RSRP score
        rsrp_5g = signal_summary.get("5g", {}).get("rsrp", {}).get("avg")
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
            breakdown["5g_rsrp"] = rsrp_score

        # Disruption penalty
        disruptions = report.get("disruptions", {})
        total_disruptions = disruptions.get("total_disruptions", 0)
        critical_disruptions = disruptions.get("critical_count", 0)

        if total_disruptions == 0:
            stability_score = 100
        elif total_disruptions <= 5:
            stability_score = 80 - (critical_disruptions * 10)
        elif total_disruptions <= 20:
            stability_score = 50 - (critical_disruptions * 5)
        else:
            stability_score = max(0, 30 - (critical_disruptions * 5))

        scores.append(stability_score)
        breakdown["stability"] = stability_score

        # Tower stability score
        tower_history = report.get("tower_history", {})
        tower_changes = tower_history.get("total_changes", 0)

        if tower_changes <= 2:
            tower_score = 100
        elif tower_changes <= 10:
            tower_score = 70
        elif tower_changes <= 50:
            tower_score = 40
        else:
            tower_score = 20

        scores.append(tower_score)
        breakdown["tower_stability"] = tower_score

        overall = round(statistics.mean(scores)) if scores else 0

        # Determine grade
        if overall >= 90:
            grade = "A"
        elif overall >= 80:
            grade = "B"
        elif overall >= 70:
            grade = "C"
        elif overall >= 60:
            grade = "D"
        else:
            grade = "F"

        return {
            "overall": overall,
            "grade": grade,
            "breakdown": breakdown,
        }

    def export_to_json(self, report: dict[str, Any]) -> str:
        """Export report to JSON format."""
        import json
        return json.dumps(report, indent=2, default=str)

    def export_to_csv(self, report: dict[str, Any]) -> str:
        """Export report to CSV format."""
        output = io.StringIO()

        # Signal Summary Section
        output.write("=== T-MOBILE DIAGNOSTIC REPORT ===\n")
        output.write(f"Generated: {report['generated_at']}\n")
        output.write(f"Duration: {report['duration_hours']} hours\n")
        output.write(f"Health Score: {report['health_score']['overall']}/100 ({report['health_score']['grade']})\n\n")

        # Signal metrics
        output.write("=== SIGNAL METRICS SUMMARY ===\n")
        writer = csv.writer(output)
        writer.writerow(["Network", "Metric", "Average", "Min", "Max", "Std Dev", "Median", "Samples"])

        for network in ["5g", "4g"]:
            for metric, stats in report["signal_summary"].get(network, {}).items():
                if isinstance(stats, dict):
                    writer.writerow([
                        network.upper(),
                        metric.upper(),
                        stats.get("avg"),
                        stats.get("min"),
                        stats.get("max"),
                        stats.get("std_dev"),
                        stats.get("median"),
                        stats.get("count"),
                    ])

        output.write("\n")

        # Disruptions
        output.write("=== DISRUPTION EVENTS ===\n")
        writer.writerow(["Start Time", "End Time", "Duration (s)", "Severity", "Tower 5G", "Tower 4G", "Affected Metrics"])

        for event in report["disruptions"].get("events", []):
            writer.writerow([
                event.get("start_time"),
                event.get("end_time"),
                event.get("duration_seconds"),
                event.get("severity"),
                event.get("tower_5g"),
                event.get("tower_4g"),
                "; ".join(event.get("affected_metrics", [])),
            ])

        output.write("\n")

        # Time of Day Patterns
        output.write("=== TIME OF DAY PATTERNS ===\n")
        writer.writerow(["Hour", "Samples", "5G SINR Avg", "5G RSRP Avg", "4G SINR Avg", "4G RSRP Avg"])

        for hour, pattern in sorted(report["time_patterns"].get("hourly_patterns", {}).items()):
            writer.writerow([
                pattern.get("hour_label"),
                pattern.get("sample_count"),
                pattern.get("5g_sinr_avg"),
                pattern.get("5g_rsrp_avg"),
                pattern.get("4g_sinr_avg"),
                pattern.get("4g_rsrp_avg"),
            ])

        output.write("\n")

        # Tower History
        output.write("=== TOWER CONNECTION SUMMARY ===\n")
        writer.writerow(["Tower ID", "Duration", "Percentage"])

        for tower_id, stats in report["tower_history"].get("tower_summary", {}).items():
            writer.writerow([
                tower_id,
                stats.get("duration_formatted"),
                f"{stats.get('percentage')}%",
            ])

        return output.getvalue()


# Global service instance
_diagnostics_service: DiagnosticsService | None = None


def get_diagnostics_service() -> DiagnosticsService:
    """Get the global diagnostics service instance."""
    global _diagnostics_service
    if _diagnostics_service is None:
        _diagnostics_service = DiagnosticsService()
    return _diagnostics_service
