"""FastAPI application for T-Mobile Dashboard."""

import time

# Configure logging FIRST before any other imports that might use structlog
from .logging import configure_logging
configure_logging()

from fastapi import FastAPI, Query, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, Response, PlainTextResponse

from .config import get_settings
from .db import get_db
from .metrics import get_metrics
from .middleware import CorrelationIDMiddleware, RequestLoggingMiddleware
from .models import HealthStatus, ComponentHealth, AlertConfig
from .services import (
    lifespan,
    AppState,
    get_congestion_service,
    get_alert_service,
    get_diagnostics_service,
    get_disruption_service,
    get_service_terms_service,
    get_support_service,
    get_scheduler_service,
    get_network_quality_service,
)

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description="Real-time 5G/LTE signal monitoring API with long-running stability",
    version=settings.version,
    lifespan=lifespan,
)

# Middleware (order matters - first added is outermost)
# CORS must be outermost
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Request logging (logs after response is complete)
app.add_middleware(RequestLoggingMiddleware)
# Correlation ID (innermost - sets up context first)
app.add_middleware(CorrelationIDMiddleware)


def get_app_state(request: Request) -> AppState:
    """Get application state from request."""
    return request.state.state


# ============================================
# Health & Status Endpoints
# ============================================


@app.get("/health", response_model=HealthStatus)
async def health_check(request: Request) -> HealthStatus:
    """Health check endpoint for monitoring.

    Returns full health status with component details.
    Use /health/live for liveness probes and /health/ready for readiness probes.
    """
    state: AppState = get_app_state(request)
    db = get_db()

    components = []

    # Check database
    db_healthy = await db.is_connected()
    components.append(
        ComponentHealth(name="database", healthy=db_healthy, message="OK" if db_healthy else "Connection failed")
    )

    # Check gateway
    gateway_stats = state.gateway.get_stats()
    gateway_healthy = gateway_stats["circuit_state"] != "open"
    components.append(
        ComponentHealth(
            name="gateway",
            healthy=gateway_healthy,
            message=f"Circuit: {gateway_stats['circuit_state']}",
        )
    )

    # Determine overall status
    all_healthy = all(c.healthy for c in components)
    some_healthy = any(c.healthy for c in components)

    if all_healthy:
        status = "healthy"
    elif some_healthy:
        status = "degraded"
    else:
        status = "unhealthy"

    return HealthStatus(
        status=status,
        uptime_seconds=state.uptime_seconds,
        version=settings.version,
        components=components,
        last_signal_poll=state.gateway.current_data.timestamp if state.gateway.current_data else None,
        signal_poll_success_rate=(
            gateway_stats["success_count"] / max(gateway_stats["success_count"] + gateway_stats["error_count"], 1)
        ),
        db_connected=db_healthy,
        active_alerts=0,  # TODO: integrate alerts
    )


@app.get("/health/live")
async def liveness_probe() -> dict:
    """Kubernetes liveness probe endpoint.

    Returns 200 if the application is running.
    This is a lightweight check - the app is alive if this endpoint responds.
    """
    return {"status": "alive"}


@app.get("/health/ready")
async def readiness_probe(request: Request) -> Response:
    """Kubernetes readiness probe endpoint.

    Returns 200 if the application is ready to serve traffic.
    Checks that essential services (database, gateway) are operational.
    Returns 503 if not ready.
    """
    state: AppState = get_app_state(request)
    db = get_db()

    # Check database connectivity
    db_ready = await db.is_connected()

    # Check gateway is not in failed state
    gateway_stats = state.gateway.get_stats()
    gateway_ready = gateway_stats["circuit_state"] != "open"

    if db_ready and gateway_ready:
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "db": "connected", "gateway": gateway_stats["circuit_state"]},
        )
    else:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "db": "connected" if db_ready else "disconnected",
                "gateway": gateway_stats["circuit_state"],
            },
        )


@app.get("/metrics")
async def metrics_endpoint(request: Request) -> PlainTextResponse:
    """Prometheus metrics endpoint.

    Returns metrics in Prometheus text format for scraping.
    """
    state: AppState = get_app_state(request)
    metrics = get_metrics()
    content = metrics.to_prometheus_format(app_state=state)
    return PlainTextResponse(content=content, media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/api/stats")
async def get_stats(request: Request) -> dict:
    """Get gateway polling statistics."""
    state: AppState = get_app_state(request)
    return state.gateway.get_stats()


@app.get("/api/db-stats")
async def get_db_stats() -> dict:
    """Get database statistics."""
    db = get_db()
    return await db.get_stats()


# ============================================
# Signal Endpoints
# ============================================


@app.get("/api/signal")
async def get_signal(request: Request) -> dict:
    """Get current signal data from gateway."""
    state: AppState = get_app_state(request)

    data = state.gateway.current_data
    if data:
        return data.model_dump(mode="json")

    return {"error": "No data available", "stats": state.gateway.get_stats()}


@app.get("/api/signal/raw")
async def get_signal_raw(request: Request) -> dict:
    """Get raw gateway response (for debugging)."""
    state: AppState = get_app_state(request)

    raw = state.gateway.raw_data
    if raw:
        return raw

    return {"error": "No data available"}


@app.get("/api/history")
async def get_history(
    request: Request,
    duration: int = Query(60, description="Duration in minutes", ge=1, le=10080),
    resolution: str = Query("auto", description="Resolution: auto, full, or seconds to bucket"),
    limit: int | None = Query(None, description="Maximum number of records", ge=1, le=100000),
) -> dict:
    """Get historical signal data."""
    state: AppState = get_app_state(request)

    records = await state.signal_repo.query_history(
        duration_minutes=duration,
        resolution=resolution,
        limit=limit,
    )

    return {
        "duration_minutes": duration,
        "resolution": resolution,
        "count": len(records),
        "data": records,
    }


@app.get("/api/tower-history")
async def get_tower_history(
    request: Request,
    duration: int = Query(60, description="Duration in minutes", ge=1, le=10080),
) -> dict:
    """Get tower/cell change history."""
    state: AppState = get_app_state(request)

    changes = await state.signal_repo.get_tower_history(duration_minutes=duration)

    return {
        "duration_minutes": duration,
        "change_count": len(changes),
        "changes": changes,
    }


# ============================================
# Advanced Signal Analysis
# ============================================


@app.get("/api/advanced")
async def get_advanced(request: Request) -> dict:
    """Get advanced signal metrics and analysis."""
    state: AppState = get_app_state(request)

    data = state.gateway.current_data
    if not data:
        return {"error": "No signal data available"}

    # Calculate health score based on SINR
    def calc_health_score(sinr: float | None) -> tuple[int, str]:
        if sinr is None:
            return 0, "N/A"
        if sinr >= 20:
            return 100, "A+"
        if sinr >= 15:
            return 90, "A"
        if sinr >= 10:
            return 80, "B"
        if sinr >= 5:
            return 70, "C"
        if sinr >= 0:
            return 60, "D"
        return 40, "F"

    nr_score, nr_grade = calc_health_score(data.nr.sinr)
    lte_score, lte_grade = calc_health_score(data.lte.sinr)

    # Overall score weighted toward 5G
    overall_score = int(nr_score * 0.7 + lte_score * 0.3) if data.nr.sinr else lte_score

    return {
        "timestamp": data.timestamp.isoformat(),
        "connection_mode": data.connection_mode.value,
        "health_score": {
            "overall": overall_score,
            "grade": nr_grade if data.nr.sinr else lte_grade,
            "nr_score": nr_score,
            "nr_grade": nr_grade,
            "lte_score": lte_score,
            "lte_grade": lte_grade,
        },
        "nr": {
            **data.nr.model_dump(),
            "quality": data.nr.quality.value,
        },
        "lte": {
            **data.lte.model_dump(),
            "quality": data.lte.quality.value,
        },
        "device": {
            "uptime": data.device_uptime,
            "registration": data.registration_status,
        },
    }


# ============================================
# Congestion Analysis Endpoints
# ============================================


@app.get("/api/congestion")
async def get_congestion_summary(
    days: int = Query(7, description="Number of days to analyze", ge=1, le=90),
) -> dict:
    """Get comprehensive congestion analysis summary.

    Returns heatmap data, peak periods, and weekday/weekend comparisons.
    """
    service = get_congestion_service()
    return await service.get_summary(days)


@app.get("/api/congestion/heatmap")
async def get_congestion_heatmap(
    days: int = Query(7, description="Number of days to analyze", ge=1, le=90),
) -> dict:
    """Get congestion heatmap data by hour of day.

    Returns average congestion score for each hour, split by weekday/weekend.
    """
    service = get_congestion_service()
    heatmap = await service.get_heatmap(days)
    return {"period_days": days, "hours": heatmap}


@app.get("/api/congestion/daily")
async def get_congestion_daily(
    days: int = Query(30, description="Number of days to analyze", ge=1, le=365),
) -> dict:
    """Get daily congestion patterns for trend analysis."""
    service = get_congestion_service()
    patterns = await service.get_daily_patterns(days)
    return {"period_days": days, "count": len(patterns), "patterns": patterns}


@app.get("/api/congestion/peaks")
async def get_congestion_peaks(
    days: int = Query(7, description="Number of days to analyze", ge=1, le=90),
    top_n: int = Query(5, description="Number of periods to return", ge=1, le=20),
) -> dict:
    """Get most and least congested time periods."""
    service = get_congestion_service()
    peaks = await service.get_peak_periods(days, top_n)
    return {"period_days": days, "top_n": top_n, **peaks}


@app.get("/api/congestion/weekday-weekend")
async def get_congestion_weekday_weekend(
    days: int = Query(30, description="Number of days to analyze", ge=1, le=365),
) -> dict:
    """Compare weekday vs weekend congestion patterns."""
    service = get_congestion_service()
    stats = await service.get_weekday_vs_weekend_stats(days)
    return {"period_days": days, **stats}


@app.post("/api/congestion/aggregate")
async def trigger_congestion_aggregation() -> dict:
    """Manually trigger hourly metrics aggregation.

    This is normally run automatically but can be triggered manually.
    """
    service = get_congestion_service()
    inserted = await service.aggregate_hourly_metrics()
    return {"status": "success", "aggregated_hours": inserted}


@app.get("/api/congestion-proof")
async def get_congestion_proof_report(
    days: int = Query(7, description="Number of days to analyze", ge=1, le=90),
) -> dict:
    """Generate comprehensive congestion proof report for FCC complaint.

    Analyzes signal quality vs speed to prove network congestion rather than
    signal issues. Returns:
    - Signal quality analysis showing signal is acceptable
    - Speed vs signal correlation showing poor speeds despite good signal
    - Time pattern analysis comparing peak vs off-peak performance
    - Evidence summary with key findings
    - Overall conclusion for FCC complaint
    """
    service = get_congestion_service()
    return await service.generate_congestion_proof_report(days)


# ============================================
# Speedtest Endpoints
# ============================================


@app.get("/api/speedtest/status")
async def get_speedtest_status(request: Request) -> dict:
    """Check if a speed test is currently running."""
    state: AppState = get_app_state(request)
    status = state.speedtest.get_status()
    return {"running": status.running}


@app.get("/api/speedtest/tools")
async def get_speedtest_tools(request: Request) -> dict:
    """Get available speedtest tools and configuration.

    Returns which tools are installed and can be used, plus current config.
    """
    state: AppState = get_app_state(request)
    return state.speedtest.get_tool_info()


@app.get("/api/speedtest/history")
async def get_speedtest_history(
    request: Request,
    limit: int = Query(20, description="Number of results to return", ge=1, le=100),
) -> dict:
    """Get speed test history.

    Returns the most recent results first.
    """
    state: AppState = get_app_state(request)
    results = state.speedtest.get_history(limit=limit)
    return {"count": len(results), "results": results}


@app.post("/api/speedtest")
async def run_speedtest(
    request: Request,
    tool: str | None = Query(None, description="Speedtest tool to use (e.g., ookla-speedtest, fast-cli)"),
    server_id: int | None = Query(None, description="Ookla server ID to target"),
) -> dict:
    """Run a speed test.

    This is a blocking operation that takes 30-60 seconds.
    Use GET /api/speedtest/status to check if a test is already running.

    Query params:
    - tool: Specific tool to use (None = auto-select from preferred_tools)
    - server_id: Target specific Ookla server (None = auto-select)
    """
    state: AppState = get_app_state(request)

    # Get current signal data to correlate with test
    signal_snapshot = state.gateway.current_data

    result = await state.speedtest.run_speedtest(
        signal_snapshot=signal_snapshot,
        triggered_by="api",
        tool=tool,
        server_id=server_id,
    )

    # Return appropriate status code
    if result.status == "success":
        return result.model_dump(mode="json")
    elif result.status == "busy":
        return {"error": result.error_message, "status": "busy"}
    else:
        return {"error": result.error_message, "status": result.status}


@app.get("/api/speedtest")
async def run_speedtest_get(
    request: Request,
    tool: str | None = Query(None, description="Speedtest tool to use (e.g., ookla-speedtest, fast-cli)"),
    server_id: int | None = Query(None, description="Ookla server ID to target"),
) -> dict:
    """Run a speed test (GET method for legacy compatibility).

    This is a blocking operation that takes 30-60 seconds.
    Prefer POST /api/speedtest for new implementations.

    Query params:
    - tool: Specific tool to use (None = auto-select from preferred_tools)
    - server_id: Target specific Ookla server (None = auto-select)
    """
    state: AppState = get_app_state(request)

    signal_snapshot = state.gateway.current_data

    result = await state.speedtest.run_speedtest(
        signal_snapshot=signal_snapshot,
        triggered_by="api",
        tool=tool,
        server_id=server_id,
    )

    if result.status == "success":
        return result.model_dump(mode="json")
    elif result.status == "busy":
        return {"error": result.error_message, "status": "busy"}
    else:
        return {"error": result.error_message, "status": result.status}


# ============================================
# Error Handlers
# ============================================


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global exception handler."""
    import structlog
    log = structlog.get_logger()
    log.error("unhandled_exception", error=str(exc), path=request.url.path)

    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc) if settings.debug else None},
    )


# ============================================
# Alerts Endpoints
# ============================================


@app.get("/api/alerts")
async def get_active_alerts() -> dict:
    """Get all currently active (unresolved) alerts."""
    service = get_alert_service()
    alerts = service.get_active_alerts()
    return {"count": len(alerts), "alerts": alerts}


@app.get("/api/alerts/history")
async def get_alert_history(
    limit: int = Query(100, description="Number of alerts to return", ge=1, le=1000),
    offset: int = Query(0, description="Number of alerts to skip", ge=0),
) -> dict:
    """Get alert history with pagination."""
    service = get_alert_service()
    alerts = service.get_history(limit=limit, offset=offset)
    return {"limit": limit, "offset": offset, "count": len(alerts), "alerts": alerts}


@app.get("/api/alerts/config")
async def get_alert_config() -> dict:
    """Get current alert configuration."""
    service = get_alert_service()
    return service.get_config().model_dump()


@app.put("/api/alerts/config")
async def update_alert_config(config: AlertConfig) -> dict:
    """Update alert configuration."""
    service = get_alert_service()
    updated = service.update_config(config)
    return updated.model_dump()


@app.post("/api/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str) -> dict:
    """Acknowledge an alert."""
    service = get_alert_service()
    success = service.acknowledge_alert(alert_id)
    if not success:
        return JSONResponse(status_code=404, content={"error": "Alert not found"})
    return {"status": "acknowledged", "alert_id": alert_id}


@app.post("/api/alerts/{alert_id}/clear")
async def clear_alert(alert_id: str) -> dict:
    """Clear a specific alert."""
    service = get_alert_service()
    success = service.clear_alert(alert_id)
    if not success:
        return JSONResponse(status_code=404, content={"error": "Alert not found"})
    return {"status": "cleared", "alert_id": alert_id}


@app.post("/api/alerts/clear")
async def clear_all_alerts() -> dict:
    """Clear all active alerts."""
    service = get_alert_service()
    count = service.clear_all_alerts()
    return {"status": "cleared", "count": count}


@app.post("/api/alerts/test")
async def trigger_test_alert() -> dict:
    """Trigger a test alert for testing notifications."""
    service = get_alert_service()
    alert = service.trigger_test_alert()
    if alert:
        return {"status": "triggered", "alert": alert.model_dump(mode="json")}
    return {"status": "disabled", "message": "Alerts are disabled or in cooldown"}


@app.get("/api/alerts/stream")
async def alert_stream() -> StreamingResponse:
    """Server-Sent Events stream for real-time alert notifications."""
    service = get_alert_service()
    subscriber = service.subscribe_sse()

    async def event_generator():
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                # Check for messages in queue
                if subscriber["queue"]:
                    while subscriber["queue"]:
                        message = subscriber["queue"].popleft()
                        yield message
                else:
                    # Send keepalive
                    yield ": keepalive\n\n"
                import asyncio
                await asyncio.sleep(1)
        finally:
            service.unsubscribe_sse(subscriber)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================
# Diagnostics Endpoints
# ============================================


@app.get("/api/diagnostics")
async def get_diagnostic_report(
    duration: int = Query(24, description="Duration in hours", ge=1, le=720),
) -> dict:
    """Generate a comprehensive diagnostic report."""
    service = get_diagnostics_service()
    return await service.generate_full_report(duration)


@app.get("/api/diagnostics/signal-summary")
async def get_signal_summary(
    duration: int = Query(24, description="Duration in hours", ge=1, le=720),
) -> dict:
    """Get signal metrics summary with statistics."""
    service = get_diagnostics_service()
    return await service.get_signal_metrics_summary(duration)


@app.get("/api/diagnostics/disruptions")
async def get_disruptions(
    duration: int = Query(24, description="Duration in hours", ge=1, le=720),
) -> dict:
    """Get detected signal disruption events."""
    service = get_diagnostics_service()
    return await service.detect_disruptions(duration)


@app.get("/api/diagnostics/time-patterns")
async def get_time_patterns(
    duration: int = Query(168, description="Duration in hours (default 7 days)", ge=1, le=720),
) -> dict:
    """Get time-of-day performance patterns."""
    service = get_diagnostics_service()
    return await service.get_time_of_day_patterns(duration)


@app.get("/api/diagnostics/tower-history")
async def get_diagnostic_tower_history(
    duration: int = Query(24, description="Duration in hours", ge=1, le=720),
) -> dict:
    """Get tower/cell connection history."""
    service = get_diagnostics_service()
    return await service.get_tower_connection_history(duration)


@app.get("/api/diagnostics/export/json")
async def export_diagnostics_json(
    duration: int = Query(24, description="Duration in hours", ge=1, le=720),
) -> Response:
    """Export diagnostic report as JSON file."""
    service = get_diagnostics_service()
    report = await service.generate_full_report(duration)
    content = service.export_to_json(report)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="diagnostic_report_{timestamp}.json"'
        },
    )


@app.get("/api/diagnostics/export/csv")
async def export_diagnostics_csv(
    duration: int = Query(24, description="Duration in hours", ge=1, le=720),
) -> Response:
    """Export diagnostic report as CSV file."""
    service = get_diagnostics_service()
    report = await service.generate_full_report(duration)
    content = service.export_to_csv(report)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return Response(
        content=content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="diagnostic_report_{timestamp}.csv"'
        },
    )


# ============================================
# Disruption Detection Endpoints
# ============================================


@app.get("/api/disruption")
async def get_disruption_events(
    duration: int = Query(24, description="Duration in hours", ge=1, le=720),
) -> dict:
    """Get real-time disruption events."""
    service = get_disruption_service()
    events = await service.get_disruptions(duration)
    return {"duration_hours": duration, "count": len(events), "events": events}


@app.get("/api/disruption/stats")
async def get_disruption_stats(
    duration: int = Query(24, description="Duration in hours", ge=1, le=720),
) -> dict:
    """Get disruption statistics."""
    service = get_disruption_service()
    return await service.get_stats(duration)


# ============================================
# Service Terms Documentation Endpoints
# ============================================


@app.get("/api/service-terms")
async def get_service_terms() -> dict:
    """Get current service terms documentation."""
    service = get_service_terms_service()
    return service.get_terms()


@app.put("/api/service-terms")
async def update_service_terms(updates: dict = Body(...)) -> dict:
    """Update service terms documentation."""
    service = get_service_terms_service()
    return service.update_terms(updates)


@app.get("/api/service-terms/summary")
async def get_service_terms_summary() -> dict:
    """Get service terms summary for FCC complaint."""
    service = get_service_terms_service()
    return service.get_summary()


@app.get("/api/service-terms/fcc-export")
async def export_service_terms_fcc() -> dict:
    """Export service terms in FCC complaint format."""
    service = get_service_terms_service()
    return service.get_fcc_export()


# ============================================
# Support Interaction Tracking Endpoints
# ============================================


@app.get("/api/support")
async def get_support_interactions() -> dict:
    """Get all support interaction records."""
    service = get_support_service()
    interactions = service.get_all()
    return {"count": len(interactions), "interactions": interactions}


@app.get("/api/support/{interaction_id}")
async def get_support_interaction(interaction_id: str) -> dict:
    """Get a specific support interaction."""
    service = get_support_service()
    interaction = service.get_by_id(interaction_id)
    if not interaction:
        return JSONResponse(status_code=404, content={"error": "Interaction not found"})
    return interaction


@app.post("/api/support")
async def create_support_interaction(data: dict = Body(...)) -> dict:
    """Create a new support interaction record."""
    service = get_support_service()
    return service.create(data)


@app.put("/api/support/{interaction_id}")
async def update_support_interaction(interaction_id: str, updates: dict = Body(...)) -> dict:
    """Update an existing support interaction."""
    service = get_support_service()
    result = service.update(interaction_id, updates)
    if not result:
        return JSONResponse(status_code=404, content={"error": "Interaction not found"})
    return result


@app.delete("/api/support/{interaction_id}")
async def delete_support_interaction(interaction_id: str) -> dict:
    """Delete a support interaction record."""
    service = get_support_service()
    success = service.delete(interaction_id)
    if not success:
        return JSONResponse(status_code=404, content={"error": "Interaction not found"})
    return {"status": "deleted", "id": interaction_id}


@app.get("/api/support/summary")
async def get_support_summary() -> dict:
    """Get summary statistics of support interactions."""
    service = get_support_service()
    return service.get_summary()


@app.get("/api/support/fcc-export")
async def export_support_fcc() -> dict:
    """Export support interactions in FCC complaint format."""
    service = get_support_service()
    return service.export_for_fcc()


# ============================================
# Scheduler Endpoints (Automated Speed Tests)
# ============================================


@app.get("/api/scheduler/config")
async def get_scheduler_config() -> dict:
    """Get scheduler configuration."""
    service = get_scheduler_service()
    return service.get_config()


@app.put("/api/scheduler/config")
async def update_scheduler_config(updates: dict = Body(...)) -> dict:
    """Update scheduler configuration."""
    service = get_scheduler_service()
    return service.update_config(updates)


@app.get("/api/scheduler/stats")
async def get_scheduler_stats() -> dict:
    """Get scheduler statistics."""
    service = get_scheduler_service()
    return service.get_stats()


@app.post("/api/scheduler/start")
async def start_scheduler() -> dict:
    """Start the scheduled speed test scheduler."""
    service = get_scheduler_service()
    success = await service.start()
    if success:
        return {"status": "started", "config": service.get_config()}
    return {"status": "already_running", "config": service.get_config()}


@app.post("/api/scheduler/stop")
async def stop_scheduler() -> dict:
    """Stop the scheduled speed test scheduler."""
    service = get_scheduler_service()
    success = await service.stop()
    if success:
        return {"status": "stopped"}
    return {"status": "already_stopped"}


@app.post("/api/scheduler/trigger")
async def trigger_scheduler_test() -> dict:
    """Manually trigger a scheduled speed test."""
    service = get_scheduler_service()
    result = await service.trigger_test_now()
    return result


@app.get("/api/scheduler/history")
async def get_scheduler_history(
    limit: int = Query(100, description="Number of results to return", ge=1, le=1000),
    offset: int = Query(0, description="Number of results to skip", ge=0),
    status: str | None = Query(None, description="Filter by status"),
    hour: int | None = Query(None, description="Filter by hour of day", ge=0, le=23),
) -> dict:
    """Get scheduled test history."""
    service = get_scheduler_service()
    return await service.get_history(
        limit=limit,
        offset=offset,
        status_filter=status,
        hour_filter=hour,
    )


@app.get("/api/scheduler/hourly-stats")
async def get_scheduler_hourly_stats() -> dict:
    """Get hourly aggregated speed test statistics."""
    service = get_scheduler_service()
    return await service.get_hourly_stats()


@app.get("/api/scheduler/evidence")
async def get_scheduler_evidence_summary() -> dict:
    """Get evidence summary for FCC complaint."""
    service = get_scheduler_service()
    return await service.get_evidence_summary()


# ============================================
# Network Quality Endpoints (Ping/Jitter/Loss)
# ============================================


@app.get("/api/network-quality/config")
async def get_network_quality_config() -> dict:
    """Get network quality monitor configuration."""
    service = get_network_quality_service()
    return service.get_config()


@app.put("/api/network-quality/config")
async def update_network_quality_config(updates: dict = Body(...)) -> dict:
    """Update network quality monitor configuration."""
    service = get_network_quality_service()
    return service.update_config(updates)


@app.get("/api/network-quality/stats")
async def get_network_quality_stats() -> dict:
    """Get network quality monitoring statistics."""
    service = get_network_quality_service()
    return service.get_stats()


@app.post("/api/network-quality/start")
async def start_network_quality_monitor() -> dict:
    """Start the network quality monitor."""
    service = get_network_quality_service()
    success = await service.start()
    if success:
        return {"status": "started", "config": service.get_config()}
    return {"status": "already_running", "config": service.get_config()}


@app.post("/api/network-quality/stop")
async def stop_network_quality_monitor() -> dict:
    """Stop the network quality monitor."""
    service = get_network_quality_service()
    success = await service.stop()
    if success:
        return {"status": "stopped"}
    return {"status": "already_stopped"}


@app.post("/api/network-quality/trigger")
async def trigger_network_quality_test(request: Request) -> dict:
    """Manually trigger a network quality test."""
    state: AppState = get_app_state(request)
    signal_snapshot = state.gateway.current_data
    service = get_network_quality_service()
    results = await service.trigger_test_now(signal_snapshot)
    return {"status": "completed", "count": len(results), "results": results}


@app.get("/api/network-quality")
async def get_network_quality_latest() -> dict:
    """Get latest network quality results."""
    service = get_network_quality_service()
    results = await service.get_latest_results()
    return {"count": len(results), "results": results}


@app.get("/api/network-quality/history")
async def get_network_quality_history(
    limit: int = Query(100, description="Number of results to return", ge=1, le=1000),
    offset: int = Query(0, description="Number of results to skip", ge=0),
    target: str | None = Query(None, description="Filter by target host"),
    hour: int | None = Query(None, description="Filter by hour of day", ge=0, le=23),
) -> dict:
    """Get network quality test history."""
    service = get_network_quality_service()
    return await service.get_history(
        limit=limit,
        offset=offset,
        target_filter=target,
        hour_filter=hour,
    )


@app.get("/api/network-quality/hourly-stats")
async def get_network_quality_hourly_stats(
    target: str | None = Query(None, description="Filter by target host"),
) -> dict:
    """Get hourly aggregated network quality statistics."""
    service = get_network_quality_service()
    return await service.get_hourly_stats(target)


@app.get("/api/network-quality/evidence")
async def get_network_quality_evidence_summary() -> dict:
    """Get network quality evidence summary for FCC complaint."""
    service = get_network_quality_service()
    return await service.get_evidence_summary()


# ============================================
# FCC Report Endpoints
# ============================================


@app.get("/api/fcc-report")
async def get_fcc_report() -> dict:
    """Generate a comprehensive FCC complaint report.

    Combines all evidence from speed tests, network quality, disruptions,
    support interactions, and service terms.
    """
    scheduler = get_scheduler_service()
    network_quality = get_network_quality_service()
    service_terms = get_service_terms_service()
    support = get_support_service()
    diagnostics = get_diagnostics_service()

    # Gather all evidence
    speed_evidence = await scheduler.get_evidence_summary()
    quality_evidence = await network_quality.get_evidence_summary()
    terms = service_terms.get_fcc_export()
    support_export = support.export_for_fcc()
    signal_summary = await diagnostics.get_signal_metrics_summary(168)  # 7 days

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "service_information": terms.get("service_information", {}),
        "advertised_performance": terms.get("advertised_performance", {}),
        "speed_test_evidence": speed_evidence,
        "network_quality_evidence": quality_evidence,
        "signal_quality_summary": signal_summary,
        "support_interactions": support_export,
        "policies": terms.get("policies", {}),
    }


@app.get("/api/fcc-readiness")
async def get_fcc_readiness() -> dict:
    """Check FCC complaint readiness.

    Returns a checklist of what evidence has been collected and what's missing.
    """
    scheduler = get_scheduler_service()
    network_quality = get_network_quality_service()
    service_terms = get_service_terms_service()
    support = get_support_service()

    scheduler_stats = scheduler.get_stats()
    quality_stats = network_quality.get_stats()
    terms_summary = service_terms.get_summary()
    support_summary = support.get_summary()

    # Calculate readiness
    speed_test_ready = scheduler_stats.get("tests_completed", 0) >= 30
    collection_days = scheduler_stats.get("collection_days", 0)
    collection_ready = collection_days >= 30

    quality_test_ready = quality_stats.get("tests_completed", 0) >= 30
    terms_ready = terms_summary.get("documentation_complete", False)
    support_logged = support_summary.get("total_interactions", 0) > 0

    overall_ready = (
        speed_test_ready
        and collection_ready
        and terms_ready
    )

    checklist = [
        {
            "item": "Speed test collection",
            "ready": speed_test_ready,
            "current": scheduler_stats.get("tests_completed", 0),
            "recommended": 30,
            "note": "Minimum 30 tests recommended",
        },
        {
            "item": "Collection period",
            "ready": collection_ready,
            "current": collection_days,
            "recommended": 30,
            "note": "30+ day collection period recommended",
        },
        {
            "item": "Network quality tests",
            "ready": quality_test_ready,
            "current": quality_stats.get("tests_completed", 0),
            "recommended": 30,
            "note": "Packet loss and jitter documentation",
        },
        {
            "item": "Service terms documented",
            "ready": terms_ready,
            "current": "Complete" if terms_ready else "Incomplete",
            "recommended": "Complete",
            "note": "Plan name, cost, advertised speeds",
        },
        {
            "item": "Support interactions logged",
            "ready": support_logged,
            "current": support_summary.get("total_interactions", 0),
            "recommended": "1+",
            "note": "Document attempts to resolve issue",
        },
    ]

    return {
        "ready": overall_ready,
        "readiness_percent": round(
            sum(1 for item in checklist if item["ready"]) / len(checklist) * 100, 1
        ),
        "checklist": checklist,
        "scheduler_stats": scheduler_stats,
        "network_quality_stats": quality_stats,
        "terms_summary": terms_summary,
        "support_summary": support_summary,
    }
