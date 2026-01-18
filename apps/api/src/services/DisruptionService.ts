/**
 * DisruptionService - Effect service for detecting and recording network disruptions.
 *
 * Migrated from Python: backend/src/netpulse/services/disruption.py
 *
 * Monitors signal changes and detects:
 * - 5G signal drops (SINR drop >= 10dB)
 * - 4G signal drops (SINR drop >= 10dB)
 * - 5G tower changes (gNB ID changes)
 * - 4G tower changes (eNB ID changes)
 * - Band switches (when nr_bands or lte_bands change)
 * - Connection mode changes (SA/NSA/LTE transitions)
 * - Gateway outages
 */

import { Context, Effect, Layer, Ref } from "effect"
import type {
  DisruptionEventInsert,
  DisruptionEventRecord,
  DisruptionSeverity,
  DisruptionStats,
  SignalHistoryRecord,
} from "../schema/Signal"
import { SignalRepository, RepositoryError } from "./SignalRepository"

// ============================================
// Types
// ============================================

/**
 * A detected disruption event before persistence.
 */
export interface DetectedDisruption {
  readonly eventType: DisruptionEventType
  readonly severity: DisruptionSeverity
  readonly description: string
  readonly beforeState: Record<string, unknown>
  readonly afterState: Record<string, unknown>
}

/**
 * Known disruption event types.
 */
export type DisruptionEventType =
  | "signal_drop_5g"
  | "signal_drop_4g"
  | "tower_change_5g"
  | "tower_change_4g"
  | "band_switch_5g"
  | "band_switch_4g"
  | "connection_mode_change"
  | "gateway_unreachable"

/**
 * Configuration for disruption detection thresholds.
 */
export interface DisruptionConfig {
  /** SINR drop threshold for 5G (default: 10 dB) */
  readonly sinrDrop5g: number
  /** SINR drop threshold for 4G (default: 10 dB) */
  readonly sinrDrop4g: number
  /** Cooldown between same type events in seconds (default: 60) */
  readonly cooldownSeconds: number
}

const defaultConfig: DisruptionConfig = {
  sinrDrop5g: 10,
  sinrDrop4g: 10,
  cooldownSeconds: 60,
}

// ============================================
// Service Tag
// ============================================

export class DisruptionService extends Context.Tag("DisruptionService")<
  DisruptionService,
  {
    /**
     * Detect disruption events by comparing current vs previous signal data.
     * Returns list of detected events (already persisted to DB).
     */
    readonly detectDisruption: (
      current: SignalHistoryRecord,
      previous: SignalHistoryRecord | null
    ) => Effect.Effect<readonly DetectedDisruption[], RepositoryError>

    /**
     * Get disruption events for the specified duration.
     */
    readonly getDisruptions: (
      durationHours?: number
    ) => Effect.Effect<readonly DisruptionEventRecord[], RepositoryError>

    /**
     * Get disruption statistics.
     */
    readonly getStats: (
      durationHours?: number
    ) => Effect.Effect<DisruptionStats, RepositoryError>

    /**
     * Create a gateway outage event when connectivity is lost.
     * Returns the event ID for later resolution.
     */
    readonly createGatewayOutageEvent: (params: {
      startTime: number
      errorCount: number
      lastError: string | null
    }) => Effect.Effect<number, RepositoryError>

    /**
     * Resolve a gateway outage event when connectivity is restored.
     */
    readonly resolveGatewayOutageEvent: (params: {
      eventId: number
      endTime: number
      durationSeconds: number
      errorCount: number
    }) => Effect.Effect<boolean, RepositoryError>
  }
>() {}

// ============================================
// Service Implementation
// ============================================

/**
 * Create the DisruptionService live layer.
 * Depends on SignalRepository for data access.
 */
export const DisruptionServiceLive = (
  config: DisruptionConfig = defaultConfig
) =>
  Layer.effect(
    DisruptionService,
    Effect.gen(function* () {
      const repo = yield* SignalRepository

      // Cooldown tracking: event type -> last event timestamp
      const cooldowns = yield* Ref.make<Map<string, number>>(new Map())

      /**
       * Check if an event type is in cooldown period.
       */
      const isInCooldown = (eventType: string) =>
        Effect.gen(function* () {
          const now = Date.now() / 1000
          const map = yield* Ref.get(cooldowns)
          const lastTime = map.get(eventType) ?? 0
          return now - lastTime < config.cooldownSeconds
        })

      /**
       * Update cooldown timestamp for an event type.
       */
      const updateCooldown = (eventType: string) =>
        Ref.update(cooldowns, (map) => {
          const newMap = new Map(map)
          newMap.set(eventType, Date.now() / 1000)
          return newMap
        })

      /**
       * Maybe create an event if not in cooldown.
       */
      const maybeCreateEvent = (
        event: DetectedDisruption
      ): Effect.Effect<DetectedDisruption | null, RepositoryError> =>
        Effect.gen(function* () {
          const inCooldown = yield* isInCooldown(event.eventType)
          if (inCooldown) {
            return null
          }

          yield* updateCooldown(event.eventType)

          const now = new Date()
          const insertData: DisruptionEventInsert = {
            timestamp: now.toISOString(),
            timestamp_unix: now.getTime() / 1000,
            event_type: event.eventType,
            severity: event.severity,
            description: event.description,
            before_state: JSON.stringify(event.beforeState),
            after_state: JSON.stringify(event.afterState),
            duration_seconds: undefined,
            resolved: 0,
            resolved_at: undefined,
          }

          yield* repo.insertDisruption(insertData)
          return event
        })

      /**
       * Check for 5G signal drop.
       */
      const check5gSignalDrop = (
        current: SignalHistoryRecord,
        previous: SignalHistoryRecord
      ): DetectedDisruption | null => {
        const currSinr = current.nr_sinr
        const prevSinr = previous.nr_sinr

        if (currSinr == null || prevSinr == null) return null

        const drop = prevSinr - currSinr
        if (drop < config.sinrDrop5g) return null

        const severity: DisruptionSeverity = drop >= 20 ? "critical" : "warning"

        return {
          eventType: "signal_drop_5g",
          severity,
          description: `5G SINR dropped by ${drop.toFixed(1)} dB`,
          beforeState: { nr_sinr: prevSinr },
          afterState: { nr_sinr: currSinr },
        }
      }

      /**
       * Check for 4G signal drop.
       */
      const check4gSignalDrop = (
        current: SignalHistoryRecord,
        previous: SignalHistoryRecord
      ): DetectedDisruption | null => {
        const currSinr = current.lte_sinr
        const prevSinr = previous.lte_sinr

        if (currSinr == null || prevSinr == null) return null

        const drop = prevSinr - currSinr
        if (drop < config.sinrDrop4g) return null

        return {
          eventType: "signal_drop_4g",
          severity: "warning",
          description: `4G SINR dropped by ${drop.toFixed(1)} dB`,
          beforeState: { lte_sinr: prevSinr },
          afterState: { lte_sinr: currSinr },
        }
      }

      /**
       * Check for 5G tower change (gNB ID change).
       */
      const check5gTowerChange = (
        current: SignalHistoryRecord,
        previous: SignalHistoryRecord
      ): DetectedDisruption | null => {
        const currGnb = current.nr_gnb_id
        const prevGnb = previous.nr_gnb_id

        if (currGnb == null || prevGnb == null) return null
        if (currGnb === prevGnb) return null

        return {
          eventType: "tower_change_5g",
          severity: "info",
          description: `5G tower changed from ${prevGnb} to ${currGnb}`,
          beforeState: { nr_gnb_id: prevGnb, nr_cid: previous.nr_cid },
          afterState: { nr_gnb_id: currGnb, nr_cid: current.nr_cid },
        }
      }

      /**
       * Check for 4G tower change (eNB ID change).
       */
      const check4gTowerChange = (
        current: SignalHistoryRecord,
        previous: SignalHistoryRecord
      ): DetectedDisruption | null => {
        const currEnb = current.lte_enb_id
        const prevEnb = previous.lte_enb_id

        if (currEnb == null || prevEnb == null) return null
        if (currEnb === prevEnb) return null

        return {
          eventType: "tower_change_4g",
          severity: "info",
          description: `4G tower changed from ${prevEnb} to ${currEnb}`,
          beforeState: { lte_enb_id: prevEnb, lte_cid: previous.lte_cid },
          afterState: { lte_enb_id: currEnb, lte_cid: current.lte_cid },
        }
      }

      /**
       * Check for 5G band switch (nr_bands change).
       */
      const check5gBandSwitch = (
        current: SignalHistoryRecord,
        previous: SignalHistoryRecord
      ): DetectedDisruption | null => {
        const currBands = current.nr_bands
        const prevBands = previous.nr_bands

        if (currBands == null || prevBands == null) return null
        if (currBands === prevBands) return null

        return {
          eventType: "band_switch_5g",
          severity: "info",
          description: `5G band changed from ${prevBands} to ${currBands}`,
          beforeState: { nr_bands: prevBands },
          afterState: { nr_bands: currBands },
        }
      }

      /**
       * Check for 4G band switch (lte_bands change).
       */
      const check4gBandSwitch = (
        current: SignalHistoryRecord,
        previous: SignalHistoryRecord
      ): DetectedDisruption | null => {
        const currBands = current.lte_bands
        const prevBands = previous.lte_bands

        if (currBands == null || prevBands == null) return null
        if (currBands === prevBands) return null

        return {
          eventType: "band_switch_4g",
          severity: "info",
          description: `4G band changed from ${prevBands} to ${currBands}`,
          beforeState: { lte_bands: prevBands },
          afterState: { lte_bands: currBands },
        }
      }

      /**
       * Infer connection mode from signal data.
       */
      const inferConnectionMode = (
        record: SignalHistoryRecord
      ): string | null => {
        const has5g = record.nr_sinr != null && record.nr_gnb_id != null
        const has4g = record.lte_sinr != null && record.lte_enb_id != null

        if (has5g && has4g) return "NSA" // Non-standalone 5G
        if (has5g) return "SA" // Standalone 5G
        if (has4g) return "LTE" // 4G only
        return "No Signal"
      }

      /**
       * Check for connection mode change.
       */
      const checkConnectionModeChange = (
        current: SignalHistoryRecord,
        previous: SignalHistoryRecord
      ): DetectedDisruption | null => {
        const currMode = inferConnectionMode(current)
        const prevMode = inferConnectionMode(previous)

        if (currMode === prevMode) return null

        // Determine severity based on change direction
        let severity: DisruptionSeverity = "info"
        if (
          (prevMode === "SA" || prevMode === "NSA") &&
          currMode === "LTE"
        ) {
          severity = "warning" // Downgrade from 5G to 4G
        } else if (currMode === "No Signal") {
          severity = "critical" // Lost all signal
        }

        return {
          eventType: "connection_mode_change",
          severity,
          description: `Connection mode changed from ${prevMode} to ${currMode}`,
          beforeState: { connection_mode: prevMode },
          afterState: { connection_mode: currMode },
        }
      }

      return {
        detectDisruption: (current, previous) =>
          Effect.gen(function* () {
            if (previous == null) {
              return []
            }

            // Run all checks
            const potentialEvents = [
              check5gSignalDrop(current, previous),
              check4gSignalDrop(current, previous),
              check5gTowerChange(current, previous),
              check4gTowerChange(current, previous),
              check5gBandSwitch(current, previous),
              check4gBandSwitch(current, previous),
              checkConnectionModeChange(current, previous),
            ].filter((e): e is DetectedDisruption => e !== null)

            // Apply cooldown and persist
            const results = yield* Effect.all(
              potentialEvents.map((e) => maybeCreateEvent(e)),
              { concurrency: 1 } // Sequential to avoid race conditions
            )

            return results.filter((e): e is DetectedDisruption => e !== null)
          }),

        getDisruptions: (durationHours = 24) =>
          repo.queryDisruptions(durationHours),

        getStats: (durationHours = 24) => repo.getDisruptionStats(durationHours),

        createGatewayOutageEvent: ({ startTime, errorCount, lastError }) =>
          Effect.gen(function* () {
            const now = new Date(startTime * 1000)
            const insertData: DisruptionEventInsert = {
              timestamp: now.toISOString(),
              timestamp_unix: startTime,
              event_type: "gateway_unreachable",
              severity: "critical",
              description: `Gateway unreachable - ${errorCount} consecutive poll failures`,
              before_state: JSON.stringify({
                error_count: errorCount,
                last_error: lastError,
              }),
              after_state: "{}",
              duration_seconds: undefined,
              resolved: 0,
              resolved_at: undefined,
            }

            const eventId = yield* repo.insertDisruption(insertData)
            return eventId
          }),

        resolveGatewayOutageEvent: ({
          eventId,
          endTime,
          durationSeconds,
          errorCount,
        }) =>
          repo.resolveDisruption(
            eventId,
            durationSeconds,
            new Date(endTime * 1000).toISOString(),
            JSON.stringify({
              recovered: true,
              total_errors_during_outage: errorCount,
            })
          ),
      }
    })
  )

