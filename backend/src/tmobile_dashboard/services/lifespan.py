"""Application lifespan management."""

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator

from fastapi import FastAPI

from ..config import get_settings
from ..db import get_db, SignalRepository
from ..logging import get_logger
from ..models import SignalData
from .gateway import GatewayService, OutageEvent
from .speedtest import SpeedtestService
from .disruption import DisruptionService, get_disruption_service
from .alerts import AlertService, get_alert_service
from .scheduler import get_scheduler_service
from .network_quality import get_network_quality_service

log = get_logger("lifespan")


@dataclass
class AppState:
    """Application state container for dependency injection."""

    gateway: GatewayService = field(default_factory=GatewayService)
    signal_repo: SignalRepository = field(default_factory=SignalRepository)
    speedtest: SpeedtestService = field(default_factory=SpeedtestService)
    disruption: DisruptionService = field(default_factory=get_disruption_service)
    alerts: AlertService = field(default_factory=get_alert_service)
    start_time: float = field(default_factory=time.time)

    # Background task handles
    _cleanup_task: asyncio.Task | None = field(default=None, repr=False)
    _flush_task: asyncio.Task | None = field(default=None, repr=False)

    @property
    def uptime_seconds(self) -> float:
        """Get application uptime in seconds."""
        return time.time() - self.start_time


# Global state instance
_state: AppState | None = None


def get_state() -> AppState:
    """Get the global application state."""
    if _state is None:
        raise RuntimeError("Application state not initialized. Is the app running?")
    return _state


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[dict]:
    """Application lifespan manager.

    Handles:
    - Database initialization
    - Gateway polling startup/shutdown
    - Background cleanup tasks
    - Graceful shutdown
    """
    global _state
    settings = get_settings()

    log.info(
        "app_starting",
        app_name=settings.app_name,
        version=settings.version,
        debug=settings.debug,
    )

    # Initialize database
    db = get_db()
    await db.initialize()

    # Create application state
    state = AppState()
    _state = state

    # Track previous signal data for disruption detection
    _previous_data: dict | None = None

    # Register signal data callback to buffer to database and detect disruptions
    async def on_signal_update(data: SignalData) -> None:
        """Buffer signal data for database insert and detect disruptions."""
        nonlocal _previous_data

        record = {
            "timestamp": data.timestamp.isoformat(),
            "timestamp_unix": data.timestamp_unix,
            "nr_sinr": data.nr.sinr,
            "nr_rsrp": data.nr.rsrp,
            "nr_rsrq": data.nr.rsrq,
            "nr_rssi": data.nr.rssi,
            "nr_bands": ",".join(data.nr.bands) if data.nr.bands else None,
            "nr_gnb_id": data.nr.tower_id,
            "nr_cid": data.nr.cell_id,
            "lte_sinr": data.lte.sinr,
            "lte_rsrp": data.lte.rsrp,
            "lte_rsrq": data.lte.rsrq,
            "lte_rssi": data.lte.rssi,
            "lte_bands": ",".join(data.lte.bands) if data.lte.bands else None,
            "lte_enb_id": data.lte.tower_id,
            "lte_cid": data.lte.cell_id,
            "registration_status": data.registration_status,
            "device_uptime": data.device_uptime,
        }
        state.signal_repo.buffer_record(record)

        # Detect disruptions by comparing current vs previous signal data
        current_data = {
            "nr_sinr": data.nr.sinr,
            "nr_rsrp": data.nr.rsrp,
            "nr_gnb_id": data.nr.tower_id,
            "nr_cid": data.nr.cell_id,
            "lte_sinr": data.lte.sinr,
            "lte_rsrp": data.lte.rsrp,
            "lte_enb_id": data.lte.tower_id,
            "lte_cid": data.lte.cell_id,
            "connection_mode": data.connection_mode.value,
        }

        try:
            events = await state.disruption.detect_disruption(current_data, _previous_data)
            if events:
                log.info("disruption_events_detected", count=len(events))
        except Exception as e:
            log.error("disruption_detection_error", error=str(e))

        _previous_data = current_data

    state.gateway.on_signal_update(on_signal_update)

    # Track current gateway outage event for resolution
    _current_outage_event_id: int | None = None

    # Register gateway outage callbacks to persist outage events to database
    async def on_gateway_outage_start(outage: OutageEvent) -> None:
        """Create a disruption event when gateway becomes unreachable."""
        nonlocal _current_outage_event_id

        try:
            event_id = await state.disruption.create_gateway_outage_event(
                start_time=outage.start_time,
                error_count=outage.error_count,
                last_error=outage.last_error,
            )
            _current_outage_event_id = event_id
            log.warning(
                "gateway_outage_persisted",
                event_id=event_id,
                error_count=outage.error_count,
            )
        except Exception as e:
            log.error("gateway_outage_persist_error", error=str(e))

        # Push outage start to Loki for event visualization
        if settings.loki.enabled:
            try:
                from .loki import get_loki_client
                loki = get_loki_client()

                event_data = {
                    "event": "gateway_outage_start",
                    "timestamp_unix": outage.start_time,
                    "error_count": outage.error_count,
                    "last_error": outage.last_error,
                }
                # Add last known signal for correlation
                if state.gateway.current_data:
                    event_data["last_signal"] = {
                        "nr_sinr": state.gateway.current_data.nr.sinr,
                        "nr_rsrp": state.gateway.current_data.nr.rsrp,
                        "lte_sinr": state.gateway.current_data.lte.sinr,
                        "lte_rsrp": state.gateway.current_data.lte.rsrp,
                    }
                await loki.push_event("gateway_outage", event_data, {"status": "started"})
            except Exception as e:
                log.warning("loki_outage_start_push_error", error=str(e))

    async def on_gateway_outage_end(outage: OutageEvent) -> None:
        """Resolve the disruption event when gateway connectivity is restored."""
        nonlocal _current_outage_event_id

        if _current_outage_event_id is None:
            log.warning("gateway_outage_end_no_event", message="No outage event to resolve")
            return

        try:
            success = await state.disruption.resolve_gateway_outage_event(
                event_id=_current_outage_event_id,
                end_time=outage.end_time or time.time(),
                duration_seconds=outage.duration_seconds or 0,
                error_count=outage.error_count,
            )
            if success:
                log.info(
                    "gateway_outage_resolved_persisted",
                    event_id=_current_outage_event_id,
                    duration_seconds=outage.duration_seconds,
                )
            _current_outage_event_id = None
        except Exception as e:
            log.error("gateway_outage_resolve_error", error=str(e))

        # Push outage end to Loki for event visualization
        if settings.loki.enabled:
            try:
                from .loki import get_loki_client
                loki = get_loki_client()

                event_data = {
                    "event": "gateway_outage_end",
                    "timestamp_unix": outage.end_time or time.time(),
                    "start_time": outage.start_time,
                    "duration_seconds": outage.duration_seconds,
                    "error_count": outage.error_count,
                }
                await loki.push_event("gateway_outage", event_data, {"status": "resolved"})
            except Exception as e:
                log.warning("loki_outage_end_push_error", error=str(e))

    state.gateway.on_outage_start(on_gateway_outage_start)
    state.gateway.on_outage_end(on_gateway_outage_end)

    # Start gateway polling
    await state.gateway.start_polling()

    # Start background tasks
    state._flush_task = asyncio.create_task(
        _periodic_flush(state.signal_repo, settings.database.batch_interval_seconds)
    )
    state._cleanup_task = asyncio.create_task(
        _periodic_cleanup(db, settings.database.retention_days)
    )

    # Start scheduler for automated speed tests
    scheduler = get_scheduler_service()

    async def run_speedtest_with_signal():
        """Run speedtest with current signal snapshot and scheduler context config."""
        signal_snapshot = state.gateway.current_data
        config = scheduler.get_config()
        result = await state.speedtest.run_speedtest(
            signal_snapshot=signal_snapshot,
            triggered_by="scheduled",
            # Pass context detection settings from scheduler config
            enable_latency_probe=config.get("enable_latency_probe", True),
            idle_hours=config.get("idle_hours", [2, 3, 4, 5]),
            baseline_latency_ms=config.get("baseline_latency_ms", 20.0),
            light_latency_multiplier=config.get("light_latency_multiplier", 1.5),
            busy_latency_multiplier=config.get("busy_latency_multiplier", 2.5),
        )
        return result.model_dump(mode="json")

    scheduler.set_speedtest_func(run_speedtest_with_signal)
    await scheduler.start()
    log.info("scheduler_auto_started", interval_minutes=scheduler.get_config().get("interval_minutes"))

    # Start network quality monitor
    network_quality = get_network_quality_service()
    await network_quality.start()
    log.info("network_quality_auto_started", interval_minutes=network_quality.get_config().get("interval_minutes"))

    # Start continuous ping monitor for short outage detection
    from .ping_monitor import get_ping_monitor
    ping_monitor = get_ping_monitor()
    await ping_monitor.start()
    log.info("ping_monitor_auto_started", interval_seconds=ping_monitor.get_config().get("interval_seconds"))

    # Initialize Loki client for event logging (creates singleton)
    from .loki import get_loki_client
    get_loki_client()
    log.info("loki_client_initialized", url=settings.loki.url, enabled=settings.loki.enabled)

    log.info("app_started", uptime_seconds=0)

    # Yield state to the application
    yield {"state": state}

    # Shutdown
    log.info("app_stopping")

    # Stop scheduler and network quality monitor
    scheduler = get_scheduler_service()
    await scheduler.stop()
    log.info("scheduler_stopped")

    network_quality = get_network_quality_service()
    await network_quality.stop()
    log.info("network_quality_stopped")

    # Stop continuous ping monitor
    from .ping_monitor import get_ping_monitor
    ping_monitor = get_ping_monitor()
    await ping_monitor.stop()
    log.info("ping_monitor_stopped")

    # Stop gateway polling
    await state.gateway.stop_polling()

    # Cancel background tasks
    for task in [state._flush_task, state._cleanup_task]:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # Final flush of buffered data
    try:
        flushed = await state.signal_repo.flush_buffer()
        if flushed:
            log.info("final_buffer_flush", records=flushed)
    except Exception as e:
        log.error("final_flush_error", error=str(e))

    # Close Loki client
    from .loki import close_loki_client
    await close_loki_client()
    log.info("loki_client_closed")

    _state = None
    log.info("app_stopped", uptime_seconds=state.uptime_seconds)


async def _periodic_flush(repo: SignalRepository, interval_seconds: int) -> None:
    """Periodically flush signal buffer to database."""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            flushed = await repo.flush_buffer()
            if flushed > 0:
                log.debug("periodic_flush", records=flushed)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("periodic_flush_error", error=str(e))


async def _periodic_cleanup(db, retention_days: int) -> None:
    """Periodically clean up old data."""
    # Run cleanup once per hour
    cleanup_interval = 60 * 60

    while True:
        try:
            await asyncio.sleep(cleanup_interval)
            deleted = await db.cleanup_old_data(retention_days)
            if deleted > 0:
                log.info("periodic_cleanup", deleted_rows=deleted)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("periodic_cleanup_error", error=str(e))
