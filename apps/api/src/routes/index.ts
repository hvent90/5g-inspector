/**
 * Route handlers for the NetPulse API.
 *
 * This module exports all route handlers that can be mounted
 * on the main HTTP router.
 *
 * Consolidated API (~29 endpoints):
 *
 * Health:
 * - GET /health - Full health status
 * - GET /health/live - Kubernetes liveness probe
 * - GET /health/ready - Kubernetes readiness probe
 *
 * Signal:
 * - GET /api/signal - Current signal metrics
 * - GET /api/signal/history - Signal history with time range
 *
 * Speedtest:
 * - GET /api/speedtest - Recent speedtest results
 * - POST /api/speedtest - Trigger a new speedtest
 *
 * Scheduler:
 * - GET /api/scheduler/config - Scheduler configuration
 * - PUT /api/scheduler/config - Update scheduler configuration
 * - GET /api/scheduler/stats - Scheduler statistics
 * - POST /api/scheduler/start - Start scheduler
 * - POST /api/scheduler/stop - Stop scheduler
 *
 * Disruptions:
 * - GET /api/disruptions - Disruption events
 *
 * Alerts:
 * - GET /api/alerts - Active alerts
 * - GET /api/alerts/history - Alert history
 *
 * Gateway:
 * - GET /api/gateway/status - Gateway connectivity status
 *
 * Diagnostics:
 * - GET /api/diagnostics - System diagnostics (basic health)
 * - GET /api/diagnostics/summary - Signal metrics summary
 * - GET /api/diagnostics/disruptions - Disruption detection
 * - GET /api/diagnostics/patterns - Time of day patterns
 * - GET /api/diagnostics/towers - Tower connection history
 * - GET /api/diagnostics/report - Full diagnostic report
 * - GET /api/diagnostics/export/json - Export to JSON
 * - GET /api/diagnostics/export/csv - Export to CSV
 *
 * Congestion:
 * - GET /api/congestion-proof - Congestion proof analysis report
 *
 * Events:
 * - GET /api/events - Real-time event stream (SSE)
 */

export * from "./health.js"
export * from "./signal.js"
export * from "./speedtest.js"
export * from "./scheduler.js"
export * from "./network-quality.js"
export * from "./disruptions.js"
export * from "./alerts.js"
export * from "./gateway.js"
export * from "./diagnostics.js"
export * from "./congestion.js"
export * from "./events.js"
