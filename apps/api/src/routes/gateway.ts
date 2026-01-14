/**
 * Gateway routes - consolidated endpoints for gateway status.
 *
 * Endpoints:
 * - GET /api/gateway/status - Gateway connectivity status
 */

import { HttpRouter, HttpServerResponse } from "@effect/platform"
import { Effect } from "effect"
import { GatewayServiceTag } from "../services/GatewayService.js"

/**
 * Gateway routes
 */
export const GatewayRoutes = HttpRouter.empty.pipe(
  // GET /api/gateway/status - Gateway connectivity status
  HttpRouter.get(
    "/api/gateway/status",
    Effect.gen(function* () {
      const gateway = yield* GatewayServiceTag
      const stats = yield* gateway.getStats()
      const currentData = yield* gateway.getCurrentData()

      // Determine overall status
      let status: "connected" | "degraded" | "disconnected"
      if (stats.circuit_state === "open") {
        status = "disconnected"
      } else if (stats.circuit_state === "half_open") {
        status = "degraded"
      } else if (stats.error_count > 0 && stats.success_count === 0) {
        status = "disconnected"
      } else {
        status = "connected"
      }

      return yield* HttpServerResponse.json({
        status,
        is_running: stats.is_running,
        circuit_state: stats.circuit_state,
        last_success: stats.last_success > 0
          ? new Date(stats.last_success).toISOString()
          : null,
        last_attempt: stats.last_attempt > 0
          ? new Date(stats.last_attempt).toISOString()
          : null,
        success_count: stats.success_count,
        error_count: stats.error_count,
        last_error: stats.last_error,
        has_data: currentData !== null,
        connection_mode: currentData?.connection_mode ?? null,
        device_uptime: currentData?.device_uptime ?? null,
      })
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
          { error: `Failed to get gateway status: ${error}` },
          { status: 500 }
        )
      )
    )
  )
)
