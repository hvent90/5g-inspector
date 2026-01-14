/**
 * AlertService - Effect-based alert management service.
 *
 * Provides threshold-based alerting with SSE streaming for real-time updates.
 * Uses Effect.Service pattern with SignalRepository dependency.
 *
 * Threshold values based on CLAUDE.md quality thresholds:
 * - SINR: Critical < -5, Warning < 0
 * - RSRP: Critical < -110, Warning < -100
 * - RSRQ: Critical < -19, Warning < -15
 */

import {
  Context,
  Effect,
  Layer,
  Option,
  PubSub,
  Queue,
  Ref,
  Schedule,
  Stream,
} from "effect"
import {
  type Alert,
  type AlertConfig,
  type AlertSSEEvent,
  type AlertType,
  type TriggerAlertInput,
  DEFAULT_ALERT_CONFIG,
} from "../schema/Alert"
import type { DisruptionSeverity } from "../schema/Signal"
import { SignalRepository, type RepositoryError } from "./SignalRepository"

// ============================================
// Alert Service Errors
// ============================================

export class AlertServiceError {
  readonly _tag = "AlertServiceError"
  constructor(
    readonly operation: string,
    readonly message: string,
    readonly cause?: unknown
  ) {}
}

// ============================================
// Internal State Types
// ============================================

interface AlertState {
  config: AlertConfig
  activeAlerts: Map<string, Alert>
  history: Alert[]
  cooldowns: Map<string, number> // alertType -> timestamp
}

// ============================================
// Helper Functions
// ============================================

const generateAlertId = (): string => String(Date.now())

const isCooldownExpired = (
  cooldowns: Map<string, number>,
  alertType: string,
  cooldownMinutes: number
): boolean => {
  const lastTime = cooldowns.get(alertType)
  if (lastTime === undefined) return true
  const cooldownMs = cooldownMinutes * 60 * 1000
  return Date.now() - lastTime >= cooldownMs
}

const createAlert = (input: TriggerAlertInput): Alert => ({
  id: generateAlertId(),
  createdAt: new Date().toISOString(),
  alertType: input.alertType,
  severity: input.severity,
  title: input.title,
  message: input.message,
  data: input.data ?? {},
  acknowledged: false,
  acknowledgedAt: undefined,
  acknowledgedBy: undefined,
  resolved: false,
  resolvedAt: undefined,
})

// ============================================
// Alert Service Interface
// ============================================

export interface AlertServiceShape {
  // Configuration
  getConfig(): Effect.Effect<AlertConfig>
  updateConfig(config: AlertConfig): Effect.Effect<AlertConfig, AlertServiceError>

  // Alert management
  triggerAlert(
    input: TriggerAlertInput
  ): Effect.Effect<Option.Option<Alert>, AlertServiceError>
  getActiveAlerts(): Effect.Effect<readonly Alert[]>
  getHistory(limit?: number, offset?: number): Effect.Effect<readonly Alert[]>
  acknowledgeAlert(alertId: string): Effect.Effect<boolean>
  clearAlert(alertId: string): Effect.Effect<boolean>
  clearAllAlerts(): Effect.Effect<number>

  // Test alert
  triggerTestAlert(): Effect.Effect<Option.Option<Alert>, AlertServiceError>

  // Threshold checking
  checkSignalThresholds(): Effect.Effect<
    readonly Alert[],
    AlertServiceError | RepositoryError
  >

  // SSE streaming
  subscribe(): Stream.Stream<AlertSSEEvent>
}

// ============================================
// Service Tag
// ============================================

export class AlertService extends Context.Tag("AlertService")<
  AlertService,
  AlertServiceShape
>() {}

// ============================================
// Live Implementation
// ============================================

export const AlertServiceLive = Layer.effect(
  AlertService,
  Effect.gen(function* () {
    const signalRepo = yield* SignalRepository

    // Internal state
    const stateRef = yield* Ref.make<AlertState>({
      config: DEFAULT_ALERT_CONFIG,
      activeAlerts: new Map(),
      history: [],
      cooldowns: new Map(),
    })

    // PubSub for SSE broadcasting
    const pubsub = yield* PubSub.bounded<AlertSSEEvent>(100)

    // Broadcast event to all subscribers
    const broadcast = (event: AlertSSEEvent): Effect.Effect<void> =>
      PubSub.publish(pubsub, event).pipe(Effect.asVoid)

    // Helper to add alert to state
    const addAlertToState = (alert: Alert): Effect.Effect<void> =>
      Ref.update(stateRef, (state) => ({
        ...state,
        activeAlerts: new Map(state.activeAlerts).set(alert.alertType, alert),
        history: [...state.history, alert].slice(-1000), // Keep last 1000
        cooldowns: new Map(state.cooldowns).set(alert.alertType, Date.now()),
      }))

    const impl: AlertServiceShape = {
      // ============================================
      // Configuration
      // ============================================

      getConfig: () => Ref.get(stateRef).pipe(Effect.map((s) => s.config)),

      updateConfig: (config) =>
        Effect.gen(function* () {
          // Basic validation
          if (typeof config.enabled !== "boolean") {
            return yield* Effect.fail(
              new AlertServiceError("updateConfig", "enabled must be a boolean")
            )
          }
          yield* Ref.update(stateRef, (s) => ({ ...s, config }))
          return config
        }),

      // ============================================
      // Alert Management
      // ============================================

      triggerAlert: (input) =>
        Effect.gen(function* () {
          const state = yield* Ref.get(stateRef)

          // Check if alerting is enabled
          if (!state.config.enabled) {
            return Option.none()
          }

          // Check cooldown
          if (
            !isCooldownExpired(
              state.cooldowns,
              input.alertType,
              state.config.cooldownMinutes
            )
          ) {
            return Option.none()
          }

          // Check notification settings
          if (input.severity === "warning" && !state.config.notifyOnWarning) {
            return Option.none()
          }
          if (input.severity === "critical" && !state.config.notifyOnCritical) {
            return Option.none()
          }

          // Create and store alert
          const alert = createAlert(input)
          yield* addAlertToState(alert)

          // Broadcast to SSE subscribers
          yield* broadcast({ type: "alert", payload: alert })

          return Option.some(alert)
        }),

      getActiveAlerts: () =>
        Ref.get(stateRef).pipe(
          Effect.map((s) =>
            Array.from(s.activeAlerts.values()).filter((a) => !a.resolved)
          )
        ),

      getHistory: (limit = 100, offset = 0) =>
        Ref.get(stateRef).pipe(
          Effect.map((s) => {
            const reversed = [...s.history].reverse()
            return reversed.slice(offset, offset + limit)
          })
        ),

      acknowledgeAlert: (alertId) =>
        Ref.modify(stateRef, (state) => {
          let found = false
          const newActiveAlerts = new Map(state.activeAlerts)

          for (const [key, alert] of newActiveAlerts) {
            if (alert.id === alertId) {
              newActiveAlerts.set(key, {
                ...alert,
                acknowledged: true,
                acknowledgedAt: new Date().toISOString(),
              })
              found = true
              break
            }
          }

          // Also update in history
          const newHistory = state.history.map((a) =>
            a.id === alertId
              ? {
                  ...a,
                  acknowledged: true,
                  acknowledgedAt: new Date().toISOString(),
                }
              : a
          )

          return [
            found,
            { ...state, activeAlerts: newActiveAlerts, history: newHistory },
          ]
        }),

      clearAlert: (alertId) =>
        Effect.gen(function* () {
          const result = yield* Ref.modify(stateRef, (state) => {
            let found = false
            const newActiveAlerts = new Map(state.activeAlerts)

            for (const [key, alert] of newActiveAlerts) {
              if (alert.id === alertId) {
                newActiveAlerts.delete(key)
                found = true
                break
              }
            }

            return [found, { ...state, activeAlerts: newActiveAlerts }]
          })

          if (result) {
            yield* broadcast({
              type: "alert_cleared",
              payload: {
                alertId,
                timestamp: new Date().toISOString(),
              },
            })
          }

          return result
        }),

      clearAllAlerts: () =>
        Effect.gen(function* () {
          const count = yield* Ref.modify(stateRef, (state) => {
            const alertCount = state.activeAlerts.size
            return [alertCount, { ...state, activeAlerts: new Map() }]
          })

          if (count > 0) {
            yield* broadcast({
              type: "all_alerts_cleared",
              payload: {
                count,
                timestamp: new Date().toISOString(),
              },
            })
          }

          return count
        }),

      // ============================================
      // Test Alert
      // ============================================

      triggerTestAlert: () =>
        impl.triggerAlert({
          alertType: "signal_drop",
          severity: "info",
          title: "Test Alert",
          message: "This is a test alert",
          data: { test: true },
        }),

      // ============================================
      // Threshold Checking
      // ============================================

      checkSignalThresholds: () =>
        Effect.gen(function* () {
          const state = yield* Ref.get(stateRef)
          const config = state.config

          if (!config.enabled) {
            return []
          }

          const signalOpt = yield* signalRepo.getLatestSignal()
          if (signalOpt === null) {
            return []
          }

          const signal = signalOpt
          const triggeredAlerts: Alert[] = []

          // Helper to create and trigger alert
          const maybeAlert = (
            alertType: AlertType,
            severity: DisruptionSeverity,
            title: string,
            message: string,
            data: Record<string, unknown>
          ): Effect.Effect<void> =>
            Effect.gen(function* () {
              if (
                !isCooldownExpired(
                  state.cooldowns,
                  alertType,
                  config.cooldownMinutes
                )
              ) {
                return
              }

              if (severity === "warning" && !config.notifyOnWarning) {
                return
              }
              if (severity === "critical" && !config.notifyOnCritical) {
                return
              }

              const alert = createAlert({
                alertType,
                severity,
                title,
                message,
                data,
              })
              yield* addAlertToState(alert)
              yield* broadcast({ type: "alert", payload: alert })
              triggeredAlerts.push(alert)
            })

          // Check 5G NR SINR
          if (signal.nr_sinr !== null && signal.nr_sinr !== undefined) {
            if (signal.nr_sinr < config.sinrCriticalThreshold) {
              yield* maybeAlert(
                "signal_critical",
                "critical",
                "5G Signal Critical",
                `5G SINR dropped to ${signal.nr_sinr} dB (threshold: ${config.sinrCriticalThreshold} dB)`,
                {
                  metric: "nr_sinr",
                  value: signal.nr_sinr,
                  threshold: config.sinrCriticalThreshold,
                }
              )
            } else if (signal.nr_sinr < config.sinrWarningThreshold) {
              yield* maybeAlert(
                "signal_drop",
                "warning",
                "5G Signal Low",
                `5G SINR at ${signal.nr_sinr} dB (threshold: ${config.sinrWarningThreshold} dB)`,
                {
                  metric: "nr_sinr",
                  value: signal.nr_sinr,
                  threshold: config.sinrWarningThreshold,
                }
              )
            }
          }

          // Check 5G NR RSRP
          if (signal.nr_rsrp !== null && signal.nr_rsrp !== undefined) {
            if (signal.nr_rsrp < config.rsrpCriticalThreshold) {
              yield* maybeAlert(
                "signal_critical",
                "critical",
                "5G Signal Strength Critical",
                `5G RSRP dropped to ${signal.nr_rsrp} dBm (threshold: ${config.rsrpCriticalThreshold} dBm)`,
                {
                  metric: "nr_rsrp",
                  value: signal.nr_rsrp,
                  threshold: config.rsrpCriticalThreshold,
                }
              )
            } else if (signal.nr_rsrp < config.rsrpWarningThreshold) {
              yield* maybeAlert(
                "signal_drop",
                "warning",
                "5G Signal Strength Low",
                `5G RSRP at ${signal.nr_rsrp} dBm (threshold: ${config.rsrpWarningThreshold} dBm)`,
                {
                  metric: "nr_rsrp",
                  value: signal.nr_rsrp,
                  threshold: config.rsrpWarningThreshold,
                }
              )
            }
          }

          // Check 4G LTE SINR (fallback when 5G is unavailable or degraded)
          if (signal.lte_sinr !== null && signal.lte_sinr !== undefined) {
            if (signal.lte_sinr < config.sinrCriticalThreshold) {
              yield* maybeAlert(
                "signal_critical",
                "critical",
                "4G Signal Critical",
                `4G SINR dropped to ${signal.lte_sinr} dB (threshold: ${config.sinrCriticalThreshold} dB)`,
                {
                  metric: "lte_sinr",
                  value: signal.lte_sinr,
                  threshold: config.sinrCriticalThreshold,
                }
              )
            }
          }

          // Check 4G LTE RSRP
          if (signal.lte_rsrp !== null && signal.lte_rsrp !== undefined) {
            if (signal.lte_rsrp < config.rsrpCriticalThreshold) {
              yield* maybeAlert(
                "signal_critical",
                "critical",
                "4G Signal Strength Critical",
                `4G RSRP dropped to ${signal.lte_rsrp} dBm (threshold: ${config.rsrpCriticalThreshold} dBm)`,
                {
                  metric: "lte_rsrp",
                  value: signal.lte_rsrp,
                  threshold: config.rsrpCriticalThreshold,
                }
              )
            }
          }

          // Check speedtest results for slow speeds
          const speedtest = yield* signalRepo.getLatestSpeedtest()
          if (speedtest !== null) {
            if (speedtest.download_mbps < config.speedLowThresholdMbps) {
              yield* maybeAlert(
                "speed_low",
                "warning",
                "Slow Download Speed",
                `Download speed ${speedtest.download_mbps.toFixed(1)} Mbps below threshold (${config.speedLowThresholdMbps} Mbps)`,
                {
                  metric: "download_mbps",
                  value: speedtest.download_mbps,
                  threshold: config.speedLowThresholdMbps,
                }
              )
            }

            // Check packet loss
            if (
              speedtest.packet_loss_percent !== null &&
              speedtest.packet_loss_percent !== undefined &&
              speedtest.packet_loss_percent > config.packetLossThresholdPercent
            ) {
              yield* maybeAlert(
                "packet_loss",
                "warning",
                "High Packet Loss",
                `Packet loss ${speedtest.packet_loss_percent.toFixed(1)}% exceeds threshold (${config.packetLossThresholdPercent}%)`,
                {
                  metric: "packet_loss_percent",
                  value: speedtest.packet_loss_percent,
                  threshold: config.packetLossThresholdPercent,
                }
              )
            }

            // Check jitter
            if (
              speedtest.jitter_ms !== null &&
              speedtest.jitter_ms !== undefined &&
              speedtest.jitter_ms > config.jitterThresholdMs
            ) {
              yield* maybeAlert(
                "high_jitter",
                "warning",
                "High Jitter",
                `Jitter ${speedtest.jitter_ms.toFixed(1)} ms exceeds threshold (${config.jitterThresholdMs} ms)`,
                {
                  metric: "jitter_ms",
                  value: speedtest.jitter_ms,
                  threshold: config.jitterThresholdMs,
                }
              )
            }
          }

          return triggeredAlerts
        }),

      // ============================================
      // SSE Streaming
      // ============================================

      subscribe: () => {
        // Create stream from pubsub subscription with heartbeats
        const eventStream = Stream.unwrapScoped(
          Effect.gen(function* () {
            const subscription = yield* PubSub.subscribe(pubsub)
            return Stream.fromQueue(subscription)
          })
        )

        const heartbeatStream = Stream.repeat(
          Stream.succeed({
            type: "heartbeat" as const,
            payload: { timestamp: new Date().toISOString() },
          }),
          Schedule.spaced("30 seconds")
        )

        return Stream.merge(eventStream, heartbeatStream)
      },
    }

    return impl
  })
)

// ============================================
// Helper Layer Composition
// ============================================

export const makeAlertServiceLayer = (
  signalRepoLayer: Layer.Layer<SignalRepository, RepositoryError>
): Layer.Layer<AlertService, RepositoryError> =>
  Layer.provide(AlertServiceLive, signalRepoLayer)
