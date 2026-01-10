"""Alert management service."""

import asyncio
import json
import os
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import structlog

from ..config import get_settings
from ..models import Alert, AlertConfig, AlertType, DisruptionSeverity

log = structlog.get_logger()


class AlertService:
    """Service for managing alerts with SSE support."""

    def __init__(self) -> None:
        settings = get_settings()
        self._config_path = Path(settings.database.path).parent / "alert_config.json"
        self._history_path = Path(settings.database.path).parent / "alert_history.json"

        self._config: AlertConfig | None = None
        self._history: list[dict] = []
        self._active_alerts: dict[str, Alert] = {}
        self._cooldowns: dict[str, float] = {}
        self._sse_subscribers: list[dict] = []

        # Callbacks for data access
        self._get_signal_data: Callable | None = None
        self._get_speedtest_results: Callable | None = None

        # Load persisted data
        self._load_config()
        self._load_history()

    def _load_config(self) -> None:
        """Load alert configuration from file."""
        if self._config_path.exists():
            try:
                with open(self._config_path) as f:
                    data = json.load(f)
                self._config = AlertConfig(**data)
            except Exception as e:
                log.error("alert_config_load_failed", error=str(e))
                self._config = AlertConfig()
        else:
            self._config = AlertConfig()

    def _save_config(self) -> None:
        """Save alert configuration to file."""
        try:
            with open(self._config_path, "w") as f:
                json.dump(self._config.model_dump(), f, indent=2)
        except Exception as e:
            log.error("alert_config_save_failed", error=str(e))

    def _load_history(self) -> None:
        """Load alert history from file."""
        if self._history_path.exists():
            try:
                with open(self._history_path) as f:
                    self._history = json.load(f)
            except Exception as e:
                log.error("alert_history_load_failed", error=str(e))
                self._history = []

    def _save_history(self) -> None:
        """Save alert history to file."""
        try:
            # Limit history size
            max_history = 1000
            if len(self._history) > max_history:
                self._history = self._history[-max_history:]

            with open(self._history_path, "w") as f:
                json.dump(self._history, f)
        except Exception as e:
            log.error("alert_history_save_failed", error=str(e))

    def get_config(self) -> AlertConfig:
        """Get current alert configuration."""
        if self._config is None:
            self._config = AlertConfig()
        return self._config

    def update_config(self, config: AlertConfig) -> AlertConfig:
        """Update alert configuration."""
        self._config = config
        self._save_config()
        return self._config

    def set_data_callbacks(
        self,
        get_signal_data: Callable | None = None,
        get_speedtest_results: Callable | None = None,
    ) -> None:
        """Set callback functions for getting current data."""
        self._get_signal_data = get_signal_data
        self._get_speedtest_results = get_speedtest_results

    def _check_cooldown(self, alert_type: str) -> bool:
        """Check if alert type is in cooldown. Returns True if alert can fire."""
        cooldown_seconds = (self._config.cooldown_minutes if self._config else 5) * 60
        last_time = self._cooldowns.get(alert_type, 0)
        return (time.time() - last_time) >= cooldown_seconds

    def _set_cooldown(self, alert_type: str) -> None:
        """Set cooldown timestamp for alert type."""
        self._cooldowns[alert_type] = time.time()

    def trigger_alert(
        self,
        alert_type: AlertType | str,
        severity: DisruptionSeverity,
        title: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> Alert | None:
        """Trigger an alert if not in cooldown.

        Returns the Alert if triggered, None if in cooldown or disabled.
        """
        if self._config and not self._config.enabled:
            return None

        type_str = alert_type.value if isinstance(alert_type, AlertType) else alert_type

        if not self._check_cooldown(type_str):
            return None

        alert = Alert(
            alert_type=alert_type if isinstance(alert_type, AlertType) else AlertType.SIGNAL_DROP,
            severity=severity,
            title=title,
            message=message,
            data=data or {},
        )

        # Add to active alerts
        self._active_alerts[type_str] = alert

        # Add to history
        self._history.append(alert.model_dump(mode="json"))
        self._save_history()

        # Set cooldown
        self._set_cooldown(type_str)

        # Notify SSE subscribers
        self._notify_sse(alert.model_dump(mode="json"))

        log.info("alert_triggered", alert_type=type_str, severity=severity.value)

        return alert

    def _notify_sse(self, data: dict) -> None:
        """Send data to all SSE subscribers."""
        message = f"data: {json.dumps(data)}\n\n"

        # Clean up dead subscribers and send message
        active_subscribers = []
        for subscriber in self._sse_subscribers:
            if not subscriber.get("closed", False):
                try:
                    subscriber["queue"].append(message)
                    active_subscribers.append(subscriber)
                except Exception:
                    subscriber["closed"] = True

        self._sse_subscribers = active_subscribers

    def subscribe_sse(self) -> dict:
        """Create a new SSE subscriber. Returns subscriber dict with queue."""
        subscriber = {
            "queue": deque(maxlen=100),
            "closed": False,
            "created": time.time(),
        }
        self._sse_subscribers.append(subscriber)
        return subscriber

    def unsubscribe_sse(self, subscriber: dict) -> None:
        """Remove SSE subscriber."""
        subscriber["closed"] = True
        if subscriber in self._sse_subscribers:
            self._sse_subscribers.remove(subscriber)

    def get_active_alerts(self) -> list[dict]:
        """Get all currently active (uncleared) alerts."""
        return [
            alert.model_dump(mode="json")
            for alert in self._active_alerts.values()
            if not alert.resolved
        ]

    def get_history(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """Get alert history with pagination."""
        # Return newest first
        history = list(reversed(self._history))
        return history[offset:offset + limit]

    def acknowledge_alert(self, alert_id: str) -> bool:
        """Mark an alert as acknowledged."""
        for alert in self._active_alerts.values():
            if alert.id == alert_id:
                alert.acknowledged = True
                alert.acknowledged_at = datetime.utcnow()
                return True

        # Also update in history
        for item in self._history:
            if item.get("id") == alert_id:
                item["acknowledged"] = True
                item["acknowledged_at"] = datetime.utcnow().isoformat()
                self._save_history()
                return True

        return False

    def clear_alert(self, alert_id: str) -> bool:
        """Clear a specific alert."""
        for alert_type, alert in list(self._active_alerts.items()):
            if alert.id == alert_id:
                alert.resolved = True
                alert.resolved_at = datetime.utcnow()
                del self._active_alerts[alert_type]

                # Notify SSE subscribers
                self._notify_sse({
                    "type": "alert_cleared",
                    "alert_id": alert_id,
                    "timestamp": datetime.utcnow().isoformat(),
                })
                return True

        return False

    def clear_all_alerts(self) -> int:
        """Clear all active alerts. Returns count of cleared alerts."""
        count = len(self._active_alerts)
        now = datetime.utcnow()

        for alert in self._active_alerts.values():
            alert.resolved = True
            alert.resolved_at = now

        self._active_alerts.clear()

        if count > 0:
            self._notify_sse({
                "type": "all_alerts_cleared",
                "count": count,
                "timestamp": now.isoformat(),
            })

        return count

    def trigger_test_alert(self) -> Alert | None:
        """Trigger a test alert."""
        return self.trigger_alert(
            alert_type=AlertType.SIGNAL_DROP,
            severity=DisruptionSeverity.INFO,
            title="Test Alert",
            message="This is a test alert",
            data={"test": True},
        )


# Global service instance
_alert_service: AlertService | None = None


def get_alert_service() -> AlertService:
    """Get the global alert service instance."""
    global _alert_service
    if _alert_service is None:
        _alert_service = AlertService()
    return _alert_service
