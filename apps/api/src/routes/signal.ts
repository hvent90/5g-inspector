/**
 * Signal routes - consolidated endpoints for signal data.
 *
 * Endpoints:
 * - GET /api/signal - Current signal metrics
 * - GET /api/signal/history - Signal history with time range
 */

import { HttpRouter, HttpServerRequest, HttpServerResponse } from "@effect/platform"
import { Effect, Schema } from "effect"
import { SignalRepository } from "../services/SignalRepository.js"
import { GatewayServiceTag } from "../services/GatewayService.js"
import type { SignalHistoryRecord } from "../schema/Signal.js"

/**
 * Transform flat DB record to nested frontend format.
 * Frontend expects: { nr: { sinr, rsrp, ... }, lte: { sinr, rsrp, ... } }
 * DB returns: { nr_sinr, nr_rsrp, lte_sinr, lte_rsrp, ... }
 */
function transformToFrontendFormat(record: SignalHistoryRecord) {
  return {
    timestamp: record.timestamp,
    connection_mode: record.registration_status ?? "CONNECTED",
    nr: {
      sinr: record.nr_sinr ?? null,
      rsrp: record.nr_rsrp ?? null,
      rsrq: record.nr_rsrq ?? null,
      rssi: record.nr_rssi ?? null,
      bands: record.nr_bands ? JSON.parse(record.nr_bands) : [],
    },
    lte: {
      sinr: record.lte_sinr ?? null,
      rsrp: record.lte_rsrp ?? null,
      rsrq: record.lte_rsrq ?? null,
      rssi: record.lte_rssi ?? null,
      bands: record.lte_bands ? JSON.parse(record.lte_bands) : [],
    },
  }
}

// Query params for history endpoint
const HistoryQuerySchema = Schema.Struct({
  duration_minutes: Schema.optional(Schema.NumberFromString),
  resolution: Schema.optional(Schema.String),
  limit: Schema.optional(Schema.NumberFromString),
})

/**
 * Signal routes
 */
export const SignalRoutes = HttpRouter.empty.pipe(
  // GET /api/signal - Current signal metrics
  HttpRouter.get(
    "/api/signal",
    Effect.gen(function* () {
      const gateway = yield* GatewayServiceTag
      const currentData = yield* gateway.getCurrentData()

      if (currentData === null) {
        // Try to get from repository if gateway hasn't polled yet
        const repo = yield* SignalRepository
        const latest = yield* repo.getLatestSignal().pipe(
          Effect.catchAll(() => Effect.succeed(null))
        )

        if (latest === null) {
          return yield* HttpServerResponse.json(
            { error: "No signal data available" },
            { status: 503 }
          )
        }

        return yield* HttpServerResponse.json(transformToFrontendFormat(latest))
      }

      return yield* HttpServerResponse.json(currentData)
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
          { error: `Failed to get signal: ${error}` },
          { status: 500 }
        )
      )
    )
  ),

  // GET /api/signal/history - Signal history with time range
  HttpRouter.get(
    "/api/signal/history",
    Effect.gen(function* () {
      const request = yield* HttpServerRequest.HttpServerRequest
      const url = new URL(request.url, "http://localhost")

      const queryParams = yield* Schema.decodeUnknown(HistoryQuerySchema)({
        duration_minutes: url.searchParams.get("duration_minutes") ?? undefined,
        resolution: url.searchParams.get("resolution") ?? undefined,
        limit: url.searchParams.get("limit") ?? undefined,
      }).pipe(
        Effect.catchAll(() =>
          Effect.succeed({
            duration_minutes: undefined,
            resolution: undefined,
            limit: undefined,
          })
        )
      )

      const repo = yield* SignalRepository
      const history = yield* repo.querySignalHistory({
        duration_minutes: queryParams.duration_minutes ?? 60,
        resolution: queryParams.resolution ?? "auto",
        limit: queryParams.limit ?? undefined,
      })

      return yield* HttpServerResponse.json({
        count: history.length,
        duration_minutes: queryParams.duration_minutes ?? 60,
        resolution: queryParams.resolution ?? "auto",
        data: history,
      })
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
          { error: `Failed to get signal history: ${error}` },
          { status: 500 }
        )
      )
    )
  )
)
