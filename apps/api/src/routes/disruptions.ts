/**
 * Disruption routes - consolidated endpoints for disruption events.
 *
 * Endpoints:
 * - GET /api/disruptions - Disruption events
 */

import { HttpRouter, HttpServerRequest, HttpServerResponse } from "@effect/platform"
import { Effect, Schema } from "effect"
import { SignalRepository } from "../services/SignalRepository.js"

// Query params for disruptions endpoint
const DisruptionsQuerySchema = Schema.Struct({
  hours: Schema.optional(Schema.NumberFromString),
})

/**
 * Disruption routes
 */
export const DisruptionRoutes = HttpRouter.empty.pipe(
  // GET /api/disruptions - Disruption events
  HttpRouter.get(
    "/api/disruptions",
    Effect.gen(function* () {
      const request = yield* HttpServerRequest.HttpServerRequest
      const url = new URL(request.url, "http://localhost")

      const queryParams = yield* Schema.decodeUnknown(DisruptionsQuerySchema)({
        hours: url.searchParams.get("hours") ?? undefined,
      }).pipe(
        Effect.catchAll(() => Effect.succeed({ hours: undefined }))
      )

      const durationHours = queryParams.hours ?? 24

      const repo = yield* SignalRepository
      const disruptions = yield* repo.queryDisruptions(durationHours)
      const stats = yield* repo.getDisruptionStats(durationHours)

      return yield* HttpServerResponse.json({
        period_hours: durationHours,
        count: disruptions.length,
        stats,
        data: disruptions,
      })
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
          { error: `Failed to get disruptions: ${error}` },
          { status: 500 }
        )
      )
    )
  )
)
