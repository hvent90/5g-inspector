/**
 * SchedulerService - Effect-based speedtest scheduler management.
 *
 * Provides:
 * - Automated speedtest scheduling with configurable intervals
 * - Start/stop scheduler controls
 * - Scheduler configuration management
 * - Statistics tracking (tests completed, averages, next run time)
 */

import { Context, Effect, Layer, Ref, Fiber, Schedule, Duration } from "effect"
import { SpeedtestService, type SpeedtestResult, type SpeedtestError } from "./SpeedtestService.js"
import type { RepositoryError } from "./SignalRepository.js"

// ============================================
// Types
// ============================================

export interface SchedulerConfig {
  readonly enabled: boolean
  readonly interval_minutes: number
  readonly time_window_start?: number // Hour (0-23) when scheduler can run
  readonly time_window_end?: number // Hour (0-23) when scheduler stops
  readonly run_on_weekends?: boolean
  readonly tools_to_run?: readonly string[] // Tools to run each cycle (undefined = default single-tool)
  readonly delay_between_tools_seconds?: number // Delay between tools (default 10)
}

export interface SchedulerStats {
  readonly is_running: boolean
  readonly tests_completed: number
  readonly tests_failed: number
  readonly last_test_time: string | null
  readonly next_test_time: string | null
  readonly next_test_in_seconds: number | null
  readonly average_download_mbps: number | null
  readonly average_upload_mbps: number | null
}

// ============================================
// Error Types
// ============================================

export class SchedulerError {
  readonly _tag = "SchedulerError"
  constructor(
    readonly type: "already_running" | "not_running" | "config",
    readonly message: string,
    readonly cause?: unknown
  ) {}
}

// ============================================
// Service Interface
// ============================================

export interface SchedulerServiceShape {
  /**
   * Get current scheduler configuration
   */
  readonly getConfig: () => Effect.Effect<SchedulerConfig>

  /**
   * Update scheduler configuration
   */
  readonly updateConfig: (
    updates: Partial<SchedulerConfig>
  ) => Effect.Effect<SchedulerConfig, SchedulerError>

  /**
   * Get scheduler statistics
   */
  readonly getStats: () => Effect.Effect<SchedulerStats>

  /**
   * Start the scheduler
   */
  readonly start: () => Effect.Effect<void, SchedulerError>

  /**
   * Stop the scheduler
   */
  readonly stop: () => Effect.Effect<void, SchedulerError>

  /**
   * Check if scheduler is running
   */
  readonly isRunning: () => Effect.Effect<boolean>
}

// ============================================
// Service Tag
// ============================================

export class SchedulerService extends Context.Tag("SchedulerService")<
  SchedulerService,
  SchedulerServiceShape
>() {}

// ============================================
// Default Configuration
// ============================================

const DEFAULT_CONFIG: SchedulerConfig = {
  enabled: true,
  interval_minutes: 30,
  time_window_start: undefined,
  time_window_end: undefined,
  run_on_weekends: true,
  tools_to_run: undefined, // undefined = use default single-tool behavior
  delay_between_tools_seconds: 10,
}

// ============================================
// Live Implementation
// ============================================

export const SchedulerServiceLive = Layer.effect(
  SchedulerService,
  Effect.gen(function* () {
    const speedtestService = yield* SpeedtestService

    // State
    const configRef = yield* Ref.make<SchedulerConfig>(DEFAULT_CONFIG)
    const fiberRef = yield* Ref.make<Fiber.RuntimeFiber<void, SpeedtestError | RepositoryError> | null>(null)
    const testsCompletedRef = yield* Ref.make<number>(0)
    const testsFailedRef = yield* Ref.make<number>(0)
    const lastTestTimeRef = yield* Ref.make<Date | null>(null)
    const nextTestTimeRef = yield* Ref.make<Date | null>(null)
    const downloadSumRef = yield* Ref.make<number>(0)
    const uploadSumRef = yield* Ref.make<number>(0)

    /**
     * Check if current time is within the configured window
     */
    const isWithinTimeWindow = (config: SchedulerConfig): boolean => {
      const now = new Date()
      const currentHour = now.getHours()
      const currentDay = now.getDay() // 0 = Sunday, 6 = Saturday

      // Check weekend restriction
      if (!config.run_on_weekends && (currentDay === 0 || currentDay === 6)) {
        return false
      }

      // Check time window
      if (config.time_window_start !== undefined && config.time_window_end !== undefined) {
        if (config.time_window_start <= config.time_window_end) {
          // Normal range (e.g., 9-17)
          return currentHour >= config.time_window_start && currentHour < config.time_window_end
        } else {
          // Overnight range (e.g., 22-6)
          return currentHour >= config.time_window_start || currentHour < config.time_window_end
        }
      }

      return true
    }

    /**
     * Calculate next test time
     */
    const calculateNextTestTime = (intervalMinutes: number): Date => {
      const next = new Date()
      next.setMinutes(next.getMinutes() + intervalMinutes)
      next.setSeconds(0)
      next.setMilliseconds(0)
      return next
    }

    /**
     * Handle a single speedtest result (update stats)
     */
    const handleResult = (result: SpeedtestResult) =>
      Effect.gen(function* () {
        yield* Ref.set(lastTestTimeRef, new Date())

        if (result.status === "success") {
          yield* Ref.update(testsCompletedRef, (n) => n + 1)
          yield* Ref.update(downloadSumRef, (sum) => sum + result.download_mbps)
          yield* Ref.update(uploadSumRef, (sum) => sum + result.upload_mbps)
          yield* Effect.logInfo(
            `Scheduler: Test complete (${result.tool}) - ${result.download_mbps} Mbps down, ${result.upload_mbps} Mbps up`
          )
        } else {
          yield* Ref.update(testsFailedRef, (n) => n + 1)
          yield* Effect.logWarning(`Scheduler: Test failed (${result.tool}) - ${result.error_message ?? result.status}`)
        }
      })

    /**
     * Run scheduled speedtest(s) - supports multi-tool mode
     */
    const runScheduledTest = Effect.gen(function* () {
      const config = yield* Ref.get(configRef)

      // Check if within time window
      if (!isWithinTimeWindow(config)) {
        yield* Effect.logDebug("Scheduler: Outside time window, skipping test")
        return
      }

      const toolsToRun = config.tools_to_run ?? []

      if (toolsToRun.length === 0) {
        // Original single-tool behavior (auto-select best available tool)
        yield* Effect.logInfo("Scheduler: Running scheduled speedtest")
        const result = yield* speedtestService.runSpeedtest({
          triggeredBy: "scheduler",
        })
        yield* handleResult(result)
      } else {
        // Multi-tool: run each in sequence
        yield* Effect.logInfo(`Scheduler: Running ${toolsToRun.length} tools: ${toolsToRun.join(", ")}`)

        for (const tool of toolsToRun) {
          yield* Effect.logInfo(`Scheduler: Running ${tool}`)
          const result = yield* speedtestService.runSpeedtest({
            triggeredBy: "scheduler",
            tool,
          })
          yield* handleResult(result)

          // Delay between tools (skip after last)
          if (tool !== toolsToRun.at(-1)) {
            const delay = config.delay_between_tools_seconds ?? 10
            yield* Effect.logDebug(`Scheduler: Waiting ${delay}s before next tool`)
            yield* Effect.sleep(Duration.seconds(delay))
          }
        }
        yield* Effect.logInfo("Scheduler: Multi-tool cycle complete")
      }
    })

    /**
     * The main scheduler loop
     */
    const schedulerLoop = Effect.gen(function* () {
      const config = yield* Ref.get(configRef)
      const intervalMs = config.interval_minutes * 60 * 1000

      // Update next test time
      yield* Ref.set(nextTestTimeRef, calculateNextTestTime(config.interval_minutes))

      // Run tests on the schedule
      yield* runScheduledTest.pipe(
        Effect.catchAll((error) =>
          Effect.logError(`Scheduler error: ${error}`)
        ),
        Effect.repeat(
          Schedule.spaced(Duration.millis(intervalMs)).pipe(
            Schedule.tapInput(() =>
              Effect.gen(function* () {
                const currentConfig = yield* Ref.get(configRef)
                yield* Ref.set(nextTestTimeRef, calculateNextTestTime(currentConfig.interval_minutes))
              })
            )
          )
        ),
        Effect.forever
      )
    })

    const impl: SchedulerServiceShape = {
      getConfig: () => Ref.get(configRef),

      updateConfig: (updates) =>
        Effect.gen(function* () {
          const current = yield* Ref.get(configRef)

          // Validate interval
          if (updates.interval_minutes !== undefined) {
            if (updates.interval_minutes < 1 || updates.interval_minutes > 1440) {
              return yield* Effect.fail(
                new SchedulerError("config", "Interval must be between 1 and 1440 minutes")
              )
            }
          }

          // Validate delay_between_tools_seconds
          if (updates.delay_between_tools_seconds !== undefined) {
            if (updates.delay_between_tools_seconds < 0 || updates.delay_between_tools_seconds > 300) {
              return yield* Effect.fail(
                new SchedulerError("config", "Delay between tools must be between 0 and 300 seconds")
              )
            }
          }

          // Validate tools_to_run (basic validation - just check it's an array of strings)
          if (updates.tools_to_run !== undefined) {
            if (updates.tools_to_run.length > 10) {
              return yield* Effect.fail(
                new SchedulerError("config", "Cannot run more than 10 tools per cycle")
              )
            }
          }

          const newConfig: SchedulerConfig = {
            ...current,
            ...updates,
          }

          yield* Ref.set(configRef, newConfig)
          yield* Effect.logInfo(`Scheduler config updated: interval=${newConfig.interval_minutes}min`)

          // If scheduler is running and interval changed, restart it
          const fiber = yield* Ref.get(fiberRef)
          if (fiber !== null && updates.interval_minutes !== undefined) {
            yield* Effect.logInfo("Scheduler: Restarting with new interval")
            yield* Fiber.interrupt(fiber)
            const newFiber = yield* Effect.fork(schedulerLoop)
            yield* Ref.set(fiberRef, newFiber)
          }

          return newConfig
        }),

      getStats: () =>
        Effect.gen(function* () {
          const fiber = yield* Ref.get(fiberRef)
          const testsCompleted = yield* Ref.get(testsCompletedRef)
          const testsFailed = yield* Ref.get(testsFailedRef)
          const lastTestTime = yield* Ref.get(lastTestTimeRef)
          const nextTestTime = yield* Ref.get(nextTestTimeRef)
          const downloadSum = yield* Ref.get(downloadSumRef)
          const uploadSum = yield* Ref.get(uploadSumRef)

          const isRunning = fiber !== null
          const now = new Date()

          let nextTestInSeconds: number | null = null
          if (isRunning && nextTestTime) {
            nextTestInSeconds = Math.max(0, Math.floor((nextTestTime.getTime() - now.getTime()) / 1000))
          }

          return {
            is_running: isRunning,
            tests_completed: testsCompleted,
            tests_failed: testsFailed,
            last_test_time: lastTestTime?.toISOString() ?? null,
            next_test_time: nextTestTime?.toISOString() ?? null,
            next_test_in_seconds: nextTestInSeconds,
            average_download_mbps:
              testsCompleted > 0 ? Math.round((downloadSum / testsCompleted) * 100) / 100 : null,
            average_upload_mbps:
              testsCompleted > 0 ? Math.round((uploadSum / testsCompleted) * 100) / 100 : null,
          }
        }),

      start: () =>
        Effect.gen(function* () {
          const existingFiber = yield* Ref.get(fiberRef)
          if (existingFiber !== null) {
            return yield* Effect.fail(
              new SchedulerError("already_running", "Scheduler is already running")
            )
          }

          yield* Effect.logInfo("Scheduler: Starting")

          // Update config to enabled
          yield* Ref.update(configRef, (c) => ({ ...c, enabled: true }))

          // Fork the scheduler loop
          const fiber = yield* Effect.fork(schedulerLoop)
          yield* Ref.set(fiberRef, fiber)

          yield* Effect.logInfo("Scheduler: Started successfully")
        }),

      stop: () =>
        Effect.gen(function* () {
          const fiber = yield* Ref.get(fiberRef)
          if (fiber === null) {
            return yield* Effect.fail(
              new SchedulerError("not_running", "Scheduler is not running")
            )
          }

          yield* Effect.logInfo("Scheduler: Stopping")

          yield* Fiber.interrupt(fiber)
          yield* Ref.set(fiberRef, null)
          yield* Ref.set(nextTestTimeRef, null)

          // Update config to disabled
          yield* Ref.update(configRef, (c) => ({ ...c, enabled: false }))

          yield* Effect.logInfo("Scheduler: Stopped successfully")
        }),

      isRunning: () =>
        Effect.gen(function* () {
          const fiber = yield* Ref.get(fiberRef)
          return fiber !== null
        }),
    }

    return impl
  })
)

// ============================================
// Layer Composition Helper
// ============================================

export const makeSchedulerServiceLayer = (
  speedtestLayer: Layer.Layer<SpeedtestService, SpeedtestError | RepositoryError>
): Layer.Layer<SchedulerService, SpeedtestError | RepositoryError> =>
  Layer.provide(SchedulerServiceLive, speedtestLayer)
