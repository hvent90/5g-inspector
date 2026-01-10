"""Speedtest service for running and managing speed tests."""

import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from ..config import get_settings
from ..models import NetworkContext, SignalData, SpeedtestResult, SpeedtestStatus
from .speedtest_tools import (
    SpeedtestTool,
    SpeedtestToolResult,
    detect_available_tools,
    get_tool,
    TOOL_REGISTRY,
)

log = structlog.get_logger()


def _ping_latency_sync(host: str = "8.8.8.8", count: int = 3, timeout: float = 5.0) -> float | None:
    """Measure ping latency to a host (synchronous, runs in thread pool).

    Returns average latency in ms, or None if ping fails.
    """
    try:
        # Platform-specific ping command
        if sys.platform == "win32":
            cmd = ["ping", "-n", str(count), "-w", str(int(timeout * 1000)), host]
        else:
            cmd = ["ping", "-c", str(count), "-W", str(int(timeout)), host]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)

        if result.returncode != 0:
            return None

        # Parse average latency from output
        output = result.stdout.lower()
        if sys.platform == "win32":
            # Windows: "Average = 15ms"
            if "average" in output:
                for line in output.split("\n"):
                    if "average" in line:
                        # Extract number before "ms"
                        parts = line.split("=")
                        if len(parts) >= 2:
                            avg_part = parts[-1].strip().replace("ms", "").strip()
                            return float(avg_part)
        else:
            # Unix: "rtt min/avg/max/mdev = 10.123/15.456/20.789/3.456 ms"
            if "avg" in output or "rtt" in output:
                for line in output.split("\n"):
                    if "avg" in line or "rtt" in line:
                        # Extract avg from "min/avg/max/mdev"
                        if "/" in line:
                            parts = line.split("=")
                            if len(parts) >= 2:
                                stats = parts[-1].strip().split("/")
                                if len(stats) >= 2:
                                    return float(stats[1])
        return None
    except Exception:
        return None


def infer_network_context(
    current_hour: int,
    pre_test_latency_ms: float | None,
    baseline_latency_ms: float,
    idle_hours: list[int],
    light_multiplier: float = 1.5,
    busy_multiplier: float = 2.5,
) -> NetworkContext:
    """Infer network context from time and latency measurements.

    Args:
        current_hour: Hour of day (0-23)
        pre_test_latency_ms: Measured latency before test, or None if not measured
        baseline_latency_ms: Expected latency when network is idle
        idle_hours: List of hours considered baseline (e.g., [2, 3, 4, 5])
        light_multiplier: Latency ratio threshold for "light" usage
        busy_multiplier: Latency ratio threshold for "busy" usage

    Returns:
        NetworkContext enum value
    """
    # Time-based: tests during configured idle hours are always baseline
    if current_hour in idle_hours:
        return NetworkContext.BASELINE

    # If no latency probe, we can't infer from latency
    if pre_test_latency_ms is None:
        return NetworkContext.UNKNOWN

    # Latency-based inference
    latency_ratio = pre_test_latency_ms / baseline_latency_ms

    if latency_ratio < light_multiplier:
        return NetworkContext.IDLE
    elif latency_ratio < busy_multiplier:
        return NetworkContext.LIGHT
    else:
        return NetworkContext.BUSY


class SpeedtestService:
    """Service for running speed tests and managing history."""

    def __init__(self, history_file: Path | None = None):
        self._settings = get_settings()
        self._history_file = history_file or self._settings.speedtest_history_file
        self._running = False
        self._lock = asyncio.Lock()
        self._results: list[dict[str, Any]] = []
        self._max_results = 100
        self._last_result: SpeedtestResult | None = None
        self._last_run_time: datetime | None = None

        # Detect available speedtest tools
        self._available_tools: list[str] = detect_available_tools()
        log.info("speedtest_tools_detected", tools=self._available_tools)

        # Load history on init
        self._load_history()

    def get_available_tools(self) -> list[str]:
        """Get list of available speedtest tools."""
        return self._available_tools

    def get_tool_info(self) -> dict[str, Any]:
        """Get information about available tools and configuration."""
        return {
            "available": self._available_tools,
            "all_known": list(TOOL_REGISTRY.keys()),
            "preferred_order": self._settings.speedtest.preferred_tools,
            "configured_server_id": self._settings.speedtest.ookla_server_id,
            "timeout_seconds": self._settings.speedtest.timeout_seconds,
        }

    def _select_tool(self, preferred: str | None = None) -> SpeedtestTool | None:
        """Select best available tool based on preference."""
        # Use explicit preference if provided and available
        if preferred and preferred in self._available_tools:
            return get_tool(preferred)

        # Use configured preference order
        for tool_name in self._settings.speedtest.preferred_tools:
            if tool_name in self._available_tools:
                return get_tool(tool_name)

        # Fallback to first available
        if self._available_tools:
            return get_tool(self._available_tools[0])

        return None

    def _load_history(self) -> None:
        """Load speedtest history from file."""
        try:
            if self._history_file.exists():
                with open(self._history_file, "r") as f:
                    self._results = json.load(f)
                log.info("speedtest_history_loaded", count=len(self._results))
        except Exception as e:
            log.warning("speedtest_history_load_error", error=str(e))
            self._results = []

    def _save_history(self) -> None:
        """Save speedtest history to file."""
        try:
            with open(self._history_file, "w") as f:
                json.dump(self._results, f, indent=2)
        except Exception as e:
            log.error("speedtest_history_save_error", error=str(e))

    async def _push_to_loki(
        self, result: SpeedtestResult, signal_snapshot: SignalData | None
    ) -> None:
        """Push speedtest result to Loki for discrete event visualization.

        This allows Grafana to show one data point per actual test instead
        of continuous gauge scrapes.
        """
        if not self._settings.loki.enabled:
            return

        try:
            from .loki import get_loki_client

            loki = get_loki_client()

            # Build event data with all relevant fields
            event_data: dict[str, Any] = {
                "event": "speedtest_result",
                "timestamp_unix": result.timestamp_unix,
                "download_mbps": result.download_mbps,
                "upload_mbps": result.upload_mbps,
                "ping_ms": result.ping_ms,
                "jitter_ms": result.jitter_ms,
                "server_name": result.server_name,
                "server_id": result.server_id,
                "tool": result.tool,
                "triggered_by": result.triggered_by,
                "status": result.status,
                "network_context": result.network_context.value if result.network_context else "unknown",
            }

            # Add signal snapshot for correlation
            if signal_snapshot:
                event_data["signal"] = {
                    "nr_sinr": signal_snapshot.nr.sinr,
                    "nr_rsrp": signal_snapshot.nr.rsrp,
                    "lte_sinr": signal_snapshot.lte.sinr,
                    "lte_rsrp": signal_snapshot.lte.rsrp,
                }

            # Labels for stream filtering (low cardinality only)
            labels = {
                "status": result.status,
                "network_context": result.network_context.value if result.network_context else "unknown",
                "tool": result.tool or "unknown",
            }

            await loki.push_event("speedtest", event_data, labels)

        except Exception as e:
            # Don't fail the speedtest if Loki push fails
            log.warning("loki_speedtest_push_error", error=str(e))

    @property
    def is_running(self) -> bool:
        """Check if a speed test is currently running."""
        return self._running

    def get_status(self) -> SpeedtestStatus:
        """Get current speedtest status."""
        return SpeedtestStatus(
            running=self._running,
            last_result=self._last_result,
            last_run_time=self._last_run_time,
        )

    def get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get speedtest history, most recent first."""
        return list(reversed(self._results[-limit:]))

    async def run_speedtest(
        self,
        signal_snapshot: SignalData | None = None,
        triggered_by: str = "manual",
        # Tool selection
        tool: str | None = None,  # Specific tool to use (None = auto-select)
        server_id: int | None = None,  # Target specific Ookla server
        # Context detection settings (optional - uses defaults if not provided)
        context_override: NetworkContext | None = None,
        enable_latency_probe: bool = True,
        idle_hours: list[int] | None = None,
        baseline_latency_ms: float = 20.0,
        light_latency_multiplier: float = 1.5,
        busy_latency_multiplier: float = 2.5,
    ) -> SpeedtestResult:
        """Run a speed test with network context detection.

        Args:
            signal_snapshot: Current signal data to correlate with test
            triggered_by: Who triggered the test (manual, scheduled, api)
            tool: Specific tool to use (None = auto-select from preferred_tools)
            server_id: Target specific Ookla server (None = auto-select)
            context_override: Manual context override (skips auto-detection)
            enable_latency_probe: Run latency probe before test to detect network usage
            idle_hours: Hours considered baseline (default: [2, 3, 4, 5])
            baseline_latency_ms: Expected latency when network is idle
            light_latency_multiplier: Latency ratio for "light" usage
            busy_latency_multiplier: Latency ratio for "busy" usage

        Returns:
            SpeedtestResult with test results, signal snapshot, and network context
        """
        async with self._lock:
            if self._running:
                return SpeedtestResult(
                    download_mbps=0,
                    upload_mbps=0,
                    ping_ms=0,
                    status="busy",
                    error_message="Speed test already running",
                    triggered_by=triggered_by,
                )
            self._running = True

        if idle_hours is None:
            idle_hours = [2, 3, 4, 5]

        test_start = time.time()
        current_hour = datetime.now().hour

        # Run pre-test latency probe to detect network usage
        pre_test_latency: float | None = None
        network_context = NetworkContext.UNKNOWN

        if context_override is not None:
            network_context = context_override
            log.info("speedtest_context_override", context=network_context.value)
        elif enable_latency_probe:
            pre_test_latency = await asyncio.get_event_loop().run_in_executor(
                None, _ping_latency_sync
            )
            network_context = infer_network_context(
                current_hour=current_hour,
                pre_test_latency_ms=pre_test_latency,
                baseline_latency_ms=baseline_latency_ms,
                idle_hours=idle_hours,
                light_multiplier=light_latency_multiplier,
                busy_multiplier=busy_latency_multiplier,
            )
            log.info(
                "speedtest_context_detected",
                context=network_context.value,
                pre_test_latency_ms=pre_test_latency,
                hour=current_hour,
            )
        elif current_hour in idle_hours:
            network_context = NetworkContext.BASELINE

        # Select tool
        selected_tool = self._select_tool(tool)
        if not selected_tool:
            async with self._lock:
                self._running = False
            return SpeedtestResult(
                download_mbps=0,
                upload_mbps=0,
                ping_ms=0,
                status="error",
                error_message="No speedtest tool available. Install speedtest-cli, Ookla CLI, or fastcli.",
                triggered_by=triggered_by,
                network_context=network_context,
                pre_test_latency_ms=pre_test_latency,
            )

        # Use configured server_id if not explicitly provided
        effective_server_id = server_id or self._settings.speedtest.ookla_server_id

        log.info(
            "speedtest_starting",
            triggered_by=triggered_by,
            context=network_context.value,
            tool=selected_tool.name,
            server_id=effective_server_id,
        )

        try:
            # Run speedtest in thread pool to not block
            result: SpeedtestToolResult = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: selected_tool.run(
                    timeout=self._settings.speedtest.timeout_seconds,
                    server_id=effective_server_id,
                ),
            )

            if result.status == "success":
                speedtest_result = SpeedtestResult(
                    timestamp=datetime.utcnow(),
                    timestamp_unix=time.time(),
                    download_mbps=result.download_mbps,
                    upload_mbps=result.upload_mbps,
                    ping_ms=result.ping_ms,
                    jitter_ms=result.jitter_ms,
                    server_name=result.server_name,
                    server_location=result.server_location,
                    server_host=result.server_host,
                    server_id=result.server_id,
                    client_ip=result.client_ip,
                    isp=result.isp,
                    tool=result.tool,
                    result_url=result.result_url,
                    signal_at_test=signal_snapshot,
                    network_context=network_context,
                    pre_test_latency_ms=pre_test_latency,
                    status="success",
                    triggered_by=triggered_by,
                )
            else:
                speedtest_result = SpeedtestResult(
                    download_mbps=0,
                    upload_mbps=0,
                    ping_ms=0,
                    tool=result.tool,
                    network_context=network_context,
                    pre_test_latency_ms=pre_test_latency,
                    status=result.status,
                    error_message=result.error_message,
                    triggered_by=triggered_by,
                )

            # Store result
            result_dict = {
                "timestamp": speedtest_result.timestamp.isoformat(),
                "timestamp_unix": speedtest_result.timestamp_unix,
                "download_mbps": speedtest_result.download_mbps,
                "upload_mbps": speedtest_result.upload_mbps,
                "ping_ms": speedtest_result.ping_ms,
                "jitter_ms": speedtest_result.jitter_ms,
                "server_name": speedtest_result.server_name,
                "server_location": speedtest_result.server_location,
                "server_host": speedtest_result.server_host,
                "server_id": speedtest_result.server_id,
                "client_ip": speedtest_result.client_ip,
                "isp": speedtest_result.isp,
                "tool": speedtest_result.tool,
                "result_url": speedtest_result.result_url,
                "status": speedtest_result.status,
                "error_message": speedtest_result.error_message,
                "triggered_by": speedtest_result.triggered_by,
                "duration_seconds": round(time.time() - test_start, 1),
                "network_context": speedtest_result.network_context.value,
                "pre_test_latency_ms": speedtest_result.pre_test_latency_ms,
                "signal_at_test": (
                    signal_snapshot.model_dump(mode="json") if signal_snapshot else None
                ),
            }

            self._results.append(result_dict)
            if len(self._results) > self._max_results:
                self._results.pop(0)

            self._save_history()
            self._last_result = speedtest_result
            self._last_run_time = speedtest_result.timestamp

            log.info(
                "speedtest_complete",
                download_mbps=speedtest_result.download_mbps,
                upload_mbps=speedtest_result.upload_mbps,
                ping_ms=speedtest_result.ping_ms,
                tool=speedtest_result.tool,
                status=speedtest_result.status,
            )

            # Push event to Loki for discrete time-series visualization
            await self._push_to_loki(speedtest_result, signal_snapshot)

            return speedtest_result

        except Exception as e:
            log.error("speedtest_error", error=str(e))
            return SpeedtestResult(
                download_mbps=0,
                upload_mbps=0,
                ping_ms=0,
                status="error",
                error_message=str(e),
                triggered_by=triggered_by,
                network_context=network_context,
                pre_test_latency_ms=pre_test_latency,
            )

        finally:
            async with self._lock:
                self._running = False

