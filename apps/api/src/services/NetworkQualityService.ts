/**
 * NetworkQualityService - Effect-based network quality monitoring.
 *
 * Pings configured targets to measure:
 * - Latency (average RTT)
 * - Jitter (latency variance)
 * - Packet loss percentage
 *
 * Provides:
 * - Start/stop monitoring with configurable interval
 * - Manual trigger for immediate tests
 * - Results storage in SQLite
 * - Config and stats retrieval
 */

import { Context, Effect, Layer, Ref, Schedule, Fiber, Schema } from "effect"
import { SqliteConnection, RepositoryError } from "./SignalRepository.js"
import * as ChildProcess from "node:child_process"
import * as os from "node:os"
import * as fs from "node:fs"
import * as path from "node:path"

// ============================================
// Types
// ============================================

export interface NetworkQualityTarget {
  readonly host: string
  readonly name: string
}

export interface NetworkQualityConfig {
  readonly enabled: boolean
  readonly interval_minutes: number
  readonly min_interval_minutes: number
  readonly max_interval_minutes: number
  readonly ping_count: number
  readonly ping_timeout_seconds: number
  readonly targets: readonly NetworkQualityTarget[]
  readonly packet_loss_threshold_percent: number
  readonly jitter_threshold_ms: number
  readonly notify_on_threshold: boolean
}

export interface NetworkQualityResult {
  readonly target_host: string
  readonly target_name: string
  readonly ping_ms: number | null
  readonly jitter_ms: number
  readonly packet_loss_percent: number
  readonly status: "success" | "error" | "timeout"
  readonly timestamp: string
  readonly timestamp_unix: number
  readonly error_message?: string
}

export interface NetworkQualityStats {
  readonly is_running: boolean
  readonly tests_completed: number
  readonly last_test_time: number
  readonly next_test_time: number
  readonly next_test_in_seconds: number | null
}

// ============================================
// Schemas for DB
// ============================================

export const NetworkQualityResultRecord = Schema.Struct({
  id: Schema.optionalWith(Schema.Number, { nullable: true }),
  timestamp: Schema.String,
  timestamp_unix: Schema.Number,
  target_host: Schema.String,
  target_name: Schema.optionalWith(Schema.String, { nullable: true }),
  ping_ms: Schema.optionalWith(Schema.Number, { nullable: true }),
  jitter_ms: Schema.Number,
  packet_loss_percent: Schema.Number,
  status: Schema.String,
  error_message: Schema.optionalWith(Schema.String, { nullable: true }),
})

export type NetworkQualityResultRecord = typeof NetworkQualityResultRecord.Type

// ============================================
// Error Types
// ============================================

export class NetworkQualityError {
  readonly _tag = "NetworkQualityError"
  constructor(
    readonly type: "execution" | "parse" | "timeout" | "config",
    readonly message: string,
    readonly cause?: unknown
  ) {}
}

// ============================================
// Constants
// ============================================

const DEFAULT_CONFIG: NetworkQualityConfig = {
  enabled: true,
  interval_minutes: 5,
  min_interval_minutes: 1,
  max_interval_minutes: 60,
  ping_count: 20,
  ping_timeout_seconds: 5,
  targets: [
    { host: "8.8.8.8", name: "Google DNS" },
    { host: "1.1.1.1", name: "Cloudflare DNS" },
    { host: "208.54.0.1", name: "Carrier DNS" },
  ],
  packet_loss_threshold_percent: 5,
  jitter_threshold_ms: 50,
  notify_on_threshold: true,
}

// ============================================
// Helper Functions
// ============================================

/**
 * Run a command and return stdout/stderr
 */
const runCommand = (
  cmd: string[],
  timeout: number
): Effect.Effect<{ stdout: string; stderr: string; code: number }, NetworkQualityError> =>
  Effect.async<{ stdout: string; stderr: string; code: number }, NetworkQualityError>(
    (resume) => {
      const [executable, ...args] = cmd
      const proc = ChildProcess.spawn(executable, args, {
        timeout: timeout * 1000,
        shell: os.platform() === "win32",
      })

      let stdout = ""
      let stderr = ""

      proc.stdout?.on("data", (data: Buffer) => {
        stdout += data.toString()
      })

      proc.stderr?.on("data", (data: Buffer) => {
        stderr += data.toString()
      })

      proc.on("close", (code) => {
        resume(Effect.succeed({ stdout, stderr, code: code ?? 1 }))
      })

      proc.on("error", (err: Error & { code?: string }) => {
        if (err.code === "ETIMEDOUT") {
          resume(
            Effect.fail(new NetworkQualityError("timeout", `Command timed out after ${timeout}s`))
          )
        } else {
          resume(Effect.fail(new NetworkQualityError("execution", err.message, err)))
        }
      })
    }
  )

/**
 * Parse ping output to extract latency statistics
 */
const parsePingOutput = (
  output: string,
  isWindows: boolean
): { latencies: number[]; packetsSent: number; packetsReceived: number } => {
  const latencies: number[] = []
  let packetsSent = 0
  let packetsReceived = 0

  if (isWindows) {
    // Windows ping output parsing
    // "Reply from 8.8.8.8: bytes=32 time=15ms TTL=117"
    // "Packets: Sent = 4, Received = 4, Lost = 0 (0% loss)"
    const lines = output.split("\n")
    for (const line of lines) {
      const timeMatch = line.match(/time[=<](\d+)ms/i)
      if (timeMatch) {
        latencies.push(parseFloat(timeMatch[1]))
      }
      const statsMatch = line.match(/Sent\s*=\s*(\d+),\s*Received\s*=\s*(\d+)/i)
      if (statsMatch) {
        packetsSent = parseInt(statsMatch[1])
        packetsReceived = parseInt(statsMatch[2])
      }
    }
  } else {
    // Unix ping output parsing
    // "64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=15.2 ms"
    // "4 packets transmitted, 4 received, 0% packet loss"
    const lines = output.split("\n")
    for (const line of lines) {
      const timeMatch = line.match(/time[=<]?([\d.]+)\s*ms/i)
      if (timeMatch) {
        latencies.push(parseFloat(timeMatch[1]))
      }
      const statsMatch = line.match(/(\d+)\s+packets transmitted,\s*(\d+)\s+received/i)
      if (statsMatch) {
        packetsSent = parseInt(statsMatch[1])
        packetsReceived = parseInt(statsMatch[2])
      }
    }
  }

  return { latencies, packetsSent, packetsReceived }
}

/**
 * Calculate jitter (mean absolute deviation of latencies)
 */
const calculateJitter = (latencies: number[]): number => {
  if (latencies.length < 2) return 0
  const mean = latencies.reduce((a, b) => a + b, 0) / latencies.length
  const deviations = latencies.map((l) => Math.abs(l - mean))
  const jitter = deviations.reduce((a, b) => a + b, 0) / deviations.length
  return Math.round(jitter * 100) / 100
}

/**
 * Ping a single target and measure quality
 */
const pingTarget = (
  target: NetworkQualityTarget,
  pingCount: number,
  timeoutSeconds: number
): Effect.Effect<NetworkQualityResult, NetworkQualityError> =>
  Effect.gen(function* () {
    const isWindows = os.platform() === "win32"
    const cmd = isWindows
      ? ["ping", "-n", String(pingCount), "-w", String(timeoutSeconds * 1000), target.host]
      : ["ping", "-c", String(pingCount), "-W", String(timeoutSeconds), target.host]

    const now = new Date()
    const result = yield* runCommand(cmd, timeoutSeconds * pingCount + 10).pipe(
      Effect.catchAll((e) =>
        Effect.succeed({ stdout: "", stderr: e.message, code: 1 })
      )
    )

    if (result.code !== 0 && result.stdout === "") {
      return {
        target_host: target.host,
        target_name: target.name,
        ping_ms: null,
        jitter_ms: 0,
        packet_loss_percent: 100,
        status: "error" as const,
        timestamp: now.toISOString(),
        timestamp_unix: now.getTime() / 1000,
        error_message: result.stderr || `Exit code ${result.code}`,
      }
    }

    const parsed = parsePingOutput(result.stdout, isWindows)
    const { latencies, packetsSent, packetsReceived } = parsed

    const effectiveSent = packetsSent > 0 ? packetsSent : pingCount
    const packetLoss =
      effectiveSent > 0
        ? Math.round(((effectiveSent - packetsReceived) / effectiveSent) * 100 * 100) / 100
        : 100

    const avgLatency =
      latencies.length > 0
        ? Math.round((latencies.reduce((a, b) => a + b, 0) / latencies.length) * 100) / 100
        : null

    const jitter = calculateJitter(latencies)

    return {
      target_host: target.host,
      target_name: target.name,
      ping_ms: avgLatency,
      jitter_ms: jitter,
      packet_loss_percent: packetLoss,
      status: avgLatency !== null ? ("success" as const) : ("error" as const),
      timestamp: now.toISOString(),
      timestamp_unix: now.getTime() / 1000,
    }
  })

/**
 * Load config from file if it exists
 */
const loadConfig = (): NetworkQualityConfig => {
  try {
    // Try to load from project root
    const configPath = path.resolve(process.cwd(), "network_quality_config.json")
    if (fs.existsSync(configPath)) {
      const content = fs.readFileSync(configPath, "utf-8")
      const parsed = JSON.parse(content)
      return { ...DEFAULT_CONFIG, ...parsed }
    }
  } catch {
    // Ignore errors, use default
  }
  return DEFAULT_CONFIG
}

// ============================================
// Service Interface
// ============================================

export interface NetworkQualityServiceShape {
  /**
   * Get current configuration
   */
  readonly getConfig: () => Effect.Effect<NetworkQualityConfig>

  /**
   * Get monitoring stats
   */
  readonly getStats: () => Effect.Effect<NetworkQualityStats>

  /**
   * Get recent results
   */
  readonly getResults: (limit?: number) => Effect.Effect<readonly NetworkQualityResult[], RepositoryError>

  /**
   * Start monitoring
   */
  readonly start: () => Effect.Effect<void, NetworkQualityError>

  /**
   * Stop monitoring
   */
  readonly stop: () => Effect.Effect<void>

  /**
   * Trigger immediate test
   */
  readonly trigger: () => Effect.Effect<readonly NetworkQualityResult[], NetworkQualityError | RepositoryError>
}

// ============================================
// Service Tag
// ============================================

export class NetworkQualityService extends Context.Tag("NetworkQualityService")<
  NetworkQualityService,
  NetworkQualityServiceShape
>() {}

// ============================================
// Live Implementation
// ============================================

export const NetworkQualityServiceLive = Layer.effect(
  NetworkQualityService,
  Effect.gen(function* () {
    const { db } = yield* SqliteConnection

    // Initialize table
    db.exec(`
      CREATE TABLE IF NOT EXISTS network_quality_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        timestamp_unix REAL NOT NULL,
        target_host TEXT NOT NULL,
        target_name TEXT,
        ping_ms REAL,
        jitter_ms REAL NOT NULL,
        packet_loss_percent REAL NOT NULL,
        status TEXT NOT NULL,
        error_message TEXT
      )
    `)

    // Create index for timestamp queries
    db.exec(`
      CREATE INDEX IF NOT EXISTS idx_nq_timestamp ON network_quality_results(timestamp_unix DESC)
    `)

    // State
    const configRef = yield* Ref.make<NetworkQualityConfig>(loadConfig())
    const isRunningRef = yield* Ref.make(false)
    const testsCompletedRef = yield* Ref.make(0)
    const lastTestTimeRef = yield* Ref.make(0)
    const nextTestTimeRef = yield* Ref.make(0)
    const monitorFiberRef = yield* Ref.make<Fiber.Fiber<void, never> | null>(null)

    /**
     * Run a test against all targets and store results
     */
    const runTest = Effect.gen(function* () {
      const config = yield* Ref.get(configRef)
      const now = Date.now() / 1000

      yield* Effect.logDebug(`Running network quality test against ${config.targets.length} targets`)

      const results: NetworkQualityResult[] = []

      for (const target of config.targets) {
        const result = yield* pingTarget(target, config.ping_count, config.ping_timeout_seconds)
        results.push(result)

        // Store in database
        yield* Effect.try({
          try: () => {
            const stmt = db.prepare(`
              INSERT INTO network_quality_results (
                timestamp, timestamp_unix, target_host, target_name,
                ping_ms, jitter_ms, packet_loss_percent, status, error_message
              ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            `)
            stmt.run(
              result.timestamp,
              result.timestamp_unix,
              result.target_host,
              result.target_name,
              result.ping_ms,
              result.jitter_ms,
              result.packet_loss_percent,
              result.status,
              result.error_message ?? null
            )
          },
          catch: (e) =>
            new RepositoryError("insertNetworkQuality", `Failed to insert result: ${e}`, e),
        })
      }

      yield* Ref.update(testsCompletedRef, (n) => n + 1)
      yield* Ref.set(lastTestTimeRef, now)

      // Schedule next test
      const nextTime = now + config.interval_minutes * 60
      yield* Ref.set(nextTestTimeRef, nextTime)

      yield* Effect.logInfo(
        `Network quality test complete: ${results.filter((r) => r.status === "success").length}/${results.length} targets responded`
      )

      return results
    })

    /**
     * Monitor loop
     */
    const monitorLoop = Effect.gen(function* () {
      const config = yield* Ref.get(configRef)
      const intervalMs = config.interval_minutes * 60 * 1000

      yield* runTest.pipe(
        Effect.catchAll((e) => Effect.logError(`Network quality test failed: ${e}`)),
        Effect.repeat(Schedule.spaced(intervalMs)),
        Effect.forever
      )
    })

    const impl: NetworkQualityServiceShape = {
      getConfig: () => Ref.get(configRef),

      getStats: () =>
        Effect.gen(function* () {
          const isRunning = yield* Ref.get(isRunningRef)
          const testsCompleted = yield* Ref.get(testsCompletedRef)
          const lastTestTime = yield* Ref.get(lastTestTimeRef)
          const nextTestTime = yield* Ref.get(nextTestTimeRef)
          const now = Date.now() / 1000

          return {
            is_running: isRunning,
            tests_completed: testsCompleted,
            last_test_time: lastTestTime,
            next_test_time: nextTestTime,
            next_test_in_seconds: isRunning && nextTestTime > now ? nextTestTime - now : null,
          }
        }),

      getResults: (limit = 100) =>
        Effect.gen(function* () {
          const rows = yield* Effect.try({
            try: () =>
              db
                .prepare(
                  `
                  SELECT * FROM network_quality_results
                  ORDER BY timestamp_unix DESC
                  LIMIT ?
                `
                )
                .all(limit) as Array<{
                id: number
                timestamp: string
                timestamp_unix: number
                target_host: string
                target_name: string | null
                ping_ms: number | null
                jitter_ms: number
                packet_loss_percent: number
                status: string
                error_message: string | null
              }>,
            catch: (e) =>
              new RepositoryError("queryNetworkQuality", `Query failed: ${e}`, e),
          })

          return rows.map((row) => ({
            target_host: row.target_host,
            target_name: row.target_name ?? row.target_host,
            ping_ms: row.ping_ms,
            jitter_ms: row.jitter_ms,
            packet_loss_percent: row.packet_loss_percent,
            status: row.status as "success" | "error" | "timeout",
            timestamp: row.timestamp,
            timestamp_unix: row.timestamp_unix,
            error_message: row.error_message ?? undefined,
          }))
        }),

      start: () =>
        Effect.gen(function* () {
          const isRunning = yield* Ref.get(isRunningRef)
          if (isRunning) {
            yield* Effect.logDebug("Network quality monitoring already running")
            return
          }

          yield* Ref.set(isRunningRef, true)

          // Set initial next test time
          const config = yield* Ref.get(configRef)
          const now = Date.now() / 1000
          yield* Ref.set(nextTestTimeRef, now + config.interval_minutes * 60)

          // Fork the monitor loop
          const fiber = yield* Effect.fork(monitorLoop)
          yield* Ref.set(monitorFiberRef, fiber)

          yield* Effect.logInfo("Network quality monitoring started")
        }),

      stop: () =>
        Effect.gen(function* () {
          const fiber = yield* Ref.get(monitorFiberRef)
          if (fiber) {
            yield* Fiber.interrupt(fiber)
            yield* Ref.set(monitorFiberRef, null)
          }
          yield* Ref.set(isRunningRef, false)
          yield* Ref.set(nextTestTimeRef, 0)

          yield* Effect.logInfo("Network quality monitoring stopped")
        }),

      trigger: () =>
        Effect.gen(function* () {
          yield* Effect.logInfo("Triggering manual network quality test")
          const results = yield* runTest
          return results
        }),
    }

    return impl
  })
)
