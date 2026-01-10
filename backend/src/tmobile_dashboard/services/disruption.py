"""Disruption detection and analysis service."""

import time
from typing import Any

import structlog

from ..db import get_db
from ..db.repository import DisruptionRepository
from ..models import DisruptionEvent, DisruptionSeverity

log = structlog.get_logger()


class DisruptionService:
    """Service for disruption detection and analysis."""

    def __init__(self) -> None:
        self.repo = DisruptionRepository()
        self._cooldowns: dict[str, float] = {}
        self._cooldown_seconds = 60  # 1 minute cooldown between same type events

    async def detect_disruption(
        self,
        current_data: dict[str, Any],
        previous_data: dict[str, Any] | None,
    ) -> list[DisruptionEvent]:
        """Detect disruption events by comparing current vs previous data.

        Returns a list of detected disruption events.
        """
        events = []

        if previous_data is None:
            return events

        # Check for 5G signal drop
        nr_sinr_curr = current_data.get("nr_sinr")
        nr_sinr_prev = previous_data.get("nr_sinr")

        if nr_sinr_curr is not None and nr_sinr_prev is not None:
            drop = nr_sinr_prev - nr_sinr_curr
            if drop >= 10:
                event = await self._maybe_create_event(
                    event_type="signal_drop_5g",
                    severity=DisruptionSeverity.CRITICAL if drop >= 20 else DisruptionSeverity.WARNING,
                    description=f"5G SINR dropped by {drop:.1f} dB",
                    before_state={"nr_sinr": nr_sinr_prev},
                    after_state={"nr_sinr": nr_sinr_curr},
                )
                if event:
                    events.append(event)

        # Check for 4G signal drop
        lte_sinr_curr = current_data.get("lte_sinr")
        lte_sinr_prev = previous_data.get("lte_sinr")

        if lte_sinr_curr is not None and lte_sinr_prev is not None:
            drop = lte_sinr_prev - lte_sinr_curr
            if drop >= 10:
                event = await self._maybe_create_event(
                    event_type="signal_drop_4g",
                    severity=DisruptionSeverity.WARNING,
                    description=f"4G SINR dropped by {drop:.1f} dB",
                    before_state={"lte_sinr": lte_sinr_prev},
                    after_state={"lte_sinr": lte_sinr_curr},
                )
                if event:
                    events.append(event)

        # Check for tower change (5G)
        nr_gnb_curr = current_data.get("nr_gnb_id")
        nr_gnb_prev = previous_data.get("nr_gnb_id")

        if nr_gnb_curr and nr_gnb_prev and nr_gnb_curr != nr_gnb_prev:
            event = await self._maybe_create_event(
                event_type="tower_change_5g",
                severity=DisruptionSeverity.INFO,
                description=f"5G tower changed from {nr_gnb_prev} to {nr_gnb_curr}",
                before_state={"nr_gnb_id": nr_gnb_prev, "nr_cid": previous_data.get("nr_cid")},
                after_state={"nr_gnb_id": nr_gnb_curr, "nr_cid": current_data.get("nr_cid")},
            )
            if event:
                events.append(event)

        # Check for tower change (4G)
        lte_enb_curr = current_data.get("lte_enb_id")
        lte_enb_prev = previous_data.get("lte_enb_id")

        if lte_enb_curr and lte_enb_prev and lte_enb_curr != lte_enb_prev:
            event = await self._maybe_create_event(
                event_type="tower_change_4g",
                severity=DisruptionSeverity.INFO,
                description=f"4G tower changed from {lte_enb_prev} to {lte_enb_curr}",
                before_state={"lte_enb_id": lte_enb_prev, "lte_cid": previous_data.get("lte_cid")},
                after_state={"lte_enb_id": lte_enb_curr, "lte_cid": current_data.get("lte_cid")},
            )
            if event:
                events.append(event)

        # Check for connection mode change
        mode_curr = current_data.get("connection_mode")
        mode_prev = previous_data.get("connection_mode")

        if mode_curr and mode_prev and mode_curr != mode_prev:
            # Determine severity based on change direction
            if mode_prev in ("SA", "NSA") and mode_curr == "LTE":
                severity = DisruptionSeverity.WARNING
            elif mode_curr == "No Signal":
                severity = DisruptionSeverity.CRITICAL
            else:
                severity = DisruptionSeverity.INFO

            event = await self._maybe_create_event(
                event_type="connection_mode_change",
                severity=severity,
                description=f"Connection mode changed from {mode_prev} to {mode_curr}",
                before_state={"connection_mode": mode_prev},
                after_state={"connection_mode": mode_curr},
            )
            if event:
                events.append(event)

        return events

    async def _maybe_create_event(
        self,
        event_type: str,
        severity: DisruptionSeverity,
        description: str,
        before_state: dict[str, Any],
        after_state: dict[str, Any],
    ) -> DisruptionEvent | None:
        """Create an event if not in cooldown period."""
        now = time.time()
        last_time = self._cooldowns.get(event_type, 0)

        if now - last_time < self._cooldown_seconds:
            return None

        self._cooldowns[event_type] = now

        event = DisruptionEvent(
            event_type=event_type,
            severity=severity,
            description=description,
            before_state=before_state,
            after_state=after_state,
        )

        # Save to database
        await self.repo.insert(event)
        log.info("disruption_detected", event_type=event_type, severity=severity.value)

        return event

    async def get_disruptions(self, duration_hours: int = 24) -> list[dict]:
        """Get disruption events for the specified duration."""
        return await self.repo.query(duration_hours)

    async def get_stats(self, duration_hours: int = 24) -> dict:
        """Get disruption statistics."""
        return await self.repo.get_stats(duration_hours)

    async def create_gateway_outage_event(
        self,
        start_time: float,
        error_count: int,
        last_error: str | None,
    ) -> int:
        """Create a gateway outage event when connectivity is lost.

        Returns the event ID for later resolution.
        """
        from datetime import datetime

        event = DisruptionEvent(
            timestamp=datetime.utcfromtimestamp(start_time),
            timestamp_unix=start_time,
            event_type="gateway_unreachable",
            severity=DisruptionSeverity.CRITICAL,
            description=f"Gateway unreachable - {error_count} consecutive poll failures",
            before_state={
                "error_count": error_count,
                "last_error": last_error,
            },
            after_state={},
            resolved=False,
        )

        event_id = await self.repo.insert(event)
        log.warning(
            "gateway_outage_event_created",
            event_id=event_id,
            start_time=start_time,
            error_count=error_count,
        )

        return event_id

    async def resolve_gateway_outage_event(
        self,
        event_id: int,
        end_time: float,
        duration_seconds: float,
        error_count: int,
    ) -> bool:
        """Resolve a gateway outage event when connectivity is restored.

        Returns True if the event was successfully updated.
        """
        from datetime import datetime

        resolved_at = datetime.utcfromtimestamp(end_time).isoformat()
        after_state = {
            "recovered": True,
            "total_errors_during_outage": error_count,
        }

        success = await self.repo.resolve(
            event_id=event_id,
            duration_seconds=duration_seconds,
            resolved_at=resolved_at,
            after_state=after_state,
        )

        if success:
            log.info(
                "gateway_outage_event_resolved",
                event_id=event_id,
                duration_seconds=duration_seconds,
                error_count=error_count,
            )
        else:
            log.error("gateway_outage_event_resolve_failed", event_id=event_id)

        return success


# Global service instance
_disruption_service: DisruptionService | None = None


def get_disruption_service() -> DisruptionService:
    """Get the global disruption service instance."""
    global _disruption_service
    if _disruption_service is None:
        _disruption_service = DisruptionService()
    return _disruption_service
