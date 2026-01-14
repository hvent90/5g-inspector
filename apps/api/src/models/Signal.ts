/**
 * Signal data models using Effect Schema
 * Mirrors the Python models from backend/src/netpulse/models.py
 */
import { Schema } from "@effect/schema"

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
export type SignalQuality = Schema.Schema.Type<typeof SignalQuality>

export const ConnectionMode = Schema.Literal("SA", "NSA", "LTE", "No Signal")
export type ConnectionMode = Schema.Schema.Type<typeof ConnectionMode>

export const CircuitState = Schema.Literal("closed", "open", "half_open")
export type CircuitState = Schema.Schema.Type<typeof CircuitState>

// ============================================
// Signal Metrics
// ============================================

export class SignalMetrics extends Schema.Class<SignalMetrics>("SignalMetrics")({
  sinr: Schema.NullOr(Schema.Number),
  rsrp: Schema.NullOr(Schema.Number),
  rsrq: Schema.NullOr(Schema.Number),
  rssi: Schema.NullOr(Schema.Number),
  bands: Schema.Array(Schema.String).pipe(Schema.optional),
  tower_id: Schema.NullOr(Schema.Number),
  cell_id: Schema.NullOr(Schema.Number),
}) {
  /**
   * Calculate signal quality from SINR
   */
  get quality(): SignalQuality {
    if (this.sinr === null) return "poor"
    if (this.sinr >= 20) return "excellent"
    if (this.sinr >= 10) return "good"
    if (this.sinr >= 0) return "fair"
    if (this.sinr >= -5) return "poor"
    return "critical"
  }
}

// ============================================
// Gateway Response Schema
// ============================================

/**
 * Schema for parsing the raw T-Mobile gateway JSON response
 * Expected structure: { signal: { "5g": {...}, "4g": {...} }, device: {...} }
 */
export const GatewayResponseSchema = Schema.Struct({
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

export type GatewayResponse = Schema.Schema.Type<typeof GatewayResponseSchema>

// ============================================
// Signal Data (parsed and normalized)
// ============================================

export class SignalData extends Schema.Class<SignalData>("SignalData")({
  timestamp: Schema.Date,
  timestamp_unix: Schema.Number,
  nr: SignalMetrics,
  lte: SignalMetrics,
  registration_status: Schema.NullOr(Schema.String),
  connection_mode: ConnectionMode,
  device_uptime: Schema.NullOr(Schema.Number),
}) {}

// ============================================
// Database Record Schema
// ============================================

export class SignalRecord extends Schema.Class<SignalRecord>("SignalRecord")({
  id: Schema.optional(Schema.Number),
  timestamp: Schema.String,
  timestamp_unix: Schema.Number,
  // 5G NR fields
  nr_sinr: Schema.NullOr(Schema.Number),
  nr_rsrp: Schema.NullOr(Schema.Number),
  nr_rsrq: Schema.NullOr(Schema.Number),
  nr_rssi: Schema.NullOr(Schema.Number),
  nr_bands: Schema.NullOr(Schema.String),
  nr_gnb_id: Schema.NullOr(Schema.Number),
  nr_cid: Schema.NullOr(Schema.Number),
  // 4G LTE fields
  lte_sinr: Schema.NullOr(Schema.Number),
  lte_rsrp: Schema.NullOr(Schema.Number),
  lte_rsrq: Schema.NullOr(Schema.Number),
  lte_rssi: Schema.NullOr(Schema.Number),
  lte_bands: Schema.NullOr(Schema.String),
  lte_enb_id: Schema.NullOr(Schema.Number),
  lte_cid: Schema.NullOr(Schema.Number),
  // Device info
  registration_status: Schema.NullOr(Schema.String),
  device_uptime: Schema.NullOr(Schema.Number),
}) {}

// ============================================
// Outage Event
// ============================================

export class OutageEvent extends Schema.Class<OutageEvent>("OutageEvent")({
  start_time: Schema.Number,
  end_time: Schema.NullOr(Schema.Number),
  duration_seconds: Schema.NullOr(Schema.Number),
  error_count: Schema.Number,
  last_error: Schema.NullOr(Schema.String),
  resolved: Schema.Boolean,
}) {}

// ============================================
// Gateway Stats
// ============================================

export class GatewayStats extends Schema.Class<GatewayStats>("GatewayStats")({
  last_success: Schema.Number,
  last_attempt: Schema.Number,
  success_count: Schema.Number,
  error_count: Schema.Number,
  last_error: Schema.NullOr(Schema.String),
  circuit_state: CircuitState,
  is_running: Schema.Boolean,
}) {}
