"""Gateway polling service with circuit breaker."""

import asyncio
import time
from contextlib import suppress
from datetime import datetime
from enum import Enum
from dataclasses import dataclass
from typing import Any, Callable

import httpx
import structlog

from ..config import get_settings
from ..models import SignalData, SignalMetrics, ConnectionMode

log = structlog.get_logger()


@dataclass
class OutageEvent:
    """Data for a gateway outage event."""

    start_time: float
    end_time: float | None = None
    duration_seconds: float | None = None
    error_count: int = 0
    last_error: str | None = None
    resolved: bool = False


class CircuitState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, rejecting requests
    HALF_OPEN = "half_open"  # Testing if recovered


class CircuitBreaker:
    """Simple circuit breaker for gateway requests."""

    def __init__(self, failure_threshold: int = 3, recovery_timeout: int = 30):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time: float | None = None
        self.state = CircuitState.CLOSED

    def record_success(self) -> None:
        """Record a successful request."""
        self.failure_count = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record a failed request."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            log.warning(
                "circuit_breaker_opened",
                failures=self.failure_count,
                recovery_timeout=self.recovery_timeout,
            )

    def can_execute(self) -> bool:
        """Check if request can proceed."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed
            if self.last_failure_time and (
                time.time() - self.last_failure_time >= self.recovery_timeout
            ):
                self.state = CircuitState.HALF_OPEN
                log.info("circuit_breaker_half_open", testing_recovery=True)
                return True
            return False

        # HALF_OPEN - allow one request through
        return True


class GatewayService:
    """Gateway polling service with circuit breaker and caching."""

    def __init__(self):
        settings = get_settings()
        self.gateway_url = f"http://{settings.gateway.host}:{settings.gateway.port}/TMI/v1/gateway?get=all"
        self.timeout = settings.gateway.timeout_seconds
        self.poll_interval = settings.gateway.poll_interval_ms / 1000.0

        self.circuit_breaker = CircuitBreaker(
            failure_threshold=settings.gateway.failure_threshold,
            recovery_timeout=settings.gateway.recovery_timeout_seconds,
        )

        # Cache state
        self._current_data: SignalData | None = None
        self._raw_data: dict | None = None
        self._last_success: float = 0
        self._last_attempt: float = 0
        self._success_count: int = 0
        self._error_count: int = 0
        self._last_error: str | None = None

        # Background task control
        self._running = False
        self._task: asyncio.Task | None = None

        # Callbacks for signal data updates
        self._callbacks: list[Callable[[SignalData], Any]] = []

        # Outage tracking state
        self._in_outage: bool = False
        self._outage_start_time: float | None = None
        self._outage_error_count: int = 0

        # Callbacks for outage events
        self._outage_start_callbacks: list[Callable[[OutageEvent], Any]] = []
        self._outage_end_callbacks: list[Callable[[OutageEvent], Any]] = []

    def on_signal_update(self, callback: Callable[[SignalData], Any]) -> None:
        """Register a callback for signal data updates."""
        self._callbacks.append(callback)

    def on_outage_start(self, callback: Callable[[OutageEvent], Any]) -> None:
        """Register a callback for when a gateway outage starts."""
        self._outage_start_callbacks.append(callback)

    def on_outage_end(self, callback: Callable[[OutageEvent], Any]) -> None:
        """Register a callback for when a gateway outage ends."""
        self._outage_end_callbacks.append(callback)

    async def poll_once(self) -> SignalData | None:
        """Poll the gateway once."""
        self._last_attempt = time.time()

        if not self.circuit_breaker.can_execute():
            log.debug("gateway_poll_skipped", reason="circuit_open")
            return self._current_data

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(self.gateway_url)
                response.raise_for_status()
                raw_data = response.json()

            # Parse signal data
            signal_data = self._parse_signal_data(raw_data)
            self._raw_data = raw_data
            self._current_data = signal_data
            self._last_success = time.time()
            self._success_count += 1

            self.circuit_breaker.record_success()

            # Detect outage recovery
            if self._in_outage:
                end_time = time.time()
                duration = end_time - self._outage_start_time if self._outage_start_time else 0

                outage_event = OutageEvent(
                    start_time=self._outage_start_time or end_time,
                    end_time=end_time,
                    duration_seconds=duration,
                    error_count=self._outage_error_count,
                    last_error=self._last_error,
                    resolved=True,
                )

                log.info(
                    "gateway_outage_resolved",
                    start_time=self._outage_start_time,
                    end_time=end_time,
                    duration_seconds=duration,
                    error_count=self._outage_error_count,
                )

                # Notify outage end callbacks
                for callback in self._outage_end_callbacks:
                    try:
                        result = callback(outage_event)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        log.error("outage_end_callback_error", error=str(e))

                # Reset outage state
                self._in_outage = False
                self._outage_start_time = None
                self._outage_error_count = 0

            # Notify callbacks
            for callback in self._callbacks:
                try:
                    result = callback(signal_data)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    log.error("gateway_callback_error", error=str(e))

            return signal_data

        except httpx.TimeoutException:
            await self._handle_error("Gateway request timed out")
            return self._current_data

        except httpx.HTTPStatusError as e:
            await self._handle_error(f"Gateway HTTP error: {e.response.status_code}")
            return self._current_data

        except Exception as e:
            await self._handle_error(f"Gateway error: {e}")
            return self._current_data

    async def _handle_error(self, message: str) -> None:
        """Handle a poll error and detect outage start."""
        self._error_count += 1
        self._last_error = message
        self._outage_error_count += 1

        was_closed = self.circuit_breaker.state == CircuitState.CLOSED
        self.circuit_breaker.record_failure()
        log.warning("gateway_poll_error", error=message, error_count=self._error_count)

        # Detect outage start: circuit just opened
        if was_closed and self.circuit_breaker.state == CircuitState.OPEN and not self._in_outage:
            self._in_outage = True
            self._outage_start_time = time.time()
            self._outage_error_count = self.circuit_breaker.failure_count

            outage_event = OutageEvent(
                start_time=self._outage_start_time,
                error_count=self._outage_error_count,
                last_error=message,
            )

            log.warning(
                "gateway_outage_started",
                start_time=self._outage_start_time,
                error_count=self._outage_error_count,
                last_error=message,
            )

            # Notify outage start callbacks
            for callback in self._outage_start_callbacks:
                try:
                    result = callback(outage_event)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    log.error("outage_start_callback_error", error=str(e))

    def _parse_signal_data(self, raw: dict) -> SignalData:
        """Parse raw gateway response into SignalData model."""
        signal = raw.get("signal", {})
        device = raw.get("device", {})

        # Parse 5G NR metrics
        nr_data = signal.get("5g", {})
        nr = SignalMetrics(
            sinr=self._safe_float(nr_data.get("sinr")),
            rsrp=self._safe_float(nr_data.get("rsrp")),
            rsrq=self._safe_float(nr_data.get("rsrq")),
            rssi=self._safe_float(nr_data.get("rssi")),
            bands=nr_data.get("bands", []),
            tower_id=nr_data.get("gNBID"),
            cell_id=nr_data.get("cid"),
        )

        # Parse 4G LTE metrics
        lte_data = signal.get("4g", {})
        lte = SignalMetrics(
            sinr=self._safe_float(lte_data.get("sinr")),
            rsrp=self._safe_float(lte_data.get("rsrp")),
            rsrq=self._safe_float(lte_data.get("rsrq")),
            rssi=self._safe_float(lte_data.get("rssi")),
            bands=lte_data.get("bands", []),
            tower_id=lte_data.get("eNBID"),
            cell_id=lte_data.get("cid"),
        )

        # Determine connection mode
        registration = device.get("connectionStatus", "")
        if "5G-SA" in registration or registration == "SA":
            mode = ConnectionMode.SA
        elif "5G-NSA" in registration or registration == "NSA":
            mode = ConnectionMode.NSA
        elif "LTE" in registration:
            mode = ConnectionMode.LTE
        else:
            mode = ConnectionMode.NO_SIGNAL

        return SignalData(
            timestamp=datetime.utcnow(),
            timestamp_unix=time.time(),
            nr=nr,
            lte=lte,
            registration_status=registration,
            connection_mode=mode,
            device_uptime=device.get("deviceUptime"),
            raw=raw,
        )

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        """Safely convert a value to float."""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    async def start_polling(self) -> None:
        """Start the background polling task."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        log.info("gateway_polling_started", interval_ms=int(self.poll_interval * 1000))

    async def stop_polling(self) -> None:
        """Stop the background polling task gracefully."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        log.info("gateway_polling_stopped")

    async def _poll_loop(self) -> None:
        """Background polling loop."""
        while self._running:
            await self.poll_once()
            await asyncio.sleep(self.poll_interval)

    @property
    def current_data(self) -> SignalData | None:
        """Get the current cached signal data."""
        return self._current_data

    @property
    def raw_data(self) -> dict | None:
        """Get the raw gateway response."""
        return self._raw_data

    def get_stats(self) -> dict:
        """Get polling statistics."""
        return {
            "last_success": self._last_success,
            "last_attempt": self._last_attempt,
            "success_count": self._success_count,
            "error_count": self._error_count,
            "last_error": self._last_error,
            "circuit_state": self.circuit_breaker.state.value,
            "is_running": self._running,
        }
