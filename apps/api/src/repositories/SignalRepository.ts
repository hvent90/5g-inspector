/**
 * SignalRepository - Effect service for accessing signal history data.
 *
 * This provides a typed interface for querying signal records from the database.
 * Used by DisruptionService to compare current vs previous signal state.
 */

import { Context, Effect, Layer } from "effect"
import type { SignalHistoryRecord } from "../schema/Signal"

// ============================================
// Service Interface
// ============================================

export interface SignalRepository {
  /**
   * Get the most recent signal record from the database.
   */
  readonly getLatest: Effect.Effect<SignalHistoryRecord | null>

  /**
   * Get the previous signal record (second most recent).
   * Used for comparison in disruption detection.
   */
  readonly getPrevious: Effect.Effect<SignalHistoryRecord | null>

  /**
   * Query signal history for a given duration.
   */
  readonly queryHistory: (params: {
    durationMinutes: number
    resolution?: string
    limit?: number
  }) => Effect.Effect<readonly SignalHistoryRecord[]>

  /**
   * Get tower/cell changes over time.
   */
  readonly getTowerHistory: (
    durationMinutes: number
  ) => Effect.Effect<readonly TowerChange[]>
}

export interface TowerChange {
  readonly timestamp: string
  readonly timestampUnix: number
  readonly nrGnbId: number | null
  readonly nrCid: number | null
  readonly lteEnbId: number | null
  readonly lteCid: number | null
  readonly changeType: "5g" | "4g"
}

// ============================================
// Service Tag
// ============================================

export class SignalRepository extends Context.Tag("SignalRepository")<
  SignalRepository,
  SignalRepository
>() {}

// ============================================
// Mock Implementation (for testing)
// ============================================

export const SignalRepositoryMock = Layer.succeed(SignalRepository, {
  getLatest: Effect.succeed(null),
  getPrevious: Effect.succeed(null),
  queryHistory: () => Effect.succeed([]),
  getTowerHistory: () => Effect.succeed([]),
})
