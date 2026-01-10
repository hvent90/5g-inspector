"""Scheduled speed test service for automated testing."""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import structlog

from ..config import get_settings
from ..db import get_db

log = structlog.get_logger()


class SchedulerConfig:
    """Scheduler configuration."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self.enabled: bool = data.get("enabled", True)  # Enabled by default
        self.interval_minutes: int = data.get("interval_minutes", 10)  # 10-minute intervals
        self.min_interval_minutes: int = data.get("min_interval_minutes", 5)
        self.max_interval_minutes: int = data.get("max_interval_minutes", 1440)
        self.start_hour: int = data.get("start_hour", 0)
        self.end_hour: int = data.get("end_hour", 24)
        self.test_on_weekends: bool = data.get("test_on_weekends", True)
        self.low_speed_threshold_mbps: float = data.get("low_speed_threshold_mbps", 10)
        self.collection_start_date: str | None = data.get("collection_start_date")
        self.target_days: int = data.get("target_days", 30)

        # Network context settings for distinguishing clean baselines
        # Hours considered "baseline" - when you're typically asleep/not using network
        self.idle_hours: list[int] = data.get("idle_hours", [2, 3, 4, 5])
        # Expected ping latency when network is idle (ms) - learns automatically if not set
        self.baseline_latency_ms: float = data.get("baseline_latency_ms", 20.0)
        # Latency multiplier thresholds for context inference
        self.light_latency_multiplier: float = data.get("light_latency_multiplier", 1.5)
        self.busy_latency_multiplier: float = data.get("busy_latency_multiplier", 2.5)
        # Enable pre-test latency probe
        self.enable_latency_probe: bool = data.get("enable_latency_probe", True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "interval_minutes": self.interval_minutes,
            "min_interval_minutes": self.min_interval_minutes,
            "max_interval_minutes": self.max_interval_minutes,
            "start_hour": self.start_hour,
            "end_hour": self.end_hour,
            "test_on_weekends": self.test_on_weekends,
            "low_speed_threshold_mbps": self.low_speed_threshold_mbps,
            "collection_start_date": self.collection_start_date,
            "target_days": self.target_days,
            "idle_hours": self.idle_hours,
            "baseline_latency_ms": self.baseline_latency_ms,
            "light_latency_multiplier": self.light_latency_multiplier,
            "busy_latency_multiplier": self.busy_latency_multiplier,
            "enable_latency_probe": self.enable_latency_probe,
        }


class SchedulerService:
    """Service for scheduling automated speed tests."""

    def __init__(self) -> None:
        settings = get_settings()
        self._config_path = Path(settings.database.path).parent / "scheduler_config.json"
        self._db = get_db()

        self._config = SchedulerConfig()
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._last_test_time: float = 0
        self._next_test_time: float = 0
        self._run_speedtest_func: Callable | None = None

        self._stats = {
            "tests_completed": 0,
            "tests_failed": 0,
            "tests_below_threshold": 0,
            "avg_download_mbps": 0.0,
            "avg_upload_mbps": 0.0,
            "min_download_mbps": None,
            "max_download_mbps": None,
            "scheduler_started_at": None,
            "last_error": None,
        }

        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from file."""
        if self._config_path.exists():
            try:
                with open(self._config_path) as f:
                    data = json.load(f)
                self._config = SchedulerConfig(data)
            except Exception as e:
                log.error("scheduler_config_load_failed", error=str(e))

    def _save_config(self) -> None:
        """Save configuration to file."""
        try:
            with open(self._config_path, "w") as f:
                json.dump(self._config.to_dict(), f, indent=2)
        except Exception as e:
            log.error("scheduler_config_save_failed", error=str(e))

    def set_speedtest_func(self, func: Callable) -> None:
        """Set the function to call for running speed tests."""
        self._run_speedtest_func = func

    def get_config(self) -> dict:
        """Get current scheduler configuration."""
        return self._config.to_dict()

    def update_config(self, updates: dict) -> dict:
        """Update scheduler configuration."""
        old_interval = self._config.interval_minutes
        current = self._config.to_dict()
        current.update(updates)

        # Validate interval
        interval = current.get("interval_minutes", 30)
        min_interval = current.get("min_interval_minutes", 5)
        max_interval = current.get("max_interval_minutes", 1440)
        current["interval_minutes"] = max(min_interval, min(max_interval, interval))

        # Validate hours
        current["start_hour"] = max(0, min(23, current.get("start_hour", 0)))
        current["end_hour"] = max(1, min(24, current.get("end_hour", 24)))

        self._config = SchedulerConfig(current)
        self._save_config()

        # Recalculate next test time if interval changed and scheduler is running
        new_interval = self._config.interval_minutes
        if new_interval != old_interval and self._last_test_time:
            self._next_test_time = self._last_test_time + (new_interval * 60)
            log.info(
                "scheduler_interval_updated",
                old_interval=old_interval,
                new_interval=new_interval,
                next_test_in_seconds=max(0, self._next_test_time - time.time()),
            )

        # Handle enabled state change
        if updates.get("enabled") and not self.is_running():
            asyncio.create_task(self.start())
        elif updates.get("enabled") is False and self.is_running():
            asyncio.create_task(self.stop())

        return self.get_config()

    def get_stats(self) -> dict:
        """Get scheduler statistics."""
        stats = dict(self._stats)
        stats["is_running"] = self.is_running()
        stats["last_test_time"] = self._last_test_time
        stats["next_test_time"] = self._next_test_time
        stats["next_test_in_seconds"] = (
            max(0, self._next_test_time - time.time()) if self._next_test_time else None
        )

        # Add collection progress
        if self._config.collection_start_date:
            try:
                start_date = datetime.fromisoformat(self._config.collection_start_date)
                days_collected = (datetime.now() - start_date).days
                stats["collection_days"] = days_collected
                stats["collection_target_days"] = self._config.target_days
                stats["collection_progress_percent"] = min(
                    100, round(days_collected / self._config.target_days * 100, 1)
                )
            except Exception:
                pass

        return stats

    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._task is not None and not self._task.done()

    async def start(self) -> bool:
        """Start the scheduler."""
        if self.is_running():
            return False

        self._stop_event.clear()
        self._config.enabled = True
        self._save_config()

        # Set collection start date if not set
        if not self._config.collection_start_date:
            self._config.collection_start_date = datetime.now().isoformat()
            self._save_config()

        self._task = asyncio.create_task(self._scheduler_loop())
        self._stats["scheduler_started_at"] = datetime.now().isoformat()

        log.info("scheduler_started", interval_minutes=self._config.interval_minutes)
        return True

    async def stop(self) -> bool:
        """Stop the scheduler."""
        if not self.is_running():
            return False

        self._stop_event.set()
        self._config.enabled = False
        self._save_config()

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        log.info("scheduler_stopped")
        return True

    def _is_in_test_window(self) -> bool:
        """Check if current time is within the configured testing window."""
        now = datetime.now()
        current_hour = now.hour

        # Check weekend setting
        if not self._config.test_on_weekends and now.weekday() >= 5:
            return False

        # Check hour window
        if self._config.end_hour > self._config.start_hour:
            return self._config.start_hour <= current_hour < self._config.end_hour
        else:
            # Overnight window
            return current_hour >= self._config.start_hour or current_hour < self._config.end_hour

    async def _scheduler_loop(self) -> None:
        """Main scheduler loop."""
        log.info("scheduler_loop_started")

        while not self._stop_event.is_set():
            if not self._config.enabled:
                await asyncio.sleep(1)
                continue

            # Calculate next test time
            interval_seconds = self._config.interval_minutes * 60
            if self._last_test_time:
                # Normal case: schedule next test based on last run
                self._next_test_time = self._last_test_time + interval_seconds
            elif self._next_test_time == 0:
                # First run after startup: wait full interval before first test
                # This prevents immediate tests on every hot reload
                self._next_test_time = time.time() + interval_seconds
                log.info(
                    "scheduler_first_test_scheduled",
                    next_test_in_seconds=interval_seconds,
                )

            # Wait until next test time
            while not self._stop_event.is_set():
                wait_time = self._next_test_time - time.time()
                if wait_time <= 0:
                    break
                await asyncio.sleep(min(wait_time, 1))

            if self._stop_event.is_set():
                break

            # Check if in test window
            if not self._is_in_test_window():
                log.debug("scheduler_outside_window")
                self._last_test_time = time.time()
                continue

            # Run the test
            await self._run_scheduled_test()

        log.info("scheduler_loop_stopped")

    async def _run_scheduled_test(self) -> dict | None:
        """Run a scheduled speed test."""
        if not self._run_speedtest_func:
            log.warning("scheduler_no_speedtest_func")
            return None

        log.info("scheduler_running_test")
        self._last_test_time = time.time()

        try:
            result = await self._run_speedtest_func()
            self._update_stats(result)
            await self._save_to_database(result)
            return result
        except Exception as e:
            log.error("scheduler_test_error", error=str(e))
            self._stats["tests_failed"] += 1
            self._stats["last_error"] = str(e)
            return None

    def _update_stats(self, result: dict) -> None:
        """Update statistics after a test."""
        if result.get("status") == "success":
            self._stats["tests_completed"] += 1

            download = result.get("download_mbps", 0)
            upload = result.get("upload_mbps", 0)

            # Update running averages
            n = self._stats["tests_completed"]
            self._stats["avg_download_mbps"] = round(
                (self._stats["avg_download_mbps"] * (n - 1) + download) / n, 2
            )
            self._stats["avg_upload_mbps"] = round(
                (self._stats["avg_upload_mbps"] * (n - 1) + upload) / n, 2
            )

            # Update min/max
            if self._stats["min_download_mbps"] is None or download < self._stats["min_download_mbps"]:
                self._stats["min_download_mbps"] = download
            if self._stats["max_download_mbps"] is None or download > self._stats["max_download_mbps"]:
                self._stats["max_download_mbps"] = download

            # Check threshold
            if download < self._config.low_speed_threshold_mbps:
                self._stats["tests_below_threshold"] += 1
        else:
            self._stats["tests_failed"] += 1
            self._stats["last_error"] = result.get("error", "Unknown error")

    async def _save_to_database(self, result: dict) -> None:
        """Save speed test result to database."""
        # Results are saved by the speedtest service itself
        pass

    async def trigger_test_now(self) -> dict:
        """Manually trigger an immediate test."""
        if not self._run_speedtest_func:
            return {"status": "error", "error": "Speed test function not configured"}

        log.info("scheduler_manual_test_triggered")
        result = await self._run_speedtest_func()
        self._update_stats(result)

        # Reset the schedule so next test waits full interval from now
        self._last_test_time = time.time()
        self._next_test_time = self._last_test_time + (self._config.interval_minutes * 60)

        return result

    async def get_history(
        self,
        limit: int = 100,
        offset: int = 0,
        status_filter: str | None = None,
        hour_filter: int | None = None,
    ) -> dict:
        """Get scheduled test history from database."""
        async with self._db.connection() as db:
            where_clauses = []
            params: list[Any] = []

            if status_filter:
                where_clauses.append("status = ?")
                params.append(status_filter)

            if hour_filter is not None:
                # Filter by hour of day (extracted from timestamp)
                where_clauses.append("CAST(strftime('%H', timestamp) AS INTEGER) = ?")
                params.append(hour_filter)

            where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

            # Get total count
            cursor = await db.execute(
                f"SELECT COUNT(*) FROM speedtest_results{where_sql}", params
            )
            row = await cursor.fetchone()
            total_count = row[0] if row else 0

            # Get results
            cursor = await db.execute(
                f"""
                SELECT * FROM speedtest_results
                {where_sql}
                ORDER BY timestamp_unix DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            )
            rows = await cursor.fetchall()

            return {
                "total_count": total_count,
                "count": len(rows),
                "offset": offset,
                "limit": limit,
                "results": [dict(row) for row in rows],
            }

    async def get_hourly_stats(self) -> dict:
        """Get aggregated statistics by hour of day."""
        async with self._db.connection() as db:
            cursor = await db.execute(
                """
                SELECT
                    CAST(strftime('%H', timestamp) AS INTEGER) as hour_of_day,
                    COUNT(*) as test_count,
                    AVG(download_mbps) as avg_download,
                    MIN(download_mbps) as min_download,
                    MAX(download_mbps) as max_download,
                    AVG(upload_mbps) as avg_upload,
                    AVG(ping_ms) as avg_ping
                FROM speedtest_results
                WHERE status = 'success'
                GROUP BY hour_of_day
                ORDER BY hour_of_day
                """
            )
            hourly_data = [dict(row) for row in await cursor.fetchall()]

            # Get overall stats
            cursor = await db.execute(
                """
                SELECT
                    COUNT(*) as total_tests,
                    AVG(download_mbps) as overall_avg_download
                FROM speedtest_results
                WHERE status = 'success'
                """
            )
            row = await cursor.fetchone()
            overall = dict(row) if row else {}

            # Find worst and best hours
            worst_hour = None
            best_hour = None
            if hourly_data:
                worst_hour = min(hourly_data, key=lambda x: x.get("avg_download") or float("inf"))
                best_hour = max(hourly_data, key=lambda x: x.get("avg_download") or 0)

            return {
                "hourly_breakdown": hourly_data,
                "overall": overall,
                "worst_hour": worst_hour,
                "best_hour": best_hour,
            }

    async def get_evidence_summary(self) -> dict:
        """Generate summary data for FCC complaint evidence."""
        async with self._db.connection() as db:
            cursor = await db.execute(
                """
                SELECT
                    COUNT(*) as total_tests,
                    MIN(timestamp_unix) as first_test,
                    MAX(timestamp_unix) as last_test,
                    AVG(download_mbps) as avg_download,
                    MIN(download_mbps) as min_download,
                    MAX(download_mbps) as max_download,
                    AVG(upload_mbps) as avg_upload,
                    AVG(ping_ms) as avg_ping
                FROM speedtest_results
                WHERE status = 'success'
                """
            )
            row = await cursor.fetchone()
            overall = dict(row) if row else {}

            # Calculate collection period
            collection_days = 0.0
            if overall.get("first_test") and overall.get("last_test"):
                collection_days = (overall["last_test"] - overall["first_test"]) / 86400

            # Advertised vs actual (default T-Mobile advertised range)
            advertised_min = 133
            advertised_max = 415
            actual_avg = overall.get("avg_download") or 0

            return {
                "collection_period": {
                    "days": round(collection_days, 1),
                    "first_test": (
                        datetime.fromtimestamp(overall["first_test"]).isoformat()
                        if overall.get("first_test")
                        else None
                    ),
                    "last_test": (
                        datetime.fromtimestamp(overall["last_test"]).isoformat()
                        if overall.get("last_test")
                        else None
                    ),
                    "total_tests": overall.get("total_tests") or 0,
                },
                "speed_metrics": {
                    "average_download_mbps": round(actual_avg, 2),
                    "min_download_mbps": round(overall.get("min_download") or 0, 2),
                    "max_download_mbps": round(overall.get("max_download") or 0, 2),
                    "average_upload_mbps": round(overall.get("avg_upload") or 0, 2),
                    "average_ping_ms": round(overall.get("avg_ping") or 0, 1),
                },
                "advertised_vs_actual": {
                    "advertised_range_mbps": f"{advertised_min}-{advertised_max}",
                    "actual_average_mbps": round(actual_avg, 2),
                    "percent_of_min_advertised": (
                        round(actual_avg / advertised_min * 100, 1) if actual_avg else 0
                    ),
                    "shortfall_mbps": (
                        round(advertised_min - actual_avg, 2)
                        if actual_avg < advertised_min
                        else 0
                    ),
                },
            }


# Global service instance
_scheduler_service: SchedulerService | None = None


def get_scheduler_service() -> SchedulerService:
    """Get the global scheduler service instance."""
    global _scheduler_service
    if _scheduler_service is None:
        _scheduler_service = SchedulerService()
    return _scheduler_service
