/**
 * Effect Schema definitions for signal-related data types.
 * These match the Python models in backend/src/netpulse/models.py
 */

import { Schema } from "effect"

// ============================================
// Enums
// ============================================

export const SignalQuality = Schema.Literal(
  "excellent",
  "good",
  "fair",
  "poor",
  "critical"
)
export type SignalQuality = typeof SignalQuality.Type

export const ConnectionMode = Schema.Literal("SA", "NSA", "LTE", "No Signal")
export type ConnectionMode = typeof ConnectionMode.Type

export const DisruptionSeverity = Schema.Literal("info", "warning", "critical")
export type DisruptionSeverity = typeof DisruptionSeverity.Type

export const NetworkContext = Schema.Literal(
  "baseline",
  "idle",
  "light",
  "busy",
  "unknown"
)
export type NetworkContext = typeof NetworkContext.Type

// ============================================
// Signal History Record (DB row)
// ============================================

export const SignalHistoryRecord = Schema.Struct({
  id: Schema.optionalWith(Schema.Number, { nullable: true }),
  timestamp: Schema.String,
  timestamp_unix: Schema.Number,

  // 5G NR fields
  nr_sinr: Schema.optionalWith(Schema.Number, { nullable: true }),
  nr_rsrp: Schema.optionalWith(Schema.Number, { nullable: true }),
  nr_rsrq: Schema.optionalWith(Schema.Number, { nullable: true }),
  nr_rssi: Schema.optionalWith(Schema.Number, { nullable: true }),
  nr_bands: Schema.optionalWith(Schema.String, { nullable: true }),
  nr_gnb_id: Schema.optionalWith(Schema.Number, { nullable: true }),
  nr_cid: Schema.optionalWith(Schema.Number, { nullable: true }),

  // 4G LTE fields
  lte_sinr: Schema.optionalWith(Schema.Number, { nullable: true }),
  lte_rsrp: Schema.optionalWith(Schema.Number, { nullable: true }),
  lte_rsrq: Schema.optionalWith(Schema.Number, { nullable: true }),
  lte_rssi: Schema.optionalWith(Schema.Number, { nullable: true }),
  lte_bands: Schema.optionalWith(Schema.String, { nullable: true }),
  lte_enb_id: Schema.optionalWith(Schema.Number, { nullable: true }),
  lte_cid: Schema.optionalWith(Schema.Number, { nullable: true }),

  // Device info
  registration_status: Schema.optionalWith(Schema.String, { nullable: true }),
  device_uptime: Schema.optionalWith(Schema.Number, { nullable: true }),
})

export type SignalHistoryRecord = typeof SignalHistoryRecord.Type

// Input type for inserting (without id)
export const SignalHistoryInsert = Schema.Struct({
  timestamp: Schema.String,
  timestamp_unix: Schema.Number,
  nr_sinr: Schema.optionalWith(Schema.Number, { nullable: true }),
  nr_rsrp: Schema.optionalWith(Schema.Number, { nullable: true }),
  nr_rsrq: Schema.optionalWith(Schema.Number, { nullable: true }),
  nr_rssi: Schema.optionalWith(Schema.Number, { nullable: true }),
  nr_bands: Schema.optionalWith(Schema.String, { nullable: true }),
  nr_gnb_id: Schema.optionalWith(Schema.Number, { nullable: true }),
  nr_cid: Schema.optionalWith(Schema.Number, { nullable: true }),
  lte_sinr: Schema.optionalWith(Schema.Number, { nullable: true }),
  lte_rsrp: Schema.optionalWith(Schema.Number, { nullable: true }),
  lte_rsrq: Schema.optionalWith(Schema.Number, { nullable: true }),
  lte_rssi: Schema.optionalWith(Schema.Number, { nullable: true }),
  lte_bands: Schema.optionalWith(Schema.String, { nullable: true }),
  lte_enb_id: Schema.optionalWith(Schema.Number, { nullable: true }),
  lte_cid: Schema.optionalWith(Schema.Number, { nullable: true }),
  registration_status: Schema.optionalWith(Schema.String, { nullable: true }),
  device_uptime: Schema.optionalWith(Schema.Number, { nullable: true }),
})

export type SignalHistoryInsert = typeof SignalHistoryInsert.Type

// ============================================
// Speedtest Result (DB row)
// ============================================

export const SpeedtestResultRecord = Schema.Struct({
  id: Schema.optionalWith(Schema.Number, { nullable: true }),
  timestamp: Schema.String,
  timestamp_unix: Schema.Number,

  // Speed metrics
  download_mbps: Schema.Number,
  upload_mbps: Schema.Number,
  ping_ms: Schema.Number,
  jitter_ms: Schema.optionalWith(Schema.Number, { nullable: true }),
  packet_loss_percent: Schema.optionalWith(Schema.Number, { nullable: true }),

  // Server info
  server_name: Schema.optionalWith(Schema.String, { nullable: true }),
  server_location: Schema.optionalWith(Schema.String, { nullable: true }),
  server_host: Schema.optionalWith(Schema.String, { nullable: true }),
  server_id: Schema.optionalWith(Schema.Number, { nullable: true }),

  // Client/ISP info
  client_ip: Schema.optionalWith(Schema.String, { nullable: true }),
  isp: Schema.optionalWith(Schema.String, { nullable: true }),

  // Tool info
  tool: Schema.String,
  result_url: Schema.optionalWith(Schema.String, { nullable: true }),

  // Signal snapshot (JSON string in DB)
  signal_snapshot: Schema.optionalWith(Schema.String, { nullable: true }),

  // Test metadata
  status: Schema.String,
  error_message: Schema.optionalWith(Schema.String, { nullable: true }),
  triggered_by: Schema.String,
  network_context: NetworkContext,
  pre_test_latency_ms: Schema.optionalWith(Schema.Number, { nullable: true }),
})

export type SpeedtestResultRecord = typeof SpeedtestResultRecord.Type

export const SpeedtestResultInsert = Schema.Struct({
  timestamp: Schema.String,
  timestamp_unix: Schema.Number,
  download_mbps: Schema.Number,
  upload_mbps: Schema.Number,
  ping_ms: Schema.Number,
  jitter_ms: Schema.optionalWith(Schema.Number, { nullable: true }),
  packet_loss_percent: Schema.optionalWith(Schema.Number, { nullable: true }),
  server_name: Schema.optionalWith(Schema.String, { nullable: true }),
  server_location: Schema.optionalWith(Schema.String, { nullable: true }),
  server_host: Schema.optionalWith(Schema.String, { nullable: true }),
  server_id: Schema.optionalWith(Schema.Number, { nullable: true }),
  client_ip: Schema.optionalWith(Schema.String, { nullable: true }),
  isp: Schema.optionalWith(Schema.String, { nullable: true }),
  tool: Schema.String,
  result_url: Schema.optionalWith(Schema.String, { nullable: true }),
  signal_snapshot: Schema.optionalWith(Schema.String, { nullable: true }),
  status: Schema.String,
  error_message: Schema.optionalWith(Schema.String, { nullable: true }),
  triggered_by: Schema.String,
  network_context: NetworkContext,
  pre_test_latency_ms: Schema.optionalWith(Schema.Number, { nullable: true }),
})

export type SpeedtestResultInsert = typeof SpeedtestResultInsert.Type

// ============================================
// Disruption Event (DB row)
// ============================================

export const DisruptionEventRecord = Schema.Struct({
  id: Schema.optionalWith(Schema.Number, { nullable: true }),
  timestamp: Schema.String,
  timestamp_unix: Schema.Number,

  event_type: Schema.String,
  severity: DisruptionSeverity,
  description: Schema.String,

  // JSON strings in DB
  before_state: Schema.optionalWith(Schema.String, { nullable: true }),
  after_state: Schema.optionalWith(Schema.String, { nullable: true }),

  duration_seconds: Schema.optionalWith(Schema.Number, { nullable: true }),
  resolved: Schema.Number, // 0 or 1 in SQLite
  resolved_at: Schema.optionalWith(Schema.String, { nullable: true }),
})

export type DisruptionEventRecord = typeof DisruptionEventRecord.Type

export const DisruptionEventInsert = Schema.Struct({
  timestamp: Schema.String,
  timestamp_unix: Schema.Number,
  event_type: Schema.String,
  severity: DisruptionSeverity,
  description: Schema.String,
  before_state: Schema.optionalWith(Schema.String, { nullable: true }),
  after_state: Schema.optionalWith(Schema.String, { nullable: true }),
  duration_seconds: Schema.optionalWith(Schema.Number, { nullable: true }),
  resolved: Schema.Number,
  resolved_at: Schema.optionalWith(Schema.String, { nullable: true }),
})

export type DisruptionEventInsert = typeof DisruptionEventInsert.Type

// ============================================
// Disruption Stats
// ============================================

export const DisruptionStats = Schema.Struct({
  period_hours: Schema.Number,
  total_events: Schema.Number,
  events_by_type: Schema.Record({ key: Schema.String, value: Schema.Number }),
  events_by_severity: Schema.Record({
    key: Schema.String,
    value: Schema.Number,
  }),
  avg_duration_seconds: Schema.optionalWith(Schema.Number, { nullable: true }),
})

export type DisruptionStats = typeof DisruptionStats.Type

// ============================================
// Tower Change Record
// ============================================

export const TowerChangeRecord = Schema.Struct({
  timestamp: Schema.String,
  timestamp_unix: Schema.Number,
  nr_gnb_id: Schema.optionalWith(Schema.Number, { nullable: true }),
  nr_cid: Schema.optionalWith(Schema.Number, { nullable: true }),
  lte_enb_id: Schema.optionalWith(Schema.Number, { nullable: true }),
  lte_cid: Schema.optionalWith(Schema.Number, { nullable: true }),
  change_type: Schema.Literal("5g", "4g"),
})

export type TowerChangeRecord = typeof TowerChangeRecord.Type

// ============================================
// Query Parameters
// ============================================

export const HistoryQueryParams = Schema.Struct({
  duration_minutes: Schema.optional(Schema.Number),
  resolution: Schema.optional(Schema.String),
  limit: Schema.optionalWith(Schema.Number, { nullable: true }),
})

export type HistoryQueryParams = typeof HistoryQueryParams.Type
