/**
 * Network Quality routes - endpoints for network quality monitoring.
 *
 * Endpoints:
 * - GET /api/network-quality/config - Get monitoring configuration
 * - GET /api/network-quality/stats - Get monitoring status and stats
 * - GET /api/network-quality - Get recent test results
 * - POST /api/network-quality/start - Start monitoring
 * - POST /api/network-quality/stop - Stop monitoring
 * - POST /api/network-quality/trigger - Trigger immediate test
 */

import { HttpRouter, HttpServerRequest, HttpServerResponse } from "@effect/platform"
import { Effect, Schema } from "effect"
import { NetworkQualityService, NetworkQualityError } from "../services/NetworkQualityService.js"
import { RepositoryError } from "../services/SignalRepository.js"

// Query params for GET /api/network-quality
const NetworkQualityQuerySchema = Schema.Struct({
  limit: Schema.optional(Schema.NumberFromString),
})

/**
 * Network Quality routes
 */
export const NetworkQualityRoutes = HttpRouter.empty.pipe(
  // GET /api/network-quality/config - Get configuration
  HttpRouter.get(
    "/api/network-quality/config",
    Effect.gen(function* () {
      const service = yield* NetworkQualityService
      const config = yield* service.getConfig()

      return yield* HttpServerResponse.json({
        enabled: config.enabled,
        interval_minutes: config.interval_minutes,
        min_interval_minutes: config.min_interval_minutes,
        max_interval_minutes: config.max_interval_minutes,
        ping_count: config.ping_count,
        targets: config.targets,
        packet_loss_threshold_percent: config.packet_loss_threshold_percent,
        jitter_threshold_ms: config.jitter_threshold_ms,
      })
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
          { error: `Failed to get config: ${error}` },
          { status: 500 }
        )
      )
    )
  ),

  // GET /api/network-quality/stats - Get monitoring stats
  HttpRouter.get(
    "/api/network-quality/stats",
    Effect.gen(function* () {
      const service = yield* NetworkQualityService
      const stats = yield* service.getStats()

      return yield* HttpServerResponse.json({
        is_running: stats.is_running,
        tests_completed: stats.tests_completed,
        last_test_time: stats.last_test_time,
        next_test_time: stats.next_test_time,
        next_test_in_seconds: stats.next_test_in_seconds,
      })
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
          { error: `Failed to get stats: ${error}` },
          { status: 500 }
        )
      )
    )
  ),

  // GET /api/network-quality - Get recent results
  HttpRouter.get(
    "/api/network-quality",
    Effect.gen(function* () {
      const request = yield* HttpServerRequest.HttpServerRequest
      const url = new URL(request.url, "http://localhost")

      const queryParams = yield* Schema.decodeUnknown(NetworkQualityQuerySchema)({
        limit: url.searchParams.get("limit") ?? undefined,
      }).pipe(Effect.catchAll(() => Effect.succeed({ limit: undefined })))

      const service = yield* NetworkQualityService
      const results = yield* service.getResults(queryParams.limit ?? 100)

      return yield* HttpServerResponse.json({
        count: results.length,
        results: results,
      })
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
          { error: `Failed to get results: ${error}` },
          { status: 500 }
        )
      )
    )
  ),

  // POST /api/network-quality/start - Start monitoring
  HttpRouter.post(
    "/api/network-quality/start",
    Effect.gen(function* () {
      const service = yield* NetworkQualityService
      yield* service.start()

      const stats = yield* service.getStats()

      return yield* HttpServerResponse.json({
        status: "started",
        is_running: stats.is_running,
        next_test_in_seconds: stats.next_test_in_seconds,
      })
    }).pipe(
      Effect.catchAll((error) => {
        if (error instanceof NetworkQualityError) {
          return HttpServerResponse.json(
            { error: error.message, type: error.type },
            { status: 500 }
          )
        }
        return HttpServerResponse.json(
          { error: `Failed to start monitoring: ${error}` },
          { status: 500 }
        )
      })
    )
  ),

  // POST /api/network-quality/stop - Stop monitoring
  HttpRouter.post(
    "/api/network-quality/stop",
    Effect.gen(function* () {
      const service = yield* NetworkQualityService
      yield* service.stop()

      const stats = yield* service.getStats()

      return yield* HttpServerResponse.json({
        status: "stopped",
        is_running: stats.is_running,
      })
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
          { error: `Failed to stop monitoring: ${error}` },
          { status: 500 }
        )
      )
    )
  ),

  // POST /api/network-quality/trigger - Trigger immediate test
  HttpRouter.post(
    "/api/network-quality/trigger",
    Effect.gen(function* () {
      const service = yield* NetworkQualityService
      const results = yield* service.trigger()

      return yield* HttpServerResponse.json({
        status: "completed",
        count: results.length,
        results: results.map((r) => ({
          target_host: r.target_host,
          target_name: r.target_name,
          latency_avg: r.ping_ms,
          jitter_ms: r.jitter_ms,
          packet_loss_percent: r.packet_loss_percent,
          status: r.status,
          timestamp: r.timestamp,
        })),
      })
    }).pipe(
      Effect.catchAll((error) => {
        if (error instanceof NetworkQualityError) {
          const status = error.type === "timeout" ? 504 : 500
          return HttpServerResponse.json(
            { error: error.message, type: error.type },
            { status }
          )
        }
        if (error instanceof RepositoryError) {
          return HttpServerResponse.json(
            { error: error.message },
            { status: 500 }
          )
        }
        return HttpServerResponse.json(
          { error: `Failed to trigger test: ${error}` },
          { status: 500 }
        )
      })
    )
  )
)
