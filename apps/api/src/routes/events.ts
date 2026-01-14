/**
 * Events routes - Server-Sent Events for real-time updates.
 *
 * Endpoints:
 * - GET /api/events - Real-time event stream (SSE)
 */

import { HttpRouter, HttpServerResponse } from "@effect/platform"
import { Effect, Stream } from "effect"
import { GatewayServiceTag, type SignalData, type OutageEvent } from "../services/GatewayService.js"
import { AlertService } from "../services/AlertService.js"

// SSE event types
type SSEEvent =
  | { type: "signal"; data: SignalData }
  | { type: "outage"; data: OutageEvent }
  | { type: "alert"; data: unknown }
  | { type: "heartbeat"; data: { timestamp: string } }

/**
 * Format an event for SSE transmission
 */
const formatSSE = (event: SSEEvent): string => {
  const data = JSON.stringify(event.data)
  return `event: ${event.type}\ndata: ${data}\n\n`
}

/**
 * Events routes
 */
export const EventsRoutes = HttpRouter.empty.pipe(
  // GET /api/events - Real-time event stream (SSE)
  HttpRouter.get(
    "/api/events",
    Effect.gen(function* () {
      const gateway = yield* GatewayServiceTag
      const alertService = yield* AlertService

      // Create merged stream of all event sources
      const signalStream = gateway.subscribe().pipe(
        Stream.map((data): SSEEvent => ({ type: "signal", data }))
      )

      const outageStream = gateway.subscribeOutages().pipe(
        Stream.map((data): SSEEvent => ({ type: "outage", data }))
      )

      const alertStream = alertService.subscribe().pipe(
        Stream.map((event): SSEEvent => ({ type: "alert", data: event }))
      )

      // Merge all streams
      const mergedStream = Stream.mergeAll([signalStream, outageStream, alertStream], {
        concurrency: 3,
      })

      // Convert to SSE format
      const sseStream = mergedStream.pipe(
        Stream.map(formatSSE),
        Stream.encodeText
      )

      // Create streaming response
      return HttpServerResponse.stream(sseStream, {
        contentType: "text/event-stream",
        headers: {
          "Cache-Control": "no-cache",
          "Connection": "keep-alive",
          "X-Accel-Buffering": "no",
        },
      })
    }).pipe(
      Effect.catchAll((error) =>
        Effect.succeed(
          HttpServerResponse.json(
            { error: `Failed to create event stream: ${error}` },
            { status: 500 }
          )
        )
      )
    )
  )
)
