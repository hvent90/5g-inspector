/**
 * Effect Schema definitions for health-related data types.
 */

import { Schema } from "effect"

// ============================================
// Health Status
// ============================================

export const HealthStatusValue = Schema.Literal("healthy", "degraded", "unhealthy")
export type HealthStatusValue = typeof HealthStatusValue.Type

export const HealthStatus = Schema.Struct({
  status: HealthStatusValue,
  uptime_seconds: Schema.Number,
  version: Schema.String,
  timestamp: Schema.String,
})

export type HealthStatus = typeof HealthStatus.Type

// ============================================
// Component Health
// ============================================

export const ComponentHealth = Schema.Struct({
  name: Schema.String,
  healthy: Schema.Boolean,
  message: Schema.String,
  last_check: Schema.String,
})

export type ComponentHealth = typeof ComponentHealth.Type

// ============================================
// Full Health Response (with components)
// ============================================

export const FullHealthStatus = Schema.Struct({
  status: HealthStatusValue,
  uptime_seconds: Schema.Number,
  version: Schema.String,
  timestamp: Schema.String,
  components: Schema.Array(ComponentHealth),
  last_signal_poll: Schema.optionalWith(Schema.String, { nullable: true }),
  signal_poll_success_rate: Schema.optionalWith(Schema.Number, { nullable: true }),
  db_connected: Schema.Boolean,
  active_alerts: Schema.Number,
})

export type FullHealthStatus = typeof FullHealthStatus.Type

// ============================================
// Probe Responses
// ============================================

export const LiveProbeResponse = Schema.Struct({
  status: Schema.Literal("ok"),
})

export type LiveProbeResponse = typeof LiveProbeResponse.Type

export const ReadyProbeResponse = Schema.Struct({
  status: Schema.Literal("ready", "not_ready"),
})

export type ReadyProbeResponse = typeof ReadyProbeResponse.Type
