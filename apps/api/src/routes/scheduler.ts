/**
 * Scheduler routes - endpoints for speedtest scheduler management.
 *
 * Endpoints:
 * - GET /api/scheduler/config - Get scheduler configuration
 * - PUT /api/scheduler/config - Update scheduler configuration
 * - GET /api/scheduler/stats - Get scheduler statistics
 * - POST /api/scheduler/start - Start the scheduler
 * - POST /api/scheduler/stop - Stop the scheduler
 */

import { HttpRouter, HttpServerRequest, HttpServerResponse } from "@effect/platform"
import { Effect, Schema } from "effect"
import { SchedulerService, SchedulerError } from "../services/SchedulerService.js"

// Schema for config updates
const SchedulerConfigUpdateSchema = Schema.Struct({
  enabled: Schema.optional(Schema.Boolean),
  interval_minutes: Schema.optional(Schema.Number),
  time_window_start: Schema.optional(Schema.Number),
  time_window_end: Schema.optional(Schema.Number),
  run_on_weekends: Schema.optional(Schema.Boolean),
})

/**
 * Scheduler routes
 */
export const SchedulerRoutes = HttpRouter.empty.pipe(
  // GET /api/scheduler/config - Get scheduler configuration
  HttpRouter.get(
    "/api/scheduler/config",
    Effect.gen(function* () {
      const service = yield* SchedulerService
      const config = yield* service.getConfig()

      return yield* HttpServerResponse.json(config)
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
          { error: `Failed to get scheduler config: ${error}` },
          { status: 500 }
        )
      )
    )
  ),

  // PUT /api/scheduler/config - Update scheduler configuration
  HttpRouter.put(
    "/api/scheduler/config",
    Effect.gen(function* () {
      // Use schemaBodyJson to properly parse and validate request body
      const updates = yield* HttpServerRequest.schemaBodyJson(SchedulerConfigUpdateSchema).pipe(
        Effect.catchAll(() => Effect.succeed({
          enabled: undefined,
          interval_minutes: undefined,
          time_window_start: undefined,
          time_window_end: undefined,
          run_on_weekends: undefined,
        }))
      )

      const service = yield* SchedulerService
      const config = yield* service.updateConfig(updates)

      return yield* HttpServerResponse.json(config)
    }).pipe(
      Effect.catchAll((error) => {
        if (error instanceof SchedulerError) {
          return HttpServerResponse.json(
            { error: error.message, type: error.type },
            { status: 400 }
          )
        }
        return HttpServerResponse.json(
          { error: `Failed to update scheduler config: ${error}` },
          { status: 500 }
        )
      })
    )
  ),

  // GET /api/scheduler/stats - Get scheduler statistics
  HttpRouter.get(
    "/api/scheduler/stats",
    Effect.gen(function* () {
      const service = yield* SchedulerService
      const stats = yield* service.getStats()

      return yield* HttpServerResponse.json(stats)
    }).pipe(
      Effect.catchAll((error) =>
        HttpServerResponse.json(
          { error: `Failed to get scheduler stats: ${error}` },
          { status: 500 }
        )
      )
    )
  ),

  // POST /api/scheduler/start - Start the scheduler
  HttpRouter.post(
    "/api/scheduler/start",
    Effect.gen(function* () {
      const service = yield* SchedulerService
      yield* service.start()

      return yield* HttpServerResponse.json({
        success: true,
        message: "Scheduler started",
      })
    }).pipe(
      Effect.catchAll((error) => {
        if (error instanceof SchedulerError) {
          const status = error.type === "already_running" ? 409 : 500
          return HttpServerResponse.json(
            { error: error.message, type: error.type },
            { status }
          )
        }
        return HttpServerResponse.json(
          { error: `Failed to start scheduler: ${error}` },
          { status: 500 }
        )
      })
    )
  ),

  // POST /api/scheduler/stop - Stop the scheduler
  HttpRouter.post(
    "/api/scheduler/stop",
    Effect.gen(function* () {
      const service = yield* SchedulerService
      yield* service.stop()

      return yield* HttpServerResponse.json({
        success: true,
        message: "Scheduler stopped",
      })
    }).pipe(
      Effect.catchAll((error) => {
        if (error instanceof SchedulerError) {
          const status = error.type === "not_running" ? 409 : 500
          return HttpServerResponse.json(
            { error: error.message, type: error.type },
            { status }
          )
        }
        return HttpServerResponse.json(
          { error: `Failed to stop scheduler: ${error}` },
          { status: 500 }
        )
      })
    )
  )
)
