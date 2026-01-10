"""Network quality monitoring service for packet loss and jitter tracking."""

import asyncio
import json
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from ..config import get_settings
from ..db import get_db
from ..models import NetworkQualityResult, SignalData

log = structlog.get_logger()


class NetworkQualityConfig:
    """Network quality monitor configuration."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self.enabled: bool = data.get("enabled", False)
        self.interval_minutes: int = data.get("interval_minutes", 5)
        self.min_interval_minutes: int = data.get("min_interval_minutes", 1)
        self.max_interval_minutes: int = data.get("max_interval_minutes", 60)
        self.ping_count: int = data.get("ping_count", 20)
        self.ping_timeout_seconds: int = data.get("ping_timeout_seconds", 5)
        self.targets: list[dict] = data.get(
            "targets",
            [
                {"host": "8.8.8.8", "name": "Google DNS"},
                {"host": "1.1.1.1", "name": "Cloudflare DNS"},
                {"host": "208.54.0.1", "name": "T-Mobile DNS"},
            ],
        )
        self.packet_loss_threshold_percent: float = data.get("packet_loss_threshold_percent", 5)
        self.jitter_threshold_ms: float = data.get("jitter_threshold_ms", 50)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "interval_minutes": self.interval_minutes,
            "min_interval_minutes": self.min_interval_minutes,
            "max_interval_minutes": self.max_interval_minutes,
            "ping_count": self.ping_count,
            "ping_timeout_seconds": self.ping_timeout_seconds,
            "targets": self.targets,
            "packet_loss_threshold_percent": self.packet_loss_threshold_percent,
            "jitter_threshold_ms": self.jitter_threshold_ms,
        }


class NetworkQualityService:
    """Service for network quality monitoring."""

    def __init__(self) -> None:
        settings = get_settings()
        self._config_path = Path(settings.database.path).parent / "network_quality_config.json"
        self._db = get_db()

        self._config = NetworkQualityConfig()
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._last_test_time: float = 0
        self._next_test_time: float = 0

        self._stats = {
            "tests_completed": 0,
            "tests_failed": 0,
            "tests_above_threshold": 0,
            "avg_packet_loss": 0.0,
            "avg_jitter": 0.0,
            "max_packet_loss": None,
            "max_jitter": None,
            "monitor_started_at": None,
            "last_error": None,
        }

        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from file."""
        if self._config_path.exists():
            try:
                with open(self._config_path) as f:
                    data = json.load(f)
                self._config = NetworkQualityConfig(data)
            except Exception as e:
                log.error("network_quality_config_load_failed", error=str(e))

    def _save_config(self) -> None:
        """Save configuration to file."""
        try:
            with open(self._config_path, "w") as f:
                json.dump(self._config.to_dict(), f, indent=2)
        except Exception as e:
            log.error("network_quality_config_save_failed", error=str(e))

    def _is_speedtest_running(self) -> bool:
        """Check if a speedtest is currently running."""
        try:
            # Import here to avoid circular imports
            from ..services.lifespan import get_state
            state = get_state()
            return state.speedtest.is_running
        except Exception:
            # If we can't check, assume not running
            return False

    async def _push_to_loki(self, result: NetworkQualityResult, target_name: str) -> None:
        """Push network quality result to Loki for event visualization."""
        try:
            from ..config import get_settings
            settings = get_settings()
            if not settings.loki.enabled:
                return

            from .loki import get_loki_client
            loki = get_loki_client()

            event_data = {
                "event": "network_quality_result",
                "timestamp_unix": result.timestamp_unix,
                "ping_ms": result.ping_ms,
                "jitter_ms": result.jitter_ms,
                "packet_loss_percent": result.packet_loss_percent,
                "target_host": result.target_host,
                "target_name": target_name,
                "speedtest_active": result.speedtest_active,
            }

            # Add signal snapshot for correlation
            if result.signal_at_test:
                event_data["signal"] = {
                    "nr_sinr": result.signal_at_test.nr.sinr,
                    "nr_rsrp": result.signal_at_test.nr.rsrp,
                }

            labels = {
                "target": result.target_host,
                "speedtest_active": "true" if result.speedtest_active else "false",
            }

            await loki.push_event("network_quality", event_data, labels)

        except Exception as e:
            log.warning("loki_network_quality_push_error", error=str(e))

    def get_config(self) -> dict:
        """Get current monitor configuration."""
        return self._config.to_dict()

    def update_config(self, updates: dict) -> dict:
        """Update monitor configuration."""
        current = self._config.to_dict()
        current.update(updates)

        # Validate interval
        interval = current.get("interval_minutes", 5)
        min_interval = current.get("min_interval_minutes", 1)
        max_interval = current.get("max_interval_minutes", 60)
        current["interval_minutes"] = max(min_interval, min(max_interval, interval))

        self._config = NetworkQualityConfig(current)
        self._save_config()

        # Handle enabled state change
        if updates.get("enabled") and not self.is_running():
            asyncio.create_task(self.start())
        elif updates.get("enabled") is False and self.is_running():
            asyncio.create_task(self.stop())

        return self.get_config()

    def get_stats(self) -> dict:
        """Get monitoring statistics."""
        stats = dict(self._stats)
        stats["is_running"] = self.is_running()
        stats["last_test_time"] = self._last_test_time
        stats["next_test_time"] = self._next_test_time
        stats["next_test_in_seconds"] = (
            max(0, self._next_test_time - time.time()) if self._next_test_time else None
        )
        return stats

    def is_running(self) -> bool:
        """Check if monitor is running."""
        return self._task is not None and not self._task.done()

    async def start(self) -> bool:
        """Start the network quality monitor."""
        if self.is_running():
            return False

        self._stop_event.clear()
        self._config.enabled = True
        self._save_config()

        self._task = asyncio.create_task(self._monitor_loop())
        self._stats["monitor_started_at"] = datetime.now().isoformat()

        log.info("network_quality_monitor_started", interval_minutes=self._config.interval_minutes)
        return True

    async def stop(self) -> bool:
        """Stop the network quality monitor."""
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

        log.info("network_quality_monitor_stopped")
        return True

    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        log.info("network_quality_loop_started")

        while not self._stop_event.is_set():
            if not self._config.enabled:
                await asyncio.sleep(1)
                continue

            # Calculate next test time
            interval_seconds = self._config.interval_minutes * 60
            self._next_test_time = (
                self._last_test_time + interval_seconds
                if self._last_test_time
                else time.time()
            )

            # Wait until next test time
            while not self._stop_event.is_set():
                wait_time = self._next_test_time - time.time()
                if wait_time <= 0:
                    break
                await asyncio.sleep(min(wait_time, 1))

            if self._stop_event.is_set():
                break

            # Run the test
            self._last_test_time = time.time()
            try:
                await self.run_test()
            except Exception as e:
                log.error("network_quality_test_error", error=str(e))
                self._stats["last_error"] = str(e)

        log.info("network_quality_loop_stopped")

    def _run_ping_test(self, host: str) -> dict:
        """Run a ping test against a specific host."""
        start_time = time.time()
        ping_times = []
        packets_sent = self._config.ping_count
        error_message = None

        # Determine ping command based on platform
        if sys.platform == "win32":
            cmd = [
                "ping",
                "-n",
                str(packets_sent),
                "-w",
                str(self._config.ping_timeout_seconds * 1000),
                host,
            ]
        else:
            cmd = [
                "ping",
                "-c",
                str(packets_sent),
                "-W",
                str(self._config.ping_timeout_seconds),
                host,
            ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=packets_sent * self._config.ping_timeout_seconds + 10,
            )

            output = result.stdout

            if sys.platform == "win32":
                # Parse Windows ping output
                time_pattern = r"time[=<](\d+(?:\.\d+)?)\s*ms"
                matches = re.findall(time_pattern, output, re.IGNORECASE)
                ping_times = [float(m) for m in matches]

                loss_pattern = r"\((\d+)%\s+loss\)"
                loss_match = re.search(loss_pattern, output)
                if loss_match:
                    packet_loss_percent = float(loss_match.group(1))
                else:
                    packets_received = len(ping_times)
                    packet_loss_percent = (
                        (packets_sent - packets_received) / packets_sent * 100
                        if packets_sent > 0
                        else 100
                    )
            else:
                # Parse Unix ping output
                time_pattern = r"time=(\d+(?:\.\d+)?)\s*ms"
                matches = re.findall(time_pattern, output)
                ping_times = [float(m) for m in matches]

                loss_pattern = r"(\d+(?:\.\d+)?)\s*%\s+packet\s+loss"
                loss_match = re.search(loss_pattern, output, re.IGNORECASE)
                if loss_match:
                    packet_loss_percent = float(loss_match.group(1))
                else:
                    packets_received = len(ping_times)
                    packet_loss_percent = (
                        (packets_sent - packets_received) / packets_sent * 100
                        if packets_sent > 0
                        else 100
                    )

        except subprocess.TimeoutExpired:
            error_message = "Ping test timed out"
            packet_loss_percent = 100.0
        except Exception as e:
            error_message = str(e)
            packet_loss_percent = 100.0

        # Calculate jitter
        jitter_ms = 0.0
        if len(ping_times) >= 2:
            differences = [
                abs(ping_times[i] - ping_times[i - 1]) for i in range(1, len(ping_times))
            ]
            jitter_ms = statistics.mean(differences) if differences else 0

        return {
            "packets_sent": packets_sent,
            "packets_received": len(ping_times),
            "packet_loss_percent": round(packet_loss_percent, 2),
            "latency_min": round(min(ping_times), 2) if ping_times else None,
            "latency_avg": round(statistics.mean(ping_times), 2) if ping_times else None,
            "latency_max": round(max(ping_times), 2) if ping_times else None,
            "jitter_ms": round(jitter_ms, 2),
            "ping_times": ping_times,
            "test_duration_seconds": round(time.time() - start_time, 2),
            "status": "success" if error_message is None else "error",
            "error_message": error_message,
        }

    async def run_test(
        self, signal_snapshot: SignalData | None = None
    ) -> list[dict]:
        """Run network quality tests against all configured targets."""
        results = []
        test_time = datetime.now()

        # Check if a speedtest is currently running
        speedtest_active = self._is_speedtest_running()
        if speedtest_active:
            log.info("network_quality_speedtest_active", message="Speedtest running during measurement")

        for target in self._config.targets:
            host = target.get("host", "")
            name = target.get("name", host)

            log.debug("network_quality_testing", target=name, host=host)

            # Run ping test in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            test_result = await loop.run_in_executor(None, self._run_ping_test, host)

            result = NetworkQualityResult(
                timestamp=test_time,
                timestamp_unix=test_time.timestamp(),
                ping_ms=test_result.get("latency_avg") or 0,
                jitter_ms=test_result.get("jitter_ms") or 0,
                packet_loss_percent=test_result.get("packet_loss_percent") or 0,
                target_host=host,
                packet_count=self._config.ping_count,
                signal_at_test=signal_snapshot,
                speedtest_active=speedtest_active,
            )

            # Save to database
            await self._save_result(result)

            # Push to Loki for event visualization
            await self._push_to_loki(result, name)

            # Update stats
            self._update_stats(test_result)

            results.append({
                "target_host": host,
                "target_name": name,
                **test_result,
            })

            if test_result["status"] == "success":
                log.debug(
                    "network_quality_result",
                    target=name,
                    packet_loss=test_result["packet_loss_percent"],
                    jitter=test_result["jitter_ms"],
                )

        return results

    async def _save_result(self, result: NetworkQualityResult) -> None:
        """Save a network quality result to database."""
        async with self._db.connection() as db:
            await db.execute(
                """
                INSERT INTO network_quality (
                    timestamp, timestamp_unix, ping_ms, jitter_ms, packet_loss_percent,
                    target_host, packet_count, signal_snapshot, speedtest_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.timestamp.isoformat(),
                    result.timestamp_unix,
                    result.ping_ms,
                    result.jitter_ms,
                    result.packet_loss_percent,
                    result.target_host,
                    result.packet_count,
                    result.signal_at_test.model_dump_json() if result.signal_at_test else None,
                    1 if result.speedtest_active else 0,
                ),
            )
            await db.commit()

    def _update_stats(self, result: dict) -> None:
        """Update statistics after a test."""
        if result.get("status") == "success":
            self._stats["tests_completed"] += 1

            packet_loss = result.get("packet_loss_percent", 0)
            jitter = result.get("jitter_ms", 0)

            # Update running averages
            n = self._stats["tests_completed"]
            self._stats["avg_packet_loss"] = round(
                (self._stats["avg_packet_loss"] * (n - 1) + packet_loss) / n, 2
            )
            self._stats["avg_jitter"] = round(
                (self._stats["avg_jitter"] * (n - 1) + jitter) / n, 2
            )

            # Update max values
            if self._stats["max_packet_loss"] is None or packet_loss > self._stats["max_packet_loss"]:
                self._stats["max_packet_loss"] = packet_loss
            if self._stats["max_jitter"] is None or jitter > self._stats["max_jitter"]:
                self._stats["max_jitter"] = jitter

            # Check thresholds
            if (
                packet_loss > self._config.packet_loss_threshold_percent
                or jitter > self._config.jitter_threshold_ms
            ):
                self._stats["tests_above_threshold"] += 1
        else:
            self._stats["tests_failed"] += 1

    async def trigger_test_now(self, signal_snapshot: SignalData | None = None) -> list[dict]:
        """Manually trigger an immediate network quality test."""
        log.info("network_quality_manual_test_triggered")
        return await self.run_test(signal_snapshot)

    async def get_history(
        self,
        limit: int = 100,
        offset: int = 0,
        target_filter: str | None = None,
        status_filter: str | None = None,
        hour_filter: int | None = None,
    ) -> dict:
        """Get network quality test history."""
        async with self._db.connection() as db:
            where_clauses = []
            params: list[Any] = []

            if target_filter:
                where_clauses.append("target_host = ?")
                params.append(target_filter)

            if hour_filter is not None:
                where_clauses.append("CAST(strftime('%H', timestamp) AS INTEGER) = ?")
                params.append(hour_filter)

            where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

            # Get total count
            cursor = await db.execute(
                f"SELECT COUNT(*) FROM network_quality{where_sql}", params
            )
            row = await cursor.fetchone()
            total_count = row[0] if row else 0

            # Get results
            cursor = await db.execute(
                f"""
                SELECT * FROM network_quality
                {where_sql}
                ORDER BY timestamp_unix DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            )
            rows = await cursor.fetchall()

            results = []
            for row in rows:
                row_dict = dict(row)
                if row_dict.get("signal_snapshot"):
                    row_dict["signal_snapshot"] = json.loads(row_dict["signal_snapshot"])
                results.append(row_dict)

            return {
                "total_count": total_count,
                "count": len(results),
                "offset": offset,
                "limit": limit,
                "results": results,
            }

    async def get_hourly_stats(self, target_host: str | None = None) -> dict:
        """Get aggregated statistics by hour of day."""
        async with self._db.connection() as db:
            target_clause = "AND target_host = ?" if target_host else ""
            params = [target_host] if target_host else []

            cursor = await db.execute(
                f"""
                SELECT
                    CAST(strftime('%H', timestamp) AS INTEGER) as hour_of_day,
                    COUNT(*) as test_count,
                    AVG(packet_loss_percent) as avg_packet_loss,
                    MIN(packet_loss_percent) as min_packet_loss,
                    MAX(packet_loss_percent) as max_packet_loss,
                    AVG(jitter_ms) as avg_jitter,
                    MIN(jitter_ms) as min_jitter,
                    MAX(jitter_ms) as max_jitter,
                    AVG(ping_ms) as avg_latency
                FROM network_quality
                WHERE 1=1 {target_clause}
                GROUP BY hour_of_day
                ORDER BY hour_of_day
                """,
                params,
            )
            hourly_data = [dict(row) for row in await cursor.fetchall()]

            # Get overall stats
            cursor = await db.execute(
                f"""
                SELECT
                    COUNT(*) as total_tests,
                    AVG(packet_loss_percent) as overall_avg_packet_loss,
                    AVG(jitter_ms) as overall_avg_jitter
                FROM network_quality
                WHERE 1=1 {target_clause}
                """,
                params,
            )
            row = await cursor.fetchone()
            overall = dict(row) if row else {}

            # Find worst hours
            worst_packet_loss_hour = None
            worst_jitter_hour = None
            if hourly_data:
                worst_packet_loss_hour = max(
                    hourly_data, key=lambda x: x.get("avg_packet_loss") or 0
                )
                worst_jitter_hour = max(hourly_data, key=lambda x: x.get("avg_jitter") or 0)

            return {
                "hourly_breakdown": hourly_data,
                "overall": overall,
                "worst_packet_loss_hour": worst_packet_loss_hour,
                "worst_jitter_hour": worst_jitter_hour,
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
                    AVG(packet_loss_percent) as avg_packet_loss,
                    MIN(packet_loss_percent) as min_packet_loss,
                    MAX(packet_loss_percent) as max_packet_loss,
                    AVG(jitter_ms) as avg_jitter,
                    MIN(jitter_ms) as min_jitter,
                    MAX(jitter_ms) as max_jitter,
                    AVG(ping_ms) as avg_latency,
                    MIN(ping_ms) as min_latency,
                    MAX(ping_ms) as max_latency
                FROM network_quality
                """
            )
            row = await cursor.fetchone()
            overall = dict(row) if row else {}

            # Per-target stats
            cursor = await db.execute(
                """
                SELECT
                    target_host,
                    COUNT(*) as test_count,
                    AVG(packet_loss_percent) as avg_packet_loss,
                    MAX(packet_loss_percent) as max_packet_loss,
                    AVG(jitter_ms) as avg_jitter,
                    MAX(jitter_ms) as max_jitter,
                    AVG(ping_ms) as avg_latency
                FROM network_quality
                GROUP BY target_host
                """
            )
            by_target = [dict(row) for row in await cursor.fetchall()]

            # Calculate collection period
            collection_days = 0.0
            if overall.get("first_test") and overall.get("last_test"):
                collection_days = (overall["last_test"] - overall["first_test"]) / 86400

            # Quality assessment
            avg_loss = overall.get("avg_packet_loss") or 0
            avg_jitter = overall.get("avg_jitter") or 0

            quality_issues = []
            if avg_loss > 1:
                quality_issues.append(
                    f"Average packet loss of {avg_loss:.2f}% indicates unreliable connection"
                )
            if avg_jitter > 20:
                quality_issues.append(
                    f"Average jitter of {avg_jitter:.1f}ms may impact real-time applications"
                )
            if overall.get("max_packet_loss") and overall["max_packet_loss"] > 10:
                quality_issues.append(
                    f"Maximum packet loss of {overall['max_packet_loss']:.1f}% indicates severe connectivity issues"
                )

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
                "packet_loss_metrics": {
                    "average_percent": round(avg_loss, 2),
                    "min_percent": round(overall.get("min_packet_loss") or 0, 2),
                    "max_percent": round(overall.get("max_packet_loss") or 0, 2),
                },
                "jitter_metrics": {
                    "average_ms": round(avg_jitter, 2),
                    "min_ms": round(overall.get("min_jitter") or 0, 2),
                    "max_ms": round(overall.get("max_jitter") or 0, 2),
                },
                "latency_metrics": {
                    "average_ms": round(overall.get("avg_latency") or 0, 2),
                    "min_ms": round(overall.get("min_latency") or 0, 2),
                    "max_ms": round(overall.get("max_latency") or 0, 2),
                },
                "by_target": by_target,
                "quality_issues": quality_issues,
                "fcc_evidence_note": (
                    "High packet loss and jitter indicate unreliable network performance that affects "
                    "real-time applications like video calls, VoIP, and gaming."
                ),
            }

    async def get_latest_results(self) -> list[dict]:
        """Get the most recent test results (one per target)."""
        async with self._db.connection() as db:
            cursor = await db.execute(
                """
                SELECT * FROM network_quality
                WHERE id IN (
                    SELECT MAX(id) FROM network_quality
                    GROUP BY target_host
                )
                ORDER BY timestamp_unix DESC
                """
            )
            rows = await cursor.fetchall()

            results = []
            for row in rows:
                row_dict = dict(row)
                if row_dict.get("signal_snapshot"):
                    row_dict["signal_snapshot"] = json.loads(row_dict["signal_snapshot"])
                results.append(row_dict)

            return results


# Global service instance
_network_quality_service: NetworkQualityService | None = None


def get_network_quality_service() -> NetworkQualityService:
    """Get the global network quality service instance."""
    global _network_quality_service
    if _network_quality_service is None:
        _network_quality_service = NetworkQualityService()
    return _network_quality_service
