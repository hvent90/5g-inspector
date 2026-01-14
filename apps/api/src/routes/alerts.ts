/**
 * Alert routes - consolidated endpoints for alert management.
 *
 * Endpoints:
 * - GET /api/alerts - Active alerts
 * - GET /api/alerts/history - Alert history
 */

import { HttpRouter, HttpServerRequest, HttpServerResponse } from "@effect/platform"
import { Effect, Schema } from "effect"
import { AlertService } from "../services/AlertService.js"

// Query params for history endpoint
const AlertHistoryQuerySchema = Schema.Struct({
  limit: Schema.optional(Schema.NumberFromString),
  offset: Schema.optional(Schema.NumberFromString),
})

/**
 * Alert routes
 */
export const AlertRoutes = HttpRouter.empty.pipe(
  // GET /api/alerts - Active alerts
  HttpRouter.get(
    "/api/alerts",
    Effect.gen(function* () {
      const alertService = yield* AlertService
      const activeAlerts = yield* alertService.getActiveAlerts()
      const config = yield* alertService.getConfig()

      return yield* HttpServerResponse.json({
        count: activeAlerts.length,
        config: {
          enabled: config.enabled,
          notify_on_warning: config.notifyOnWarning,
          notify_on_critical: config.notifyOnCritical,
        },
        data: activeAlerts,
      })
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
          { error: `Failed to get alerts: ${error}` },
          { status: 500 }
        )
      )
    )
  ),

  // GET /api/alerts/history - Alert history
  HttpRouter.get(
    "/api/alerts/history",
    Effect.gen(function* () {
      const request = yield* HttpServerRequest.HttpServerRequest
      const url = new URL(request.url, "http://localhost")

      const queryParams = yield* Schema.decodeUnknown(AlertHistoryQuerySchema)({
        limit: url.searchParams.get("limit") ?? undefined,
        offset: url.searchParams.get("offset") ?? undefined,
      }).pipe(
        Effect.catchAll(() => Effect.succeed({ limit: undefined, offset: undefined }))
      )

      const alertService = yield* AlertService
      const history = yield* alertService.getHistory(
        queryParams.limit ?? 100,
        queryParams.offset ?? 0
      )

      return yield* HttpServerResponse.json({
        count: history.length,
        limit: queryParams.limit ?? 100,
        offset: queryParams.offset ?? 0,
        data: history,
      })
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
          { error: `Failed to get alert history: ${error}` },
          { status: 500 }
        )
      )
    )
  )
)
