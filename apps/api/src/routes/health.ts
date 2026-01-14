/**
 * Health check route handlers.
 *
 * Provides endpoints for monitoring service health:
 * - GET /health - Full health status with component details
 * - GET /health/live - Kubernetes liveness probe
 * - GET /health/ready - Kubernetes readiness probe
 */

import { HttpRouter, HttpServerResponse } from "@effect/platform"
import { Effect, Schema } from "effect"

// Schema for health response
const HealthStatus = Schema.Struct({
  status: Schema.Literal("healthy", "degraded", "unhealthy"),
  uptime_seconds: Schema.Number,
  version: Schema.String,
  timestamp: Schema.String,
})

type HealthStatus = typeof HealthStatus.Type

// Track server start time for uptime calculation
const startTime = Date.now()

// Version from package.json (could be injected via env)
const VERSION = process.env.npm_package_version ?? "1.0.0"

/**
 * Build health status response
 */
const getHealthStatus = (): HealthStatus => ({
  status: "healthy",
  uptime_seconds: Math.floor((Date.now() - startTime) / 1000),
  version: VERSION,
  timestamp: new Date().toISOString(),
})

/**
 * Health routes
 */
export const HealthRoutes = HttpRouter.empty.pipe(
  // GET /health - Full health status
  HttpRouter.get(
    "/health",
    Effect.gen(function* () {
      const status = getHealthStatus()
      return yield* HttpServerResponse.json(status)
    })
  ),

  // GET /health/live - Kubernetes liveness probe
  HttpRouter.get(
    "/health/live",
    Effect.gen(function* () {
      return yield* HttpServerResponse.json({ status: "ok" })
    })
  ),

  // GET /health/ready - Kubernetes readiness probe
  HttpRouter.get(
    "/health/ready",
    Effect.gen(function* () {
      // In a full implementation, this would check database connectivity, etc.
      return yield* HttpServerResponse.json({ status: "ready" })
    })
  )
)
