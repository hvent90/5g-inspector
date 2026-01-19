/**
 * Gateway polling service using Effect
 * Polls the T-Mobile gateway at 192.168.12.1 for signal data
 * Implements circuit breaker pattern and writes to SQLite via SignalRepository
 */
import {
  Context,
  Effect,
  Layer,
  Schedule,
  Fiber,
  Ref,
  Stream,
  PubSub,
  Duration,
  Schema,
  Queue,
} from "effect"
import { HttpClient, HttpClientRequest, HttpClientResponse } from "@effect/platform"
import { SignalHistoryInsert, type ConnectionMode } from "../schema/Signal"
import { GatewayConfigService, type GatewayConfig } from "../config/GatewayConfig"
import { SignalRepository, type RepositoryError } from "./SignalRepository"

// ============================================
// Types
// ============================================

export type CircuitState = "closed" | "open" | "half_open"

// ============================================
// Gateway Response Schema
// ============================================

/**
 * Schema for parsing the raw T-Mobile gateway JSON response
 * Expected structure: { signal: { "5g": {...}, "4g": {...} }, device: {...} }
 */
const GatewayResponseSchema = Schema.Struct({
  signal: Schema.Struct({
    "5g": Schema.optional(
      Schema.Struct({
        sinr: Schema.optional(Schema.Union(Schema.Number, Schema.String, Schema.Null)),
        rsrp: Schema.optional(Schema.Union(Schema.Number, Schema.String, Schema.Null)),
        rsrq: Schema.optional(Schema.Union(Schema.Number, Schema.String, Schema.Null)),
        rssi: Schema.optional(Schema.Union(Schema.Number, Schema.String, Schema.Null)),
        bands: Schema.optional(Schema.Array(Schema.String)),
        gNBID: Schema.optional(Schema.Union(Schema.Number, Schema.Null)),
        cid: Schema.optional(Schema.Union(Schema.Number, Schema.Null)),
      })
    ),
    "4g": Schema.optional(
      Schema.Struct({
        sinr: Schema.optional(Schema.Union(Schema.Number, Schema.String, Schema.Null)),
        rsrp: Schema.optional(Schema.Union(Schema.Number, Schema.String, Schema.Null)),
        rsrq: Schema.optional(Schema.Union(Schema.Number, Schema.String, Schema.Null)),
        rssi: Schema.optional(Schema.Union(Schema.Number, Schema.String, Schema.Null)),
        bands: Schema.optional(Schema.Array(Schema.String)),
        eNBID: Schema.optional(Schema.Union(Schema.Number, Schema.Null)),
        cid: Schema.optional(Schema.Union(Schema.Number, Schema.Null)),
      })
    ),
  }),
  device: Schema.optional(
    Schema.Struct({
      connectionStatus: Schema.optional(Schema.String),
      deviceUptime: Schema.optional(Schema.Number),
    })
  ),
})

type GatewayResponse = typeof GatewayResponseSchema.Type

// ============================================
// Error Types
// ============================================

export class GatewayError {
  readonly _tag = "GatewayError"
  constructor(
    readonly type: "timeout" | "http_error" | "parse_error" | "connection_refused" | "unknown",
    readonly message: string,
    readonly cause?: unknown
  ) {}
}

export class CircuitOpenError {
  readonly _tag = "CircuitOpenError"
  constructor(readonly recoveryTimeRemaining: number) {}
}

// ============================================
// Signal Data Types
// ============================================

export interface SignalMetrics {
  readonly sinr: number | null
  readonly rsrp: number | null
  readonly rsrq: number | null
  readonly rssi: number | null
  readonly bands: readonly string[]
  readonly tower_id: number | null
  readonly cell_id: number | null
}

export interface SignalData {
  readonly timestamp: Date
  readonly timestamp_unix: number
  readonly nr: SignalMetrics
  readonly lte: SignalMetrics
  readonly registration_status: string | null
  readonly connection_mode: ConnectionMode
  readonly device_uptime: number | null
}

export interface OutageEvent {
  readonly start_time: number
  readonly end_time: number | null
  readonly duration_seconds: number | null
  readonly error_count: number
  readonly last_error: string | null
  readonly resolved: boolean
}

export interface GatewayStats {
  readonly last_success: number
  readonly last_attempt: number
  readonly success_count: number
  readonly error_count: number
  readonly last_error: string | null
  readonly circuit_state: CircuitState
  readonly is_running: boolean
}

// ============================================
// Circuit Breaker
// ============================================

interface CircuitBreakerState {
  state: CircuitState
  failureCount: number
  lastFailureTime: number | null
}

const makeCircuitBreaker = (failureThreshold: number, recoveryTimeout: number) =>
  Effect.gen(function* () {
    const stateRef = yield* Ref.make<CircuitBreakerState>({
      state: "closed",
      failureCount: 0,
      lastFailureTime: null,
    })

    const recordSuccess = Ref.update(stateRef, () => ({
      state: "closed" as CircuitState,
      failureCount: 0,
      lastFailureTime: null,
    }))

    const recordFailure = Ref.update(stateRef, (s) => {
      const newFailureCount = s.failureCount + 1
      const now = Date.now()
      return {
        state: newFailureCount >= failureThreshold ? ("open" as CircuitState) : s.state,
        failureCount: newFailureCount,
        lastFailureTime: now,
      }
    })

    const canExecute = Effect.gen(function* () {
      const s = yield* Ref.get(stateRef)

      if (s.state === "closed") return true

      if (s.state === "open" && s.lastFailureTime) {
        const elapsed = Date.now() - s.lastFailureTime
        if (elapsed >= recoveryTimeout * 1000) {
          yield* Ref.update(stateRef, (curr) => ({
            ...curr,
            state: "half_open" as CircuitState,
          }))
          return true
        }
        return false
      }

      // half_open - allow one request through
      return true
    })

    const getState = Ref.get(stateRef)

    return { recordSuccess, recordFailure, canExecute, getState }
  })

// ============================================
// GatewayService Interface
// ============================================

export interface GatewayService {
  /**
   * Poll the gateway once and return signal data
   */
  readonly pollOnce: () => Effect.Effect<SignalData | null, GatewayError | CircuitOpenError>

  /**
   * Start the background polling loop
   */
  readonly startPolling: () => Effect.Effect<void, never>

  /**
   * Stop the background polling loop
   */
  readonly stopPolling: () => Effect.Effect<void, never>

  /**
   * Get current cached signal data
   */
  readonly getCurrentData: () => Effect.Effect<SignalData | null, never>

  /**
   * Get raw gateway response
   */
  readonly getRawData: () => Effect.Effect<GatewayResponse | null, never>

  /**
   * Get polling statistics
   */
  readonly getStats: () => Effect.Effect<GatewayStats, never>

  /**
   * Subscribe to signal data updates
   */
  readonly subscribe: () => Stream.Stream<SignalData, never>

  /**
   * Subscribe to outage events
   */
  readonly subscribeOutages: () => Stream.Stream<OutageEvent, never>
}

// ============================================
// GatewayService Tag
// ============================================

export class GatewayServiceTag extends Context.Tag("GatewayService")<
  GatewayServiceTag,
  GatewayService
>() {}

// ============================================
// Helper Functions
// ============================================

/**
 * Safely convert a value to float (handles string numbers from gateway)
 */
const safeFloat = (value: string | number | null | undefined): number | null => {
  if (value === null || value === undefined) return null
  if (typeof value === "number") return value
  const parsed = parseFloat(value)
  return isNaN(parsed) ? null : parsed
}

/**
 * Determine connection mode from signal data presence.
 * - SA: 5G only (standalone)
 * - NSA: 5G + LTE (non-standalone, LTE anchor)
 * - LTE: LTE only
 * - No Signal: neither
 */
const detectConnectionMode = (
  has5g: boolean,
  has4g: boolean
): ConnectionMode => {
  if (has5g && !has4g) return "SA"
  if (has5g && has4g) return "NSA"
  if (!has5g && has4g) return "LTE"
  return "No Signal"
}

/** Check if signal metrics have actual data */
const hasSignalData = (metrics: SignalMetrics): boolean =>
  metrics.sinr !== null || metrics.rsrp !== null

/**
 * Parse gateway response into SignalData
 */
const parseSignalData = (raw: GatewayResponse): SignalData => {
  const nr5g = raw.signal["5g"]
  const lte4g = raw.signal["4g"]
  const device = raw.device

  const nr: SignalMetrics = {
    sinr: safeFloat(nr5g?.sinr),
    rsrp: safeFloat(nr5g?.rsrp),
    rsrq: safeFloat(nr5g?.rsrq),
    rssi: safeFloat(nr5g?.rssi),
    bands: nr5g?.bands ?? [],
    tower_id: nr5g?.gNBID ?? null,
    cell_id: nr5g?.cid ?? null,
  }

  const lte: SignalMetrics = {
    sinr: safeFloat(lte4g?.sinr),
    rsrp: safeFloat(lte4g?.rsrp),
    rsrq: safeFloat(lte4g?.rsrq),
    rssi: safeFloat(lte4g?.rssi),
    bands: lte4g?.bands ?? [],
    tower_id: lte4g?.eNBID ?? null,
    cell_id: lte4g?.cid ?? null,
  }

  const now = new Date()
  const connectionMode = detectConnectionMode(hasSignalData(nr), hasSignalData(lte))

  return {
    timestamp: now,
    timestamp_unix: now.getTime() / 1000,
    nr,
    lte,
    registration_status: connectionMode,
    connection_mode: connectionMode,
    device_uptime: device?.deviceUptime ?? null,
  }
}

/**
 * Convert SignalData to SignalHistoryInsert for database
 */
const signalDataToDbRecord = (data: SignalData): SignalHistoryInsert => ({
  timestamp: data.timestamp.toISOString(),
  timestamp_unix: data.timestamp_unix,
  nr_sinr: data.nr.sinr,
  nr_rsrp: data.nr.rsrp,
  nr_rsrq: data.nr.rsrq,
  nr_rssi: data.nr.rssi,
  nr_bands: data.nr.bands.length > 0 ? JSON.stringify(data.nr.bands) : null,
  nr_gnb_id: data.nr.tower_id,
  nr_cid: data.nr.cell_id,
  lte_sinr: data.lte.sinr,
  lte_rsrp: data.lte.rsrp,
  lte_rsrq: data.lte.rsrq,
  lte_rssi: data.lte.rssi,
  lte_bands: data.lte.bands.length > 0 ? JSON.stringify(data.lte.bands) : null,
  lte_enb_id: data.lte.tower_id,
  lte_cid: data.lte.cell_id,
  registration_status: data.registration_status,
  device_uptime: data.device_uptime,
})

// ============================================
// Implementation
// ============================================

const makeGatewayService = (
  config: GatewayConfig,
  httpClient: HttpClient.HttpClient,
  signalRepository: Context.Tag.Service<typeof SignalRepository>
): Effect.Effect<GatewayService> =>
  Effect.gen(function* () {
    // State refs
    const currentDataRef = yield* Ref.make<SignalData | null>(null)
    const rawDataRef = yield* Ref.make<GatewayResponse | null>(null)
    const lastSuccessRef = yield* Ref.make(0)
    const lastAttemptRef = yield* Ref.make(0)
    const successCountRef = yield* Ref.make(0)
    const errorCountRef = yield* Ref.make(0)
    const lastErrorRef = yield* Ref.make<string | null>(null)
    const runningRef = yield* Ref.make(false)
    const pollFiberRef = yield* Ref.make<Fiber.Fiber<void, never> | null>(null)

    // Outage tracking
    const inOutageRef = yield* Ref.make(false)
    const outageStartTimeRef = yield* Ref.make<number | null>(null)
    const outageErrorCountRef = yield* Ref.make(0)

    // Previous values for signal drop detection
    const prevNrSinrRef = yield* Ref.make<number | null>(null)
    const prevLteSinrRef = yield* Ref.make<number | null>(null)

    // PubSub for signal updates and outage events
    const signalPubSub = yield* PubSub.unbounded<SignalData>()
    const outagePubSub = yield* PubSub.unbounded<OutageEvent>()

    // Batch queue for database writes (2s polling -> batch every 5 seconds)
    const batchQueue = yield* Queue.unbounded<SignalHistoryInsert>()

    // Circuit breaker
    const circuitBreaker = yield* makeCircuitBreaker(
      config.failureThreshold,
      config.recoveryTimeoutSeconds
    )

    // Gateway URL
    const gatewayUrl = `http://${config.host}:${config.port}/TMI/v1/gateway?get=all`

    /**
     * Flush batch queue to database
     */
    const flushBatch = Effect.gen(function* () {
      const records = yield* Queue.takeAll(batchQueue)
      if (records.length === 0) return

      const recordsArray = Array.from(records)
      const insertResult = yield* signalRepository.insertSignalHistory(recordsArray).pipe(
        Effect.timeout(Duration.seconds(5)),
        Effect.catchTag("TimeoutException", () =>
          Effect.gen(function* () {
            yield* Effect.logError("Batch insert timed out after 5 seconds")
            return 0
          })
        ),
        Effect.catchAll((error) =>
          Effect.gen(function* () {
            yield* Effect.logError(`Failed to persist signals: ${JSON.stringify(error)}`)
            return 0
          })
        )
      )
      yield* Effect.logDebug(`Flushed ${insertResult} signals to database`)
    })

    // Background batch flush loop (every 5 seconds)
    const batchFlushFiberRef = yield* Ref.make<Fiber.Fiber<void, never> | null>(null)

    /**
     * Handle poll error
     */
    const handleError = (message: string) =>
      Effect.gen(function* () {
        yield* Ref.update(errorCountRef, (n) => n + 1)
        yield* Ref.set(lastErrorRef, message)
        yield* Ref.update(outageErrorCountRef, (n) => n + 1)

        const cbState = yield* circuitBreaker.getState
        const wasClosed = cbState.state === "closed"
        yield* circuitBreaker.recordFailure

        yield* Effect.logWarning(`Gateway poll error: ${message}`)

        // Check if circuit just opened (outage start)
        const newCbState = yield* circuitBreaker.getState
        const inOutage = yield* Ref.get(inOutageRef)

        if (wasClosed && newCbState.state === "open" && !inOutage) {
          const now = Date.now()
          yield* Ref.set(inOutageRef, true)
          yield* Ref.set(outageStartTimeRef, now)

          const errorCount = yield* Ref.get(outageErrorCountRef)
          const outageEvent: OutageEvent = {
            start_time: now,
            end_time: null,
            duration_seconds: null,
            error_count: errorCount,
            last_error: message,
            resolved: false,
          }

          yield* Effect.logWarning(
            `Gateway outage started at ${new Date(now).toISOString()}`
          )
          yield* PubSub.publish(outagePubSub, outageEvent)
        }
      })

    /**
     * Detect signal quality drops
     */
    const detectSignalDrops = (data: SignalData) =>
      Effect.gen(function* () {
        const prevNrSinr = yield* Ref.get(prevNrSinrRef)
        const prevLteSinr = yield* Ref.get(prevLteSinrRef)

        // 5G SINR drop detection
        if (data.nr.sinr !== null && prevNrSinr !== null) {
          const drop = prevNrSinr - data.nr.sinr
          if (drop > config.sinrDropThresholdDb) {
            yield* Effect.logWarning(
              `5G SINR drop detected: ${prevNrSinr.toFixed(1)} -> ${data.nr.sinr.toFixed(1)} (${drop.toFixed(1)} dB)`
            )
          }
        }

        // 4G SINR drop detection
        if (data.lte.sinr !== null && prevLteSinr !== null) {
          const drop = prevLteSinr - data.lte.sinr
          if (drop > config.sinrDropThresholdDb) {
            yield* Effect.logWarning(
              `4G SINR drop detected: ${prevLteSinr.toFixed(1)} -> ${data.lte.sinr.toFixed(1)} (${drop.toFixed(1)} dB)`
            )
          }
        }

        // Update previous values
        yield* Ref.set(prevNrSinrRef, data.nr.sinr)
        yield* Ref.set(prevLteSinrRef, data.lte.sinr)
      })

    /**
     * Handle outage recovery
     */
    const handleOutageRecovery = Effect.gen(function* () {
      const inOutage = yield* Ref.get(inOutageRef)
      if (!inOutage) return

      const outageStartTime = yield* Ref.get(outageStartTimeRef)
      const now = Date.now()
      const duration = outageStartTime ? (now - outageStartTime) / 1000 : 0
      const errorCount = yield* Ref.get(outageErrorCountRef)
      const lastError = yield* Ref.get(lastErrorRef)

      const outageEvent: OutageEvent = {
        start_time: outageStartTime ?? now,
        end_time: now,
        duration_seconds: duration,
        error_count: errorCount,
        last_error: lastError,
        resolved: true,
      }

      yield* Effect.logInfo(
        `Gateway outage resolved after ${duration.toFixed(1)}s with ${errorCount} errors`
      )
      yield* PubSub.publish(outagePubSub, outageEvent)

      // Reset outage state
      yield* Ref.set(inOutageRef, false)
      yield* Ref.set(outageStartTimeRef, null)
      yield* Ref.set(outageErrorCountRef, 0)
    })

    /**
     * Poll once implementation
     */
    const pollOnce: Effect.Effect<SignalData | null, GatewayError | CircuitOpenError> =
      Effect.gen(function* () {
        yield* Ref.set(lastAttemptRef, Date.now())

        // Check circuit breaker
        const canExecute = yield* circuitBreaker.canExecute
        if (!canExecute) {
          yield* Effect.logDebug("Gateway poll skipped: circuit open")
          return null
        }

        // Make HTTP request using native fetch wrapped in Effect
        const { status, body } = yield* Effect.tryPromise({
          try: async () => {
            const controller = new AbortController()
            const timeoutId = setTimeout(() => controller.abort(), config.timeoutSeconds * 1000)
            try {
              const res = await fetch(gatewayUrl, { signal: controller.signal })
              clearTimeout(timeoutId)
              const text = await res.text()
              return { status: res.status, body: text }
            } catch (e) {
              clearTimeout(timeoutId)
              throw e
            }
          },
          catch: (error) => {
            const errorType = String(error).includes("abort") ? "timeout"
              : String(error).includes("ECONNREFUSED") ? "connection_refused"
              : "http_error"
            return new GatewayError(errorType, `Fetch error: ${error}`, error)
          }
        }).pipe(
          Effect.tapError((error) => handleError(`[${error.type}] ${error.message}`))
        )

        // Check HTTP status
        if (status !== 200) {
          const preview = body.slice(0, 500)
          const error = new GatewayError("http_error", `HTTP ${status}: ${preview}`, { status, body })
          yield* handleError(`[http_error] HTTP ${status}: ${preview}`)
          return yield* Effect.fail(error)
        }

        // Parse JSON response
        const json = yield* Effect.try({
          try: () => JSON.parse(body),
          catch: (error) => {
            const preview = body.slice(0, 500)
            return new GatewayError("parse_error", `Failed to parse JSON: ${error}. Raw: ${preview}`, { error, body })
          }
        }).pipe(
          Effect.tapError((error) => handleError(`[${error.type}] ${error.message}`))
        )

        // Decode with schema
        const rawData = yield* Schema.decodeUnknown(GatewayResponseSchema)(json).pipe(
          Effect.catchAll((error) =>
            Effect.gen(function* () {
              yield* handleError(`[parse_error] Schema validation failed: ${error}`)
              return yield* Effect.fail(
                new GatewayError("parse_error", "Schema validation failed", error)
              )
            })
          )
        )

        // Parse into SignalData
        const signalData = parseSignalData(rawData)

        // Update state
        yield* Ref.set(rawDataRef, rawData)
        yield* Ref.set(currentDataRef, signalData)
        yield* Ref.set(lastSuccessRef, Date.now())
        yield* Ref.update(successCountRef, (n) => n + 1)

        // Record success with circuit breaker
        yield* circuitBreaker.recordSuccess

        // Detect signal drops
        yield* detectSignalDrops(signalData)

        // Handle outage recovery
        yield* handleOutageRecovery

        // Publish signal update to subscribers
        yield* PubSub.publish(signalPubSub, signalData)

        // Queue for batched database persistence
        yield* Queue.offer(batchQueue, signalDataToDbRecord(signalData))

        return signalData
      })

    /**
     * Single poll iteration
     */
    const doPoll = Effect.gen(function* () {
      const running = yield* Ref.get(runningRef)
      if (!running) return

      yield* pollOnce.pipe(
        Effect.catchAll((error) => {
          if (error._tag === "GatewayError") {
            const causeInfo = error.cause instanceof Error
              ? ` | cause: ${error.cause.message}`
              : error.cause
                ? ` | cause: ${String(error.cause)}`
                : ""
            return Effect.logWarning(`Poll error: [${error.type}] ${error.message}${causeInfo}`)
          }
          if (error._tag === "CircuitOpenError") {
            return Effect.logWarning(`Poll error: Circuit breaker open, recovery in ${error.recoveryTimeRemaining}ms`)
          }
          return Effect.logWarning(`Poll error: ${error._tag} - ${JSON.stringify(error)}`)
        })
      )
    })

    /**
     * Polling loop - repeats doPoll every pollIntervalMs
     */
    const pollLoop = Effect.gen(function* () {
      while (true) {
        yield* doPoll
        yield* Effect.sleep(Duration.millis(config.pollIntervalMs))
      }
    }).pipe(
      Effect.catchAll((error) => Effect.logError(`pollLoop error: ${error}`))
    )

    /**
     * Batch flush loop (every 5 seconds)
     */
    const batchFlushLoop = flushBatch.pipe(
      Effect.repeat(Schedule.spaced(5000)),
      Effect.forever
    )

    const service: GatewayService = {
      pollOnce: () => pollOnce,

      startPolling: () =>
        Effect.gen(function* () {
          const running = yield* Ref.get(runningRef)
          if (running) return

          yield* Ref.set(runningRef, true)

          // Start polling fiber
          const pollFiber = yield* Effect.fork(pollLoop)
          yield* Ref.set(pollFiberRef, pollFiber)

          // Start batch flush fiber
          const flushFiber = yield* Effect.fork(batchFlushLoop)
          yield* Ref.set(batchFlushFiberRef, flushFiber)

          yield* Effect.logInfo(
            `Gateway polling started with ${config.pollIntervalMs}ms interval`
          )
        }),

      stopPolling: () =>
        Effect.gen(function* () {
          const running = yield* Ref.get(runningRef)
          if (!running) return

          yield* Ref.set(runningRef, false)

          // Stop polling fiber
          const pollFiber = yield* Ref.get(pollFiberRef)
          if (pollFiber) {
            yield* Fiber.interrupt(pollFiber)
            yield* Ref.set(pollFiberRef, null)
          }

          // Stop batch flush fiber and do final flush
          const flushFiber = yield* Ref.get(batchFlushFiberRef)
          if (flushFiber) {
            yield* Fiber.interrupt(flushFiber)
            yield* Ref.set(batchFlushFiberRef, null)
          }

          // Final flush of any remaining records
          yield* flushBatch

          yield* Effect.logInfo("Gateway polling stopped")
        }),

      getCurrentData: () => Ref.get(currentDataRef),

      getRawData: () => Ref.get(rawDataRef),

      getStats: () =>
        Effect.gen(function* () {
          const cbState = yield* circuitBreaker.getState
          return {
            last_success: yield* Ref.get(lastSuccessRef),
            last_attempt: yield* Ref.get(lastAttemptRef),
            success_count: yield* Ref.get(successCountRef),
            error_count: yield* Ref.get(errorCountRef),
            last_error: yield* Ref.get(lastErrorRef),
            circuit_state: cbState.state,
            is_running: yield* Ref.get(runningRef),
          }
        }),

      subscribe: () => Stream.fromPubSub(signalPubSub),

      subscribeOutages: () => Stream.fromPubSub(outagePubSub),
    }

    return service
  })

// ============================================
// Layer
// ============================================

export const GatewayServiceLive = Layer.effect(
  GatewayServiceTag,
  Effect.gen(function* () {
    const config = yield* GatewayConfigService
    const httpClient = yield* HttpClient.HttpClient
    const signalRepository = yield* SignalRepository
    return yield* makeGatewayService(config, httpClient, signalRepository)
  })
)
