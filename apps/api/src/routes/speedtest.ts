/**
 * Speedtest routes - consolidated endpoints for speed tests.
 *
 * Endpoints:
 * - GET /api/speedtest/history - Recent speedtest results ({results} format, frontend-compatible)
 * - GET /api/speedtest/tools - Available speedtest tools
 * - GET /api/speedtest/status - Current speedtest status
 * - GET /api/speedtest - Recent speedtest results ({count, data} format)
 * - POST /api/speedtest - Trigger a new speedtest
 */

import { HttpRouter, HttpServerRequest, HttpServerResponse } from "@effect/platform"
import { Effect, Schema } from "effect"
import { SpeedtestService, SpeedtestError } from "../services/SpeedtestService.js"
import type { NetworkContext } from "../schema/Signal.js"

// Query params for GET endpoint
const SpeedtestQuerySchema = Schema.Struct({
  limit: Schema.optional(Schema.NumberFromString),
})

// Request body for POST endpoint
const TriggerSpeedtestSchema = Schema.Struct({
  server_id: Schema.optional(Schema.Number),
  tool: Schema.optional(Schema.String),
  triggered_by: Schema.optional(Schema.String),
  network_context: Schema.optional(Schema.String),
  enable_latency_probe: Schema.optional(Schema.Boolean),
})

/**
 * Speedtest routes
 *
 * NOTE: More specific routes (/history, /tools, /status) must be defined BEFORE
 * the base /api/speedtest route to ensure correct matching.
 */
export const SpeedtestRoutes = HttpRouter.empty.pipe(
  // GET /api/speedtest/history - Recent speedtest results (frontend-compatible format)
  HttpRouter.get(
    "/api/speedtest/history",
    Effect.gen(function* () {
      const request = yield* HttpServerRequest.HttpServerRequest
      const url = new URL(request.url, "http://localhost")

      const queryParams = yield* Schema.decodeUnknown(SpeedtestQuerySchema)({
        limit: url.searchParams.get("limit") ?? undefined,
      }).pipe(
        Effect.catchAll(() => Effect.succeed({ limit: undefined }))
      )

      const service = yield* SpeedtestService
      const results = yield* service.getHistory(queryParams.limit ?? 10)

      return yield* HttpServerResponse.json({
        results: results,
      })
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
          { error: `Failed to get speedtest history: ${error}` },
          { status: 500 }
        )
      )
    )
  ),

  // GET /api/speedtest/tools - Available speedtest tools
  HttpRouter.get(
    "/api/speedtest/tools",
    Effect.gen(function* () {
      const service = yield* SpeedtestService
      const toolInfo = yield* service.getToolInfo()

      return yield* HttpServerResponse.json(toolInfo)
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
          { error: `Failed to get tool info: ${error}` },
          { status: 500 }
        )
      )
    )
  ),

  // GET /api/speedtest/status - Current speedtest status
  HttpRouter.get(
    "/api/speedtest/status",
    Effect.gen(function* () {
      const service = yield* SpeedtestService
      const isRunning = yield* service.isRunning()
      const lastResult = yield* service.getLastResult()

      return yield* HttpServerResponse.json({
        is_running: isRunning,
        last_result: lastResult,
      })
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
          { error: `Failed to get status: ${error}` },
          { status: 500 }
        )
      )
    )
  ),

  // GET /api/speedtest - Recent speedtest results
  HttpRouter.get(
    "/api/speedtest",
    Effect.gen(function* () {
      const request = yield* HttpServerRequest.HttpServerRequest
      const url = new URL(request.url, "http://localhost")

      const queryParams = yield* Schema.decodeUnknown(SpeedtestQuerySchema)({
        limit: url.searchParams.get("limit") ?? undefined,
      }).pipe(
        Effect.catchAll(() => Effect.succeed({ limit: undefined }))
      )

      const service = yield* SpeedtestService
      const results = yield* service.getHistory(queryParams.limit ?? 10)

      return yield* HttpServerResponse.json({
        count: results.length,
        data: results,
      })
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
          { error: `Failed to get speedtest results: ${error}` },
          { status: 500 }
        )
      )
    )
  ),

  // POST /api/speedtest - Trigger a new speedtest
  HttpRouter.post(
    "/api/speedtest",
    Effect.gen(function* () {
      const request = yield* HttpServerRequest.HttpServerRequest
      const url = new URL(request.url, "http://localhost")

      // Parse query params (frontend sends tool as query param)
      const queryTool = url.searchParams.get("tool") ?? undefined
      const queryTriggeredBy = url.searchParams.get("triggered_by") ?? undefined

      const service = yield* SpeedtestService

      // Run the actual speedtest with query params
      const result = yield* service.runSpeedtest({
        tool: queryTool,
        triggeredBy: queryTriggeredBy ?? "api",
      })

      // Return appropriate status based on result
      const httpStatus = result.status === "success" ? 200 :
                         result.status === "busy" ? 409 :
                         result.status === "error" ? 500 : 504

      return yield* HttpServerResponse.json({
        status: result.status,
        download_mbps: result.download_mbps,
        upload_mbps: result.upload_mbps,
        ping_ms: result.ping_ms,
        jitter_ms: result.jitter_ms,
        server_name: result.server_name,
        server_location: result.server_location,
        tool: result.tool,
        result_url: result.result_url,
        network_context: result.network_context,
        pre_test_latency_ms: result.pre_test_latency_ms,
        triggered_by: result.triggered_by,
        timestamp: result.timestamp.toISOString(),
        error_message: result.error_message,
      }, { status: httpStatus })
    }).pipe(
      Effect.catchAll((error) => {
        if (error instanceof SpeedtestError) {
          const status = error.type === "no_tool" ? 503 :
                        error.type === "busy" ? 409 :
                        error.type === "timeout" ? 504 : 500
          return HttpServerResponse.json(
            { error: error.message, type: error.type },
            { status }
          )
        }
        const errorMessage = error instanceof Error ? error.message :
                            typeof error === "object" && error !== null ? JSON.stringify(error) :
                            String(error)
        return HttpServerResponse.json(
          { error: `Failed to run speedtest: ${errorMessage}` },
          { status: 500 }
        )
      })
    )
  )
)
