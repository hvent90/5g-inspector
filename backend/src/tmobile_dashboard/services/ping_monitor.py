"""Continuous ping monitoring service for detecting short outages."""

import asyncio
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from ..config import get_settings
from ..db import get_db
from ..db.repository import ContinuousPingRepository

log = structlog.get_logger()


class PingMonitorConfig:
    """Continuous ping monitor configuration."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self.enabled: bool = data.get("enabled", True)
        self.interval_seconds: int = data.get("interval_seconds", 30)
        self.ping_timeout_seconds: int = data.get("ping_timeout_seconds", 2)
        self.targets: list[str] = data.get(
            "targets",
            ["8.8.8.8", "1.1.1.1", "208.54.0.1"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "interval_seconds": self.interval_seconds,
            "ping_timeout_seconds": self.ping_timeout_seconds,
            "targets": self.targets,
        }


class ContinuousPingMonitor:
    """Lightweight ping monitor running at high frequency for outage detection.

    Runs every 30 seconds (by default) and stores results in both SQLite and Loki.
    This is separate from the full network quality monitor which runs every 5-15 minutes.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._config_path = Path(settings.database.path).parent / "ping_monitor_config.json"
        self._db = get_db()
        self._repo = ContinuousPingRepository()

        self._config = PingMonitorConfig()
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

        self._stats = {
            "pings_completed": 0,
            "pings_failed": 0,
            "monitor_started_at": None,
            "last_ping_time": None,
            "last_error": None,
        }

        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from file."""
        if self._config_path.exists():
            try:
                with open(self._config_path) as f:
                    data = json.load(f)
                self._config = PingMonitorConfig(data)
            except Exception as e:
                log.error("ping_monitor_config_load_failed", error=str(e))

    def _save_config(self) -> None:
        """Save configuration to file."""
        try:
            with open(self._config_path, "w") as f:
                json.dump(self._config.to_dict(), f, indent=2)
        except Exception as e:
            log.error("ping_monitor_config_save_failed", error=str(e))

    def _ping_target(self, host: str) -> dict:
        """Run a single ping to a target (blocking, run in executor)."""
        try:
            if sys.platform == "win32":
                cmd = ["ping", "-n", "1", "-w", str(self._config.ping_timeout_seconds * 1000), host]
            else:
                cmd = ["ping", "-c", "1", "-W", str(self._config.ping_timeout_seconds), host]

            start = time.time()
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._config.ping_timeout_seconds + 2,
            )
            elapsed_ms = (time.time() - start) * 1000

            if result.returncode == 0:
                # Parse latency from output
                if sys.platform == "win32":
                    match = re.search(r"time[=<](\d+(?:\.\d+)?)\s*ms", result.stdout, re.IGNORECASE)
                else:
                    match = re.search(r"time=(\d+(?:\.\d+)?)\s*ms", result.stdout)

                latency_ms = float(match.group(1)) if match else elapsed_ms
                return {"success": True, "latency_ms": latency_ms, "error": None}
            else:
                return {"success": False, "latency_ms": None, "error": "no_response"}

        except subprocess.TimeoutExpired:
            return {"success": False, "latency_ms": None, "error": "timeout"}
        except Exception as e:
            return {"success": False, "latency_ms": None, "error": str(e)}

    async def _push_to_loki(self, target: str, result: dict) -> None:
        """Push ping result to Loki."""
        try:
            settings = get_settings()
            if not settings.loki.enabled:
                return

            from .loki import get_loki_client
            loki = get_loki_client()

            event_data = {
                "event": "continuous_ping",
                "timestamp_unix": time.time(),
                "target": target,
                "success": result["success"],
                "latency_ms": result["latency_ms"],
                "error": result["error"],
            }

            labels = {
                "target": target,
                "success": "true" if result["success"] else "false",
            }

            await loki.push_event("continuous_ping", event_data, labels)

        except Exception as e:
            log.warning("loki_ping_push_error", error=str(e))

    async def _save_result(self, target: str, result: dict) -> None:
        """Save ping result to database."""
        try:
            await self._repo.insert(
                target_host=target,
                success=result["success"],
                latency_ms=result["latency_ms"],
                error_type=result["error"],
            )
        except Exception as e:
            log.warning("ping_result_save_error", error=str(e))

    async def ping_all_targets(self) -> list[dict]:
        """Ping all configured targets."""
        results = []
        loop = asyncio.get_event_loop()

        for target in self._config.targets:
            result = await loop.run_in_executor(None, self._ping_target, target)
            results.append({"target": target, **result})

            # Save to DB and Loki in parallel
            await asyncio.gather(
                self._save_result(target, result),
                self._push_to_loki(target, result),
                return_exceptions=True,
            )

            # Update stats
            if result["success"]:
                self._stats["pings_completed"] += 1
            else:
                self._stats["pings_failed"] += 1

        self._stats["last_ping_time"] = time.time()
        return results

    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        log.info("ping_monitor_loop_started", interval_seconds=self._config.interval_seconds)

        while not self._stop_event.is_set():
            if self._config.enabled:
                try:
                    await self.ping_all_targets()
                except Exception as e:
                    self._stats["last_error"] = str(e)
                    log.error("ping_monitor_error", error=str(e))

            # Wait for next interval or stop event
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._config.interval_seconds,
                )
                break  # Stop event was set
            except asyncio.TimeoutError:
                pass  # Continue to next iteration

        log.info("ping_monitor_loop_stopped")

    def is_running(self) -> bool:
        """Check if monitor is running."""
        return self._task is not None and not self._task.done()

    async def start(self) -> bool:
        """Start the ping monitor."""
        if self.is_running():
            return False

        self._stop_event.clear()
        self._config.enabled = True
        self._save_config()
        self._task = asyncio.create_task(self._monitor_loop())
        self._stats["monitor_started_at"] = datetime.now().isoformat()

        log.info("ping_monitor_started", interval_seconds=self._config.interval_seconds)
        return True

    async def stop(self) -> bool:
        """Stop the ping monitor."""
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

        log.info("ping_monitor_stopped")
        return True

    def get_config(self) -> dict:
        """Get current configuration."""
        return self._config.to_dict()

    def update_config(self, updates: dict) -> dict:
        """Update configuration."""
        current = self._config.to_dict()
        current.update(updates)
        self._config = PingMonitorConfig(current)
        self._save_config()
        return self.get_config()

    def get_stats(self) -> dict:
        """Get monitoring statistics."""
        return {
            **self._stats,
            "is_running": self.is_running(),
            "config": self.get_config(),
        }


# Global service instance
_ping_monitor: ContinuousPingMonitor | None = None


def get_ping_monitor() -> ContinuousPingMonitor:
    """Get the global ping monitor instance."""
    global _ping_monitor
    if _ping_monitor is None:
        _ping_monitor = ContinuousPingMonitor()
    return _ping_monitor
