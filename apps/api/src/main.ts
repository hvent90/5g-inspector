/**
 * Effect-based HTTP API server for NetPulse.
 *
 * This is the main entry point that sets up the HTTP server using Effect Platform.
 * Run with: bun run dev (for development with hot reload)
 *
 * Consolidated API (~29 endpoints):
 * - GET /health - Health check
 * - GET /health/live - Liveness probe
 * - GET /health/ready - Readiness probe
 * - GET /api/signal - Current signal metrics
 * - GET /api/signal/history - Signal history
 * - GET /api/speedtest - Recent speedtest results
 * - GET /api/speedtest/tools - Available speedtest tools
 * - GET /api/speedtest/status - Current speedtest status
 * - POST /api/speedtest - Trigger speedtest
 * - GET /api/scheduler/config - Scheduler configuration
 * - PUT /api/scheduler/config - Update scheduler configuration
 * - GET /api/scheduler/stats - Scheduler statistics
 * - POST /api/scheduler/start - Start scheduler
 * - POST /api/scheduler/stop - Stop scheduler
 * - GET /api/disruptions - Disruption events
 * - GET /api/alerts - Active alerts
 * - GET /api/alerts/history - Alert history
 * - GET /api/gateway/status - Gateway status
 * - GET /api/diagnostics - System diagnostics
 * - GET /api/diagnostics/summary - Signal metrics summary
 * - GET /api/diagnostics/disruptions - Disruption detection
 * - GET /api/diagnostics/patterns - Time of day patterns
 * - GET /api/diagnostics/towers - Tower connection history
 * - GET /api/diagnostics/report - Full diagnostic report
 * - GET /api/diagnostics/export/json - Export to JSON
 * - GET /api/diagnostics/export/csv - Export to CSV
 * - GET /api/congestion-proof - Congestion proof analysis
 * - GET /api/events - SSE event stream
 */

import {
  FetchHttpClient,
  HttpRouter,
  HttpServer,
  HttpServerRequest,
  HttpServerResponse,
} from "@effect/platform"
import { BunHttpServer, BunRuntime } from "@effect/platform-bun"
import { Effect, Layer, pipe, Schedule } from "effect"

import {
  HealthRoutes,
  SignalRoutes,
  SpeedtestRoutes,
  SchedulerRoutes,
  NetworkQualityRoutes,
  DisruptionRoutes,
  AlertRoutes,
  GatewayRoutes,
  DiagnosticsRoutes,
  CongestionRoutes,
  EventsRoutes,
} from "./routes/index.js"
import { GatewayServiceTag, GatewayServiceLive } from "./services/GatewayService.js"
import { GatewayConfigLive } from "./config/GatewayConfig.js"
import {
  SignalRepository,
  SignalRepositoryLive,
  makeSqliteConnectionLayer,
} from "./services/SignalRepository.js"
import { AlertService, AlertServiceLive } from "./services/AlertService.js"
import { SpeedtestService, SpeedtestServiceLive } from "./services/SpeedtestService.js"
import { SchedulerService, SchedulerServiceLive } from "./services/SchedulerService.js"
import { DiagnosticsService, DiagnosticsServiceLive } from "./services/DiagnosticsService.js"
import { NetworkQualityService, NetworkQualityServiceLive } from "./services/NetworkQualityService.js"

// Server configuration
const PORT = Number(process.env.PORT ?? 3001)
const DB_PATH = process.env.DB_PATH ?? "signal_history.db"

// Database and repository layers
const SqliteLayer = makeSqliteConnectionLayer(DB_PATH)
const RepositoryLayer = Layer.provide(SignalRepositoryLive, SqliteLayer)

// Gateway service layer with all dependencies
const GatewayLayer = GatewayServiceLive.pipe(
  Layer.provide(GatewayConfigLive),
  Layer.provide(FetchHttpClient.layer),
  Layer.provide(RepositoryLayer)
)

// Alert service layer
const AlertLayer = Layer.provide(AlertServiceLive, RepositoryLayer)

// Speedtest service layer
const SpeedtestLayer = Layer.provide(SpeedtestServiceLive, RepositoryLayer)

// Scheduler service layer (depends on SpeedtestService)
const SchedulerLayer = Layer.provide(SchedulerServiceLive, SpeedtestLayer)

// Diagnostics service layer
const DiagnosticsLayer = Layer.provide(DiagnosticsServiceLive, RepositoryLayer)

// Network Quality service layer
const NetworkQualityLayer = Layer.provide(NetworkQualityServiceLive, SqliteLayer)

// Combined service layer
const ServicesLayer = Layer.mergeAll(
  GatewayLayer,
  RepositoryLayer,
  AlertLayer,
  SpeedtestLayer,
  SchedulerLayer,
  DiagnosticsLayer,
  NetworkQualityLayer
)

// Combine all route handlers
const router = HttpRouter.empty.pipe(
  // Health routes at root
  HttpRouter.mount("/", HealthRoutes),
  // API routes
  HttpRouter.mount("/", SignalRoutes),
  HttpRouter.mount("/", SpeedtestRoutes),
  HttpRouter.mount("/", SchedulerRoutes),
  HttpRouter.mount("/", NetworkQualityRoutes),
  HttpRouter.mount("/", DisruptionRoutes),
  HttpRouter.mount("/", AlertRoutes),
  HttpRouter.mount("/", GatewayRoutes),
  HttpRouter.mount("/", DiagnosticsRoutes),
  HttpRouter.mount("/", CongestionRoutes),
  HttpRouter.mount("/", EventsRoutes),
  // Provide services to all route handlers
  HttpRouter.use((httpApp) =>
    Effect.gen(function* () {
      const request = yield* HttpServerRequest.HttpServerRequest

      // Handle preflight OPTIONS requests before providing services
      if (request.method === "OPTIONS") {
        return HttpServerResponse.empty({
          status: 204,
          headers: {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Max-Age": "86400",
          },
        })
      }

      // Run the handler (services are provided at the app layer level)
      const response = yield* httpApp

      // Add CORS headers to all responses
      return HttpServerResponse.setHeaders(response, {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
      })
    })
  )
)

// Create the HTTP application
const app = router.pipe(HttpServer.serve())

// Server layer with Bun runtime
const ServerLive = BunHttpServer.layer({ port: PORT })

// Program to start gateway polling
const startGatewayPolling = Effect.gen(function* () {
  const gatewayService = yield* GatewayServiceTag
  yield* gatewayService.startPolling()

  // Log stats periodically (every 30 seconds)
  yield* Effect.fork(
    Effect.gen(function* () {
      const stats = yield* gatewayService.getStats()
      yield* Effect.log(
        `Gateway stats: ${stats.success_count} polls, ${stats.error_count} errors, circuit: ${stats.circuit_state}`
      )
    }).pipe(
      Effect.repeat(Schedule.spaced(30000)),
      Effect.forever,
      Effect.catchAll(() => Effect.void)
    )
  )
})

// Main program
const main = Effect.gen(function* () {
  // Log startup info
  yield* Effect.log(`NetPulse API server starting on http://localhost:${PORT}`)
  yield* Effect.all([
    Effect.log("Consolidated API endpoints:"),
    Effect.log("  GET  /health, /api/signal, /api/signal/history"),
    Effect.log("  GET  /api/speedtest, /api/speedtest/tools, /api/speedtest/status"),
    Effect.log("  POST /api/speedtest"),
    Effect.log("  GET  /api/disruptions, /api/alerts, /api/alerts/history"),
    Effect.log("  GET  /api/gateway/status, /api/diagnostics, /api/events"),
  ])

  // Start gateway polling
  yield* startGatewayPolling
  yield* Effect.log("Gateway polling started")

  // Launch the server (this runs forever)
  // ServicesLayer is provided to the app layer for HTTP handlers
  yield* pipe(
    app,
    Layer.provideMerge(ServicesLayer),
    Layer.provide(ServerLive),
    Layer.launch
  )
}).pipe(Effect.provide(ServicesLayer))

// Run with Bun runtime
BunRuntime.runMain(main)
