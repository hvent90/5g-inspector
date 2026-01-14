/**
 * DisruptionRepository - Effect service for storing disruption events.
 *
 * Provides typed interface for persisting disruption events to the database.
 */

import { Context, Effect, Layer } from "effect"
import type {
  DisruptionEventInsert,
  DisruptionEventRecord,
  DisruptionStats,
} from "../schema/Signal"

// ============================================
// Service Interface
// ============================================

export interface DisruptionRepository {
  /**
   * Insert a disruption event. Returns the row ID.
   */
  readonly insert: (
    event: DisruptionEventInsert
  ) => Effect.Effect<number>

  /**
   * Query disruption events for a given duration.
   */
  readonly query: (
    durationHours: number
  ) => Effect.Effect<readonly DisruptionEventRecord[]>

  /**
   * Get disruption statistics.
   */
  readonly getStats: (durationHours: number) => Effect.Effect<DisruptionStats>

  /**
   * Mark a disruption event as resolved.
   */
  readonly resolve: (params: {
    eventId: number
    durationSeconds: number
    resolvedAt: string
    afterState?: Record<string, unknown>
  }) => Effect.Effect<boolean>
}

// ============================================
// Service Tag
// ============================================

export class DisruptionRepository extends Context.Tag("DisruptionRepository")<
  DisruptionRepository,
  DisruptionRepository
>() {}

// ============================================
// Mock Implementation (for testing)
// ============================================

export const DisruptionRepositoryMock = Layer.succeed(DisruptionRepository, {
  insert: () => Effect.succeed(1),
  query: () => Effect.succeed([]),
  getStats: (durationHours) =>
    Effect.succeed({
      period_hours: durationHours,
      total_events: 0,
      events_by_type: {},
      events_by_severity: {},
      avg_duration_seconds: null,
    }),
  resolve: () => Effect.succeed(true),
})
