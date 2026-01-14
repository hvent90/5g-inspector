/**
 * Effect Schema definitions for alert-related data types.
 * These match the Python models in backend/src/netpulse/models.py
 */

import { Schema } from "effect"
import { DisruptionSeverity } from "./Signal"

// ============================================
// Alert Types
// ============================================

export const AlertType = Schema.Literal(
  "signal_drop",
  "signal_critical",
  "tower_change",
  "speed_low",
  "packet_loss",
  "high_jitter"
)
export type AlertType = typeof AlertType.Type

// ============================================
// Alert Configuration
// ============================================

export const AlertConfig = Schema.Struct({
  enabled: Schema.Boolean,

  // Signal thresholds (based on CLAUDE.md quality thresholds)
  sinrCriticalThreshold: Schema.Number, // Below this = critical (-5)
  sinrWarningThreshold: Schema.Number, // Below this = warning (0)
  rsrpCriticalThreshold: Schema.Number, // Below this = critical (-110)
  rsrpWarningThreshold: Schema.Number, // Below this = warning (-100)
  rsrqCriticalThreshold: Schema.Number, // Below this = critical (-19)
  rsrqWarningThreshold: Schema.Number, // Below this = warning (-15)

  // Speed thresholds
  speedLowThresholdMbps: Schema.Number, // Default: 10.0

  // Network quality thresholds
  packetLossThresholdPercent: Schema.Number, // Default: 5.0
  jitterThresholdMs: Schema.Number, // Default: 50.0

  // Signal drop detection
  signalDropThresholdDb: Schema.Number, // Default: 10.0

  // Notification settings
  notifyOnWarning: Schema.Boolean,
  notifyOnCritical: Schema.Boolean,
  cooldownMinutes: Schema.Number, // Default: 5
})

export type AlertConfig = typeof AlertConfig.Type

// ============================================
// Alert
// ============================================

export const Alert = Schema.Struct({
  id: Schema.String,
  createdAt: Schema.String, // ISO 8601 timestamp

  alertType: AlertType,
  severity: DisruptionSeverity,
  title: Schema.String,
  message: Schema.String,
  data: Schema.Record({ key: Schema.String, value: Schema.Unknown }),

  acknowledged: Schema.Boolean,
  acknowledgedAt: Schema.optionalWith(Schema.String, { nullable: true }),
  acknowledgedBy: Schema.optionalWith(Schema.String, { nullable: true }),

  resolved: Schema.Boolean,
  resolvedAt: Schema.optionalWith(Schema.String, { nullable: true }),
})

export type Alert = typeof Alert.Type

// ============================================
// Alert Input (for triggering alerts)
// ============================================

export const TriggerAlertInput = Schema.Struct({
  alertType: AlertType,
  severity: DisruptionSeverity,
  title: Schema.String,
  message: Schema.String,
  data: Schema.optional(
    Schema.Record({ key: Schema.String, value: Schema.Unknown })
  ),
})

export type TriggerAlertInput = typeof TriggerAlertInput.Type

// ============================================
// SSE Event Types
// ============================================

export const AlertSSEEvent = Schema.Union(
  Schema.Struct({
    type: Schema.Literal("alert"),
    payload: Alert,
  }),
  Schema.Struct({
    type: Schema.Literal("alert_cleared"),
    payload: Schema.Struct({
      alertId: Schema.String,
      timestamp: Schema.String,
    }),
  }),
  Schema.Struct({
    type: Schema.Literal("all_alerts_cleared"),
    payload: Schema.Struct({
      count: Schema.Number,
      timestamp: Schema.String,
    }),
  }),
  Schema.Struct({
    type: Schema.Literal("heartbeat"),
    payload: Schema.Struct({
      timestamp: Schema.String,
    }),
  })
)

export type AlertSSEEvent = typeof AlertSSEEvent.Type

// ============================================
// Alert History Query
// ============================================

export const AlertHistoryQuery = Schema.Struct({
  limit: Schema.optional(Schema.Number),
  offset: Schema.optional(Schema.Number),
})

export type AlertHistoryQuery = typeof AlertHistoryQuery.Type

// ============================================
// Default Configuration
// ============================================

export const DEFAULT_ALERT_CONFIG: AlertConfig = {
  enabled: true,
  sinrCriticalThreshold: -5,
  sinrWarningThreshold: 0,
  rsrpCriticalThreshold: -110,
  rsrpWarningThreshold: -100,
  rsrqCriticalThreshold: -19,
  rsrqWarningThreshold: -15,
  speedLowThresholdMbps: 10.0,
  packetLossThresholdPercent: 5.0,
  jitterThresholdMs: 50.0,
  signalDropThresholdDb: 10.0,
  notifyOnWarning: true,
  notifyOnCritical: true,
  cooldownMinutes: 5,
}
