"""Alert management service for T-Mobile Dashboard.

Provides:
- Signal threshold monitoring
- Alert triggering with cooldown
- Alert history and acknowledgment
- SSE (Server-Sent Events) for real-time notifications
- Optional webhook notifications
"""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import structlog
from pydantic import BaseModel, Field

from ..config import get_settings
from ..models import SignalData, AlertType, DisruptionSeverity

log = structlog.get_logger()


class AlertThreshold(BaseModel):
    """Configuration for a single alert threshold."""

    min: float | None = None
    max: float | None = None
    enabled: bool = True


class AlertConfig(BaseModel):
    """Alert system configuration."""

    enabled: bool = True
    thresholds: dict[str, AlertThreshold] = Field(default_factory=lambda: {
        "5g_sinr": AlertThreshold(min=5),
        "5g_rsrp": AlertThreshold(min=-100),
        "5g_rsrq": AlertThreshold(min=-15),
        "4g_sinr": AlertThreshold(min=5),
        "4g_rsrp": AlertThreshold(min=-100),
        "4g_rsrq": AlertThreshold(min=-15),
        "download_mbps": AlertThreshold(min=25),
        "upload_mbps": AlertThreshold(min=5),
        "ping_ms": AlertThreshold(max=100),
    })
    gateway_timeout_seconds: int = 10
    cooldown_seconds: int = 300
    webhook_url: str | None = None
    max_history: int = 1000


class Alert(BaseModel):
    """An alert event."""

    id: str
    alert_type: str
    severity: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    acknowledged: bool = False
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None
    cleared: bool = False
    cleared_at: datetime | None = None


class SSESubscriber:
    """A Server-Sent Events subscriber."""

    def __init__(self):
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        self.created_at: float = time.time()
        self.closed: bool = False

    async def get_message(self, timeout: float = 30.0) -> str | None:
        """Get next message from queue with timeout."""
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def put_message(self, message: str) -> bool:
        """Add message to queue. Returns False if queue is full."""
        if self.closed:
            return False
        try:
            self.queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            return False

    def close(self):
        """Mark subscriber as closed."""
        self.closed = True


class AlertingService:
    """Service for managing alerts with async support."""

    def __init__(self, config_file: Path | None = None, history_file: Path | None = None):
        settings = get_settings()
        self._config_file = config_file or settings.data_dir / "alert_config.json"
        self._history_file = history_file or settings.alert.history_file

        self._config: AlertConfig | None = None
        self._history: list[dict[str, Any]] = []
        self._active_alerts: dict[str, Alert] = {}
        self._cooldowns: dict[str, float] = {}
        self._subscribers: list[SSESubscriber] = []
        self._lock = asyncio.Lock()

        # Load initial state
        self._load_config()
        self._load_history()

        # Monitor state
        self._monitor_task: asyncio.Task | None = None
        self._running = False
        self._get_signal_data: Callable[[], SignalData | None] | None = None
        self._get_speedtest_result: Callable[[], dict | None] | None = None

    def _load_config(self) -> None:
        """Load alert configuration from file."""
        if self._config_file.exists():
            try:
                with open(self._config_file, "r") as f:
                    data = json.load(f)
                    self._config = AlertConfig(**data)
                    log.info("alert_config_loaded", path=str(self._config_file))
            except Exception as e:
                log.warning("alert_config_load_error", error=str(e))
                self._config = AlertConfig()
        else:
            self._config = AlertConfig()

    def _save_config(self) -> None:
        """Save alert configuration to file."""
        if self._config is None:
            return
        try:
            with open(self._config_file, "w") as f:
                json.dump(self._config.model_dump(), f, indent=2, default=str)
        except Exception as e:
            log.error("alert_config_save_error", error=str(e))

    def _load_history(self) -> None:
        """Load alert history from file."""
        if self._history_file.exists():
            try:
                with open(self._history_file, "r") as f:
                    self._history = json.load(f)
                    log.info("alert_history_loaded", count=len(self._history))
            except Exception as e:
                log.warning("alert_history_load_error", error=str(e))
                self._history = []
        else:
            self._history = []

    def _save_history(self) -> None:
        """Save alert history to file."""
        try:
            max_history = self._config.max_history if self._config else 1000
            if len(self._history) > max_history:
                self._history = self._history[-max_history:]

            with open(self._history_file, "w") as f:
                json.dump(self._history, f, default=str)
        except Exception as e:
            log.error("alert_history_save_error", error=str(e))

    @property
    def config(self) -> AlertConfig:
        """Get current alert configuration."""
        if self._config is None:
            self._config = AlertConfig()
        return self._config

    def update_config(self, updates: dict[str, Any]) -> AlertConfig:
        """Update alert configuration."""
        current = self.config.model_dump()
        current.update(updates)
        self._config = AlertConfig(**current)
        self._save_config()
        log.info("alert_config_updated")
        return self._config

    def _generate_alert_id(self) -> str:
        """Generate unique alert ID."""
        import os
        return f"alert_{int(time.time() * 1000)}_{os.urandom(4).hex()}"

    def _check_cooldown(self, alert_type: str) -> bool:
        """Check if alert type is in cooldown. Returns True if can fire."""
        cooldown_period = self.config.cooldown_seconds
        last_time = self._cooldowns.get(alert_type, 0)
        return (time.time() - last_time) >= cooldown_period

    def _set_cooldown(self, alert_type: str) -> None:
        """Set cooldown timestamp for alert type."""
        self._cooldowns[alert_type] = time.time()

    async def trigger_alert(
        self,
        alert_type: str,
        severity: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> Alert | None:
        """Trigger an alert if not in cooldown.

        Args:
            alert_type: Type of alert (e.g., '5g_sinr_low', 'gateway_disconnected')
            severity: 'info', 'warning', or 'critical'
            message: Human-readable alert message
            details: Additional context

        Returns:
            Alert if triggered, None if in cooldown or disabled
        """
        if not self.config.enabled:
            return None

        if not self._check_cooldown(alert_type):
            return None

        async with self._lock:
            alert = Alert(
                id=self._generate_alert_id(),
                alert_type=alert_type,
                severity=severity,
                message=message,
                details=details or {},
            )

            # Add to active alerts
            self._active_alerts[alert_type] = alert

            # Add to history
            self._history.append(alert.model_dump(mode="json"))
            self._save_history()

            # Set cooldown
            self._set_cooldown(alert_type)

            log.warning(
                "alert_triggered",
                alert_type=alert_type,
                severity=severity,
                message=message,
            )

            # Notify SSE subscribers
            await self._notify_subscribers(alert)

            # Send webhook if configured
            if self.config.webhook_url:
                asyncio.create_task(self._send_webhook(alert))

            return alert

    async def _notify_subscribers(self, alert: Alert | dict[str, Any]) -> None:
        """Send alert to all SSE subscribers."""
        if isinstance(alert, Alert):
            data = alert.model_dump(mode="json")
        else:
            data = alert

        message = f"data: {json.dumps(data, default=str)}\n\n"

        # Remove dead subscribers
        self._subscribers = [s for s in self._subscribers if not s.closed]

        for subscriber in self._subscribers:
            subscriber.put_message(message)

    async def _send_webhook(self, alert: Alert) -> None:
        """Send alert to webhook URL."""
        if not self.config.webhook_url:
            return

        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    self.config.webhook_url,
                    json=alert.model_dump(mode="json"),
                )
        except Exception as e:
            log.error("webhook_send_error", error=str(e))

    def subscribe_sse(self) -> SSESubscriber:
        """Create a new SSE subscriber."""
        subscriber = SSESubscriber()
        self._subscribers.append(subscriber)
        log.debug("sse_subscriber_added", total=len(self._subscribers))
        return subscriber

    def unsubscribe_sse(self, subscriber: SSESubscriber) -> None:
        """Remove an SSE subscriber."""
        subscriber.close()
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)
        log.debug("sse_subscriber_removed", total=len(self._subscribers))

    def get_active_alerts(self) -> list[Alert]:
        """Get all currently active (uncleared) alerts."""
        return [a for a in self._active_alerts.values() if not a.cleared]

    def get_history(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """Get alert history with pagination (newest first)."""
        history = list(reversed(self._history))
        return history[offset:offset + limit]

    async def acknowledge_alert(self, alert_id: str, by: str = "user") -> bool:
        """Mark an alert as acknowledged."""
        async with self._lock:
            for alert in self._active_alerts.values():
                if alert.id == alert_id:
                    alert.acknowledged = True
                    alert.acknowledged_at = datetime.utcnow()
                    alert.acknowledged_by = by
                    log.info("alert_acknowledged", alert_id=alert_id)
                    return True

            # Also check history
            for item in self._history:
                if item.get("id") == alert_id:
                    item["acknowledged"] = True
                    item["acknowledged_at"] = datetime.utcnow().isoformat()
                    item["acknowledged_by"] = by
                    self._save_history()
                    return True

        return False

    async def clear_alert(self, alert_id: str) -> bool:
        """Clear an alert (remove from active alerts)."""
        async with self._lock:
            for alert_type, alert in list(self._active_alerts.items()):
                if alert.id == alert_id:
                    alert.cleared = True
                    alert.cleared_at = datetime.utcnow()
                    del self._active_alerts[alert_type]

                    # Notify subscribers of clear
                    await self._notify_subscribers({
                        "type": "alert_cleared",
                        "alert_id": alert_id,
                        "timestamp": datetime.utcnow().isoformat(),
                    })

                    log.info("alert_cleared", alert_id=alert_id)
                    return True
        return False

    async def clear_all_alerts(self) -> int:
        """Clear all active alerts. Returns count of cleared alerts."""
        async with self._lock:
            count = len(self._active_alerts)
            now = datetime.utcnow()

            for alert in self._active_alerts.values():
                alert.cleared = True
                alert.cleared_at = now

            self._active_alerts.clear()

            if count > 0:
                await self._notify_subscribers({
                    "type": "all_alerts_cleared",
                    "count": count,
                    "timestamp": now.isoformat(),
                })

            log.info("all_alerts_cleared", count=count)
            return count

    def check_signal_thresholds(self, signal_data: SignalData) -> list[Alert]:
        """Check signal metrics against thresholds.

        Returns list of triggered alerts.
        """
        alerts = []
        thresholds = self.config.thresholds

        # Check 5G SINR
        if signal_data.nr.sinr is not None:
            threshold = thresholds.get("5g_sinr")
            if threshold and threshold.enabled and threshold.min is not None:
                if signal_data.nr.sinr < threshold.min:
                    asyncio.create_task(self.trigger_alert(
                        alert_type="5g_sinr_low",
                        severity="critical",
                        message=f"5G SINR below threshold: {signal_data.nr.sinr:.1f} dB (min: {threshold.min})",
                        details={
                            "metric": "5g_sinr",
                            "current_value": signal_data.nr.sinr,
                            "threshold": threshold.min,
                        }
                    ))

        # Check 5G RSRP
        if signal_data.nr.rsrp is not None:
            threshold = thresholds.get("5g_rsrp")
            if threshold and threshold.enabled and threshold.min is not None:
                if signal_data.nr.rsrp < threshold.min:
                    asyncio.create_task(self.trigger_alert(
                        alert_type="5g_rsrp_low",
                        severity="warning",
                        message=f"5G RSRP below threshold: {signal_data.nr.rsrp:.1f} dBm (min: {threshold.min})",
                        details={
                            "metric": "5g_rsrp",
                            "current_value": signal_data.nr.rsrp,
                            "threshold": threshold.min,
                        }
                    ))

        # Check 4G SINR
        if signal_data.lte.sinr is not None:
            threshold = thresholds.get("4g_sinr")
            if threshold and threshold.enabled and threshold.min is not None:
                if signal_data.lte.sinr < threshold.min:
                    asyncio.create_task(self.trigger_alert(
                        alert_type="4g_sinr_low",
                        severity="critical",
                        message=f"4G SINR below threshold: {signal_data.lte.sinr:.1f} dB (min: {threshold.min})",
                        details={
                            "metric": "4g_sinr",
                            "current_value": signal_data.lte.sinr,
                            "threshold": threshold.min,
                        }
                    ))

        # Check 4G RSRP
        if signal_data.lte.rsrp is not None:
            threshold = thresholds.get("4g_rsrp")
            if threshold and threshold.enabled and threshold.min is not None:
                if signal_data.lte.rsrp < threshold.min:
                    asyncio.create_task(self.trigger_alert(
                        alert_type="4g_rsrp_low",
                        severity="warning",
                        message=f"4G RSRP below threshold: {signal_data.lte.rsrp:.1f} dBm (min: {threshold.min})",
                        details={
                            "metric": "4g_rsrp",
                            "current_value": signal_data.lte.rsrp,
                            "threshold": threshold.min,
                        }
                    ))

        return alerts

    def check_speedtest_thresholds(self, speedtest: dict[str, Any]) -> list[Alert]:
        """Check speed test results against thresholds."""
        alerts = []
        thresholds = self.config.thresholds

        # Check download speed
        download = speedtest.get("download_mbps")
        if download is not None:
            threshold = thresholds.get("download_mbps")
            if threshold and threshold.enabled and threshold.min is not None:
                if download < threshold.min:
                    asyncio.create_task(self.trigger_alert(
                        alert_type="download_speed_low",
                        severity="warning",
                        message=f"Download speed below threshold: {download:.1f} Mbps (min: {threshold.min})",
                        details={
                            "metric": "download_mbps",
                            "current_value": download,
                            "threshold": threshold.min,
                        }
                    ))

        # Check upload speed
        upload = speedtest.get("upload_mbps")
        if upload is not None:
            threshold = thresholds.get("upload_mbps")
            if threshold and threshold.enabled and threshold.min is not None:
                if upload < threshold.min:
                    asyncio.create_task(self.trigger_alert(
                        alert_type="upload_speed_low",
                        severity="warning",
                        message=f"Upload speed below threshold: {upload:.1f} Mbps (min: {threshold.min})",
                        details={
                            "metric": "upload_mbps",
                            "current_value": upload,
                            "threshold": threshold.min,
                        }
                    ))

        # Check ping
        ping = speedtest.get("ping_ms")
        if ping is not None:
            threshold = thresholds.get("ping_ms")
            if threshold and threshold.enabled and threshold.max is not None:
                if ping > threshold.max:
                    asyncio.create_task(self.trigger_alert(
                        alert_type="ping_high",
                        severity="warning",
                        message=f"Ping above threshold: {ping:.1f} ms (max: {threshold.max})",
                        details={
                            "metric": "ping_ms",
                            "current_value": ping,
                            "threshold": threshold.max,
                        }
                    ))

        return alerts

    async def check_gateway_connection(
        self, signal_data: SignalData | None, last_success: float
    ) -> Alert | None:
        """Check if gateway connection is lost."""
        timeout = self.config.gateway_timeout_seconds
        now = time.time()

        if signal_data is None or (now - last_success) > timeout:
            return await self.trigger_alert(
                alert_type="gateway_disconnected",
                severity="critical",
                message=f"Gateway connection lost (no response for {now - last_success:.0f}s)",
                details={
                    "last_success": datetime.fromtimestamp(last_success).isoformat() if last_success else None,
                    "timeout_threshold": timeout,
                }
            )

        # If connection restored, clear the disconnected alert
        if "gateway_disconnected" in self._active_alerts:
            await self.clear_alert(self._active_alerts["gateway_disconnected"].id)

        return None

    def set_data_callbacks(
        self,
        get_signal: Callable[[], SignalData | None],
        get_speedtest: Callable[[], dict | None],
    ) -> None:
        """Set callback functions for getting current data."""
        self._get_signal_data = get_signal
        self._get_speedtest_result = get_speedtest

    async def start_monitor(self, signal_check_interval: float = 5.0) -> None:
        """Start background alert monitoring."""
        if self._running:
            return

        self._running = True
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(signal_check_interval)
        )
        log.info("alert_monitor_started", interval_seconds=signal_check_interval)

    async def stop_monitor(self) -> None:
        """Stop background alert monitoring."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        log.info("alert_monitor_stopped")

    async def _monitor_loop(self, interval: float) -> None:
        """Background monitoring loop."""
        while self._running:
            try:
                if self._get_signal_data:
                    signal_data = self._get_signal_data()
                    if signal_data:
                        self.check_signal_thresholds(signal_data)
            except Exception as e:
                log.error("alert_monitor_error", error=str(e))

            await asyncio.sleep(interval)


# Global service instance
_alerting_service: AlertingService | None = None


def get_alerting_service() -> AlertingService:
    """Get the global alerting service instance."""
    global _alerting_service
    if _alerting_service is None:
        _alerting_service = AlertingService()
    return _alerting_service
