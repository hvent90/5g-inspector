/**
 * Diagnostics routes - endpoints for diagnostic reports using DiagnosticsService.
 *
 * Endpoints:
 * - GET /api/diagnostics - System diagnostics (basic health)
 * - GET /api/diagnostics/summary - Signal metrics summary
 * - GET /api/diagnostics/disruptions - Disruption detection
 * - GET /api/diagnostics/patterns - Time of day patterns
 * - GET /api/diagnostics/towers - Tower connection history
 * - GET /api/diagnostics/report - Full diagnostic report
 * - GET /api/diagnostics/export/json - Export report to JSON
 * - GET /api/diagnostics/export/csv - Export report to CSV
 */

import { HttpRouter, HttpServerRequest, HttpServerResponse } from "@effect/platform"
import { Effect, Schema } from "effect"
import { GatewayServiceTag } from "../services/GatewayService.js"
import { SignalRepository } from "../services/SignalRepository.js"
import { DiagnosticsService } from "../services/DiagnosticsService.js"

// Track server start time for uptime calculation
const serverStartTime = Date.now()

// Query params for duration-based endpoints
const DurationQuerySchema = Schema.Struct({
  duration_hours: Schema.optional(Schema.NumberFromString),
})

/**
 * Diagnostics routes
 */
export const DiagnosticsRoutes = HttpRouter.empty.pipe(
  // GET /api/diagnostics - System diagnostics (basic health)
  HttpRouter.get(
    "/api/diagnostics",
    Effect.gen(function* () {
      const gateway = yield* GatewayServiceTag
      const repo = yield* SignalRepository

      // Get gateway stats
      const gatewayStats = yield* gateway.getStats()

      // Get latest signal for current state
      const latestSignal = yield* repo.getLatestSignal().pipe(
        Effect.catchAll(() => Effect.succeed(null))
      )

      // Get latest speedtest
      const latestSpeedtest = yield* repo.getLatestSpeedtest().pipe(
        Effect.catchAll(() => Effect.succeed(null))
      )

      // Get recent disruption stats (last 24h)
      const disruptionStats = yield* repo.getDisruptionStats(24).pipe(
        Effect.catchAll(() => Effect.succeed({
          period_hours: 24,
          total_events: 0,
          events_by_type: {},
          events_by_severity: {},
          avg_duration_seconds: null,
        }))
      )

      // Build diagnostics response
      const now = Date.now()
      const uptimeSeconds = Math.floor((now - serverStartTime) / 1000)

      return yield* HttpServerResponse.json({
        timestamp: new Date().toISOString(),
        uptime_seconds: uptimeSeconds,

        // Gateway status
        gateway: {
          status: gatewayStats.circuit_state === "closed" ? "healthy" :
                  gatewayStats.circuit_state === "half_open" ? "recovering" : "unhealthy",
          is_running: gatewayStats.is_running,
          circuit_state: gatewayStats.circuit_state,
          poll_stats: {
            success_count: gatewayStats.success_count,
            error_count: gatewayStats.error_count,
            last_error: gatewayStats.last_error,
          },
        },

        // Current signal snapshot
        signal: latestSignal ? {
          timestamp: latestSignal.timestamp,
          nr: {
            sinr: latestSignal.nr_sinr,
            rsrp: latestSignal.nr_rsrp,
            rsrq: latestSignal.nr_rsrq,
            bands: latestSignal.nr_bands,
            tower_id: latestSignal.nr_gnb_id,
          },
          lte: {
            sinr: latestSignal.lte_sinr,
            rsrp: latestSignal.lte_rsrp,
            rsrq: latestSignal.lte_rsrq,
            bands: latestSignal.lte_bands,
            tower_id: latestSignal.lte_enb_id,
          },
        } : null,

        // Latest speedtest
        speedtest: latestSpeedtest ? {
          timestamp: latestSpeedtest.timestamp,
          download_mbps: latestSpeedtest.download_mbps,
          upload_mbps: latestSpeedtest.upload_mbps,
          ping_ms: latestSpeedtest.ping_ms,
          status: latestSpeedtest.status,
        } : null,

        // Disruption summary
        disruptions: {
          period_hours: 24,
          total_events: disruptionStats.total_events,
          by_severity: disruptionStats.events_by_severity,
          avg_duration_seconds: disruptionStats.avg_duration_seconds,
        },

        // System info
        system: {
          version: process.env.npm_package_version ?? "1.0.0",
          node_version: process.version,
          platform: process.platform,
          memory: {
            heap_used_mb: Math.round(process.memoryUsage().heapUsed / 1024 / 1024),
            heap_total_mb: Math.round(process.memoryUsage().heapTotal / 1024 / 1024),
          },
        },
      })
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
            { error: `Failed to get diagnostics: ${error}` },
            { status: 500 }
        )
      )
    )
  ),

  // GET /api/diagnostics/summary - Signal metrics summary
  HttpRouter.get(
    "/api/diagnostics/summary",
    Effect.gen(function* () {
      const request = yield* HttpServerRequest.HttpServerRequest
      const url = new URL(request.url, "http://localhost")

      const queryParams = yield* Schema.decodeUnknown(DurationQuerySchema)({
        duration_hours: url.searchParams.get("duration_hours") ?? undefined,
      }).pipe(
        Effect.catchAll(() => Effect.succeed({ duration_hours: undefined }))
      )

      const diagnostics = yield* DiagnosticsService
      const summary = yield* diagnostics.getSignalMetricsSummary(
        queryParams.duration_hours ?? 24
      )

      return yield* HttpServerResponse.json(summary)
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
            { error: `Failed to get signal summary: ${error}` },
            { status: 500 }
        )
      )
    )
  ),

  // GET /api/diagnostics/disruptions - Disruption detection
  HttpRouter.get(
    "/api/diagnostics/disruptions",
    Effect.gen(function* () {
      const request = yield* HttpServerRequest.HttpServerRequest
      const url = new URL(request.url, "http://localhost")

      const queryParams = yield* Schema.decodeUnknown(DurationQuerySchema)({
        duration_hours: url.searchParams.get("duration_hours") ?? undefined,
      }).pipe(
        Effect.catchAll(() => Effect.succeed({ duration_hours: undefined }))
      )

      const diagnostics = yield* DiagnosticsService
      const disruptions = yield* diagnostics.detectDisruptions(
        queryParams.duration_hours ?? 24
      )

      return yield* HttpServerResponse.json(disruptions)
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
            { error: `Failed to detect disruptions: ${error}` },
            { status: 500 }
        )
      )
    )
  ),

  // GET /api/diagnostics/patterns - Time of day patterns
  HttpRouter.get(
    "/api/diagnostics/patterns",
    Effect.gen(function* () {
      const request = yield* HttpServerRequest.HttpServerRequest
      const url = new URL(request.url, "http://localhost")

      const queryParams = yield* Schema.decodeUnknown(DurationQuerySchema)({
        duration_hours: url.searchParams.get("duration_hours") ?? undefined,
      }).pipe(
        Effect.catchAll(() => Effect.succeed({ duration_hours: undefined }))
      )

      const diagnostics = yield* DiagnosticsService
      const patterns = yield* diagnostics.getTimeOfDayPatterns(
        queryParams.duration_hours ?? 168
      )

      return yield* HttpServerResponse.json(patterns)
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
            { error: `Failed to get time patterns: ${error}` },
            { status: 500 }
        )
      )
    )
  ),

  // GET /api/diagnostics/towers - Tower connection history
  HttpRouter.get(
    "/api/diagnostics/towers",
    Effect.gen(function* () {
      const request = yield* HttpServerRequest.HttpServerRequest
      const url = new URL(request.url, "http://localhost")

      const queryParams = yield* Schema.decodeUnknown(DurationQuerySchema)({
        duration_hours: url.searchParams.get("duration_hours") ?? undefined,
      }).pipe(
        Effect.catchAll(() => Effect.succeed({ duration_hours: undefined }))
      )

      const diagnostics = yield* DiagnosticsService
      const towerHistory = yield* diagnostics.getTowerConnectionHistory(
        queryParams.duration_hours ?? 24
      )

      return yield* HttpServerResponse.json(towerHistory)
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
            { error: `Failed to get tower history: ${error}` },
            { status: 500 }
        )
      )
    )
  ),

  // GET /api/diagnostics/report - Full diagnostic report
  HttpRouter.get(
    "/api/diagnostics/report",
    Effect.gen(function* () {
      const request = yield* HttpServerRequest.HttpServerRequest
      const url = new URL(request.url, "http://localhost")

      const queryParams = yield* Schema.decodeUnknown(DurationQuerySchema)({
        duration_hours: url.searchParams.get("duration_hours") ?? undefined,
      }).pipe(
        Effect.catchAll(() => Effect.succeed({ duration_hours: undefined }))
      )

      const diagnostics = yield* DiagnosticsService
      const report = yield* diagnostics.generateFullReport(
        queryParams.duration_hours ?? 24
      )

      return yield* HttpServerResponse.json(report)
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
            { error: `Failed to generate report: ${error}` },
            { status: 500 }
        )
      )
    )
  ),

  // GET /api/diagnostics/export/json - Export report to JSON
  HttpRouter.get(
    "/api/diagnostics/export/json",
    Effect.gen(function* () {
      const request = yield* HttpServerRequest.HttpServerRequest
      const url = new URL(request.url, "http://localhost")

      const queryParams = yield* Schema.decodeUnknown(DurationQuerySchema)({
        duration_hours: url.searchParams.get("duration_hours") ?? undefined,
      }).pipe(
        Effect.catchAll(() => Effect.succeed({ duration_hours: undefined }))
      )

      const diagnostics = yield* DiagnosticsService
      const report = yield* diagnostics.generateFullReport(
        queryParams.duration_hours ?? 24
      )
      const jsonExport = diagnostics.exportToJson(report)

      return HttpServerResponse.text(jsonExport, {
        headers: {
          "Content-Type": "application/json",
          "Content-Disposition": `attachment; filename="diagnostics-${new Date().toISOString().slice(0, 10)}.json"`,
        },
      })
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
            { error: `Failed to export JSON: ${error}` },
            { status: 500 }
        )
      )
    )
  ),

  // GET /api/diagnostics/export/csv - Export report to CSV
  HttpRouter.get(
    "/api/diagnostics/export/csv",
    Effect.gen(function* () {
      const request = yield* HttpServerRequest.HttpServerRequest
      const url = new URL(request.url, "http://localhost")

      const queryParams = yield* Schema.decodeUnknown(DurationQuerySchema)({
        duration_hours: url.searchParams.get("duration_hours") ?? undefined,
      }).pipe(
        Effect.catchAll(() => Effect.succeed({ duration_hours: undefined }))
      )

      const diagnostics = yield* DiagnosticsService
      const report = yield* diagnostics.generateFullReport(
        queryParams.duration_hours ?? 24
      )
      const csvExport = diagnostics.exportToCsv(report)

      return HttpServerResponse.text(csvExport, {
        headers: {
          "Content-Type": "text/csv",
          "Content-Disposition": `attachment; filename="diagnostics-${new Date().toISOString().slice(0, 10)}.csv"`,
        },
      })
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
            { error: `Failed to export CSV: ${error}` },
            { status: 500 }
        )
      )
    )
  )
)
