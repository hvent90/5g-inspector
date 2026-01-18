/**
 * SignalRepository - Effect-based data access for signal metrics.
 *
 * Uses @effect/sql-pg for PostgreSQL access and Effect.Service for dependency injection.
 * Provides CRUD operations for: signal_history, speedtest_results, disruption_events
 */

import { Context, Effect, Layer, Schema } from "effect"
import { SqlClient, SqlError } from "@effect/sql"
import {
  SignalHistoryRecord,
  SignalHistoryInsert,
  SpeedtestResultRecord,
  SpeedtestResultInsert,
  DisruptionEventRecord,
  DisruptionEventInsert,
  type DisruptionStats,
  type TowerChangeRecord,
  type HistoryQueryParams,
} from "../schema/Signal"

// ============================================
// Repository Errors
// ============================================

export class RepositoryError {
  readonly _tag = "RepositoryError"
  constructor(
    readonly operation: string,
    readonly message: string,
    readonly cause?: unknown
  ) {}
}

// ============================================
// Signal Repository Service
// ============================================

export class SignalRepository extends Context.Tag("SignalRepository")<
  SignalRepository,
  {
    // Signal History CRUD
    readonly insertSignalHistory: (
      records: ReadonlyArray<SignalHistoryInsert>
    ) => Effect.Effect<number, RepositoryError>

    readonly querySignalHistory: (
      params: HistoryQueryParams
    ) => Effect.Effect<ReadonlyArray<SignalHistoryRecord>, RepositoryError>

    readonly getLatestSignal: () => Effect.Effect<
      SignalHistoryRecord | null,
      RepositoryError
    >

    readonly getTowerHistory: (
      durationMinutes: number
    ) => Effect.Effect<ReadonlyArray<TowerChangeRecord>, RepositoryError>

    // Speedtest Results CRUD
    readonly insertSpeedtest: (
      result: SpeedtestResultInsert
    ) => Effect.Effect<number, RepositoryError>

    readonly querySpeedtests: (
      limit: number
    ) => Effect.Effect<ReadonlyArray<SpeedtestResultRecord>, RepositoryError>

    readonly getLatestSpeedtest: () => Effect.Effect<
      SpeedtestResultRecord | null,
      RepositoryError
    >

    // Disruption Events CRUD
    readonly insertDisruption: (
      event: DisruptionEventInsert
    ) => Effect.Effect<number, RepositoryError>

    readonly resolveDisruption: (
      eventId: number,
      durationSeconds: number,
      resolvedAt: string,
      afterState?: string
    ) => Effect.Effect<boolean, RepositoryError>

    readonly queryDisruptions: (
      durationHours: number
    ) => Effect.Effect<ReadonlyArray<DisruptionEventRecord>, RepositoryError>

    readonly getDisruptionStats: (
      durationHours: number
    ) => Effect.Effect<DisruptionStats, RepositoryError>
  }
>() {}

// ============================================
// Live Implementation
// ============================================

export const SignalRepositoryLive = Layer.effect(
  SignalRepository,
  Effect.gen(function* () {
    const sql = yield* SqlClient.SqlClient

    const parseRows = <T>(
      rows: unknown[],
      schema: Schema.Schema<T>
    ): Effect.Effect<T[], RepositoryError> =>
      Effect.forEach(rows, (row) =>
        Schema.decodeUnknown(schema)(row).pipe(
          Effect.mapError(
            (e) =>
              new RepositoryError(
                "parse",
                `Failed to parse row: ${e.message}`,
                e
              )
          )
        )
      )

    const mapSqlError = (operation: string) => (e: SqlError.SqlError) =>
      new RepositoryError(operation, `Database error: ${e.message}`, e)

    return {
      // ============================================
      // Signal History Operations
      // ============================================

      insertSignalHistory: (records) =>
        Effect.gen(function* () {
          if (records.length === 0) return 0

          // Insert all records in a batch
          for (const item of records) {
            yield* sql`
              INSERT INTO signal_history (
                timestamp, timestamp_unix,
                nr_sinr, nr_rsrp, nr_rsrq, nr_rssi, nr_bands, nr_gnb_id, nr_cid,
                lte_sinr, lte_rsrp, lte_rsrq, lte_rssi, lte_bands, lte_enb_id, lte_cid,
                registration_status, device_uptime
              ) VALUES (
                ${item.timestamp}, ${item.timestamp_unix},
                ${item.nr_sinr ?? null}, ${item.nr_rsrp ?? null}, ${item.nr_rsrq ?? null}, ${item.nr_rssi ?? null},
                ${item.nr_bands ?? null}, ${item.nr_gnb_id ?? null}, ${item.nr_cid ?? null},
                ${item.lte_sinr ?? null}, ${item.lte_rsrp ?? null}, ${item.lte_rsrq ?? null}, ${item.lte_rssi ?? null},
                ${item.lte_bands ?? null}, ${item.lte_enb_id ?? null}, ${item.lte_cid ?? null},
                ${item.registration_status ?? null}, ${item.device_uptime ?? null}
              )
            `
          }

          return records.length
        }).pipe(Effect.mapError(mapSqlError("insertSignalHistory"))),

      querySignalHistory: (params) =>
        Effect.gen(function* () {
          const durationMinutes = params.duration_minutes ?? 60
          const resolution = params.resolution ?? "auto"
          const limit = params.limit

          const cutoff = Date.now() / 1000 - durationMinutes * 60

          let rows: unknown[]

          if (resolution === "full" || durationMinutes <= 5) {
            // Return all data points
            if (limit) {
              rows = yield* sql`
                SELECT * FROM signal_history
                WHERE timestamp_unix >= ${cutoff}
                ORDER BY timestamp_unix ASC
                LIMIT ${limit}
              `
            } else {
              rows = yield* sql`
                SELECT * FROM signal_history
                WHERE timestamp_unix >= ${cutoff}
                ORDER BY timestamp_unix ASC
              `
            }
          } else {
            // Auto-downsample for longer durations
            let bucketSeconds: number
            if (resolution === "auto") {
              if (durationMinutes <= 60) {
                bucketSeconds = 5
              } else if (durationMinutes <= 360) {
                bucketSeconds = 30
              } else if (durationMinutes <= 1440) {
                bucketSeconds = 60
              } else {
                bucketSeconds = 300
              }
            } else {
              bucketSeconds = parseInt(resolution, 10) || 60
            }

            if (limit) {
              rows = yield* sql`
                SELECT
                  MIN(id) as id,
                  MIN(timestamp) as timestamp,
                  (FLOOR(timestamp_unix / ${bucketSeconds}) * ${bucketSeconds})::DOUBLE PRECISION as timestamp_unix,
                  AVG(nr_sinr) as nr_sinr,
                  AVG(nr_rsrp) as nr_rsrp,
                  AVG(nr_rsrq) as nr_rsrq,
                  AVG(nr_rssi) as nr_rssi,
                  MAX(nr_bands) as nr_bands,
                  MAX(nr_gnb_id)::INTEGER as nr_gnb_id,
                  MAX(nr_cid)::INTEGER as nr_cid,
                  AVG(lte_sinr) as lte_sinr,
                  AVG(lte_rsrp) as lte_rsrp,
                  AVG(lte_rsrq) as lte_rsrq,
                  AVG(lte_rssi) as lte_rssi,
                  MAX(lte_bands) as lte_bands,
                  MAX(lte_enb_id)::INTEGER as lte_enb_id,
                  MAX(lte_cid)::INTEGER as lte_cid,
                  MAX(registration_status) as registration_status,
                  MAX(device_uptime)::INTEGER as device_uptime
                FROM signal_history
                WHERE timestamp_unix >= ${cutoff}
                GROUP BY FLOOR(timestamp_unix / ${bucketSeconds})
                ORDER BY timestamp_unix ASC
                LIMIT ${limit}
              `
            } else {
              rows = yield* sql`
                SELECT
                  MIN(id) as id,
                  MIN(timestamp) as timestamp,
                  (FLOOR(timestamp_unix / ${bucketSeconds}) * ${bucketSeconds})::DOUBLE PRECISION as timestamp_unix,
                  AVG(nr_sinr) as nr_sinr,
                  AVG(nr_rsrp) as nr_rsrp,
                  AVG(nr_rsrq) as nr_rsrq,
                  AVG(nr_rssi) as nr_rssi,
                  MAX(nr_bands) as nr_bands,
                  MAX(nr_gnb_id)::INTEGER as nr_gnb_id,
                  MAX(nr_cid)::INTEGER as nr_cid,
                  AVG(lte_sinr) as lte_sinr,
                  AVG(lte_rsrp) as lte_rsrp,
                  AVG(lte_rsrq) as lte_rsrq,
                  AVG(lte_rssi) as lte_rssi,
                  MAX(lte_bands) as lte_bands,
                  MAX(lte_enb_id)::INTEGER as lte_enb_id,
                  MAX(lte_cid)::INTEGER as lte_cid,
                  MAX(registration_status) as registration_status,
                  MAX(device_uptime)::INTEGER as device_uptime
                FROM signal_history
                WHERE timestamp_unix >= ${cutoff}
                GROUP BY FLOOR(timestamp_unix / ${bucketSeconds})
                ORDER BY timestamp_unix ASC
              `
            }
          }

          return yield* parseRows(rows, SignalHistoryRecord)
        }).pipe(Effect.mapError(mapSqlError("querySignalHistory"))),

      getLatestSignal: () =>
        Effect.gen(function* () {
          const rows = yield* sql`
            SELECT * FROM signal_history ORDER BY timestamp_unix DESC LIMIT 1
          `

          if (rows.length === 0) return null

          return yield* Schema.decodeUnknown(SignalHistoryRecord)(rows[0]).pipe(
            Effect.mapError(
              (e) =>
                new RepositoryError(
                  "getLatestSignal",
                  `Parse failed: ${e.message}`,
                  e
                )
            )
          )
        }).pipe(Effect.mapError(mapSqlError("getLatestSignal"))),

      getTowerHistory: (durationMinutes) =>
        Effect.gen(function* () {
          const cutoff = Date.now() / 1000 - durationMinutes * 60

          const rows = (yield* sql`
            SELECT
              timestamp, timestamp_unix,
              nr_gnb_id, nr_cid, nr_bands,
              lte_enb_id, lte_cid, lte_bands
            FROM signal_history
            WHERE timestamp_unix >= ${cutoff}
            ORDER BY timestamp_unix ASC
          `) as Array<{
            timestamp: string
            timestamp_unix: number
            nr_gnb_id: number | null
            nr_cid: number | null
            lte_enb_id: number | null
            lte_cid: number | null
          }>

          // Find tower changes
          const changes: TowerChangeRecord[] = []
          let prevNrGnb: number | null = null
          let prevLteEnb: number | null = null

          for (const row of rows) {
            const nrGnb = row.nr_gnb_id
            const lteEnb = row.lte_enb_id

            if (nrGnb !== prevNrGnb || lteEnb !== prevLteEnb) {
              changes.push({
                timestamp: row.timestamp,
                timestamp_unix: row.timestamp_unix,
                nr_gnb_id: nrGnb,
                nr_cid: row.nr_cid,
                lte_enb_id: lteEnb,
                lte_cid: row.lte_cid,
                change_type: nrGnb !== prevNrGnb ? "5g" : "4g",
              })
              prevNrGnb = nrGnb
              prevLteEnb = lteEnb
            }
          }

          return changes
        }).pipe(Effect.mapError(mapSqlError("getTowerHistory"))),

      // ============================================
      // Speedtest Operations
      // ============================================

      insertSpeedtest: (result) =>
        Effect.gen(function* () {
          const rows = yield* sql`
            INSERT INTO speedtest_results (
              timestamp, timestamp_unix, download_mbps, upload_mbps, ping_ms,
              jitter_ms, packet_loss_percent, server_name, server_location,
              server_host, server_id, client_ip, isp, tool, result_url,
              signal_snapshot, status, error_message, triggered_by,
              network_context, pre_test_latency_ms
            ) VALUES (
              ${result.timestamp}, ${result.timestamp_unix},
              ${result.download_mbps}, ${result.upload_mbps}, ${result.ping_ms},
              ${result.jitter_ms ?? null}, ${result.packet_loss_percent ?? null},
              ${result.server_name ?? null}, ${result.server_location ?? null},
              ${result.server_host ?? null}, ${result.server_id ?? null},
              ${result.client_ip ?? null}, ${result.isp ?? null},
              ${result.tool}, ${result.result_url ?? null},
              ${result.signal_snapshot ?? null}, ${result.status},
              ${result.error_message ?? null}, ${result.triggered_by},
              ${result.network_context}, ${result.pre_test_latency_ms ?? null}
            ) RETURNING id
          `
          return (rows[0] as { id: number }).id
        }).pipe(Effect.mapError(mapSqlError("insertSpeedtest"))),

      querySpeedtests: (limit) =>
        Effect.gen(function* () {
          const rows = yield* sql`
            SELECT * FROM speedtest_results
            ORDER BY timestamp_unix DESC
            LIMIT ${limit}
          `

          return yield* parseRows(rows, SpeedtestResultRecord)
        }).pipe(Effect.mapError(mapSqlError("querySpeedtests"))),

      getLatestSpeedtest: () =>
        Effect.gen(function* () {
          const rows = yield* sql`
            SELECT * FROM speedtest_results ORDER BY timestamp_unix DESC LIMIT 1
          `

          if (rows.length === 0) return null

          return yield* Schema.decodeUnknown(SpeedtestResultRecord)(
            rows[0]
          ).pipe(
            Effect.mapError(
              (e) =>
                new RepositoryError(
                  "getLatestSpeedtest",
                  `Parse failed: ${e.message}`,
                  e
                )
            )
          )
        }).pipe(Effect.mapError(mapSqlError("getLatestSpeedtest"))),

      // ============================================
      // Disruption Operations
      // ============================================

      insertDisruption: (event) =>
        Effect.gen(function* () {
          const rows = yield* sql`
            INSERT INTO disruption_events (
              timestamp, timestamp_unix, event_type, severity, description,
              before_state, after_state, duration_seconds, resolved, resolved_at
            ) VALUES (
              ${event.timestamp}, ${event.timestamp_unix},
              ${event.event_type}, ${event.severity}, ${event.description},
              ${event.before_state ?? null}, ${event.after_state ?? null},
              ${event.duration_seconds ?? null}, ${event.resolved},
              ${event.resolved_at ?? null}
            ) RETURNING id
          `
          return (rows[0] as { id: number }).id
        }).pipe(Effect.mapError(mapSqlError("insertDisruption"))),

      resolveDisruption: (eventId, durationSeconds, resolvedAt, afterState) =>
        Effect.gen(function* () {
          if (afterState !== undefined) {
            yield* sql`
              UPDATE disruption_events
              SET resolved = 1, duration_seconds = ${durationSeconds},
                  resolved_at = ${resolvedAt}, after_state = ${afterState}
              WHERE id = ${eventId}
            `
          } else {
            yield* sql`
              UPDATE disruption_events
              SET resolved = 1, duration_seconds = ${durationSeconds},
                  resolved_at = ${resolvedAt}
              WHERE id = ${eventId}
            `
          }
          return true
        }).pipe(Effect.mapError(mapSqlError("resolveDisruption"))),

      queryDisruptions: (durationHours) =>
        Effect.gen(function* () {
          const cutoff = Date.now() / 1000 - durationHours * 60 * 60

          const rows = yield* sql`
            SELECT * FROM disruption_events
            WHERE timestamp_unix >= ${cutoff}
            ORDER BY timestamp_unix DESC
          `

          return yield* parseRows(rows, DisruptionEventRecord)
        }).pipe(Effect.mapError(mapSqlError("queryDisruptions"))),

      getDisruptionStats: (durationHours) =>
        Effect.gen(function* () {
          const cutoff = Date.now() / 1000 - durationHours * 60 * 60

          // Total count
          const totalRows = yield* sql`
            SELECT COUNT(*)::INTEGER as count FROM disruption_events WHERE timestamp_unix >= ${cutoff}
          `
          const totalRow = totalRows[0] as { count: number }

          // By type
          const byTypeRows = (yield* sql`
            SELECT event_type, COUNT(*)::INTEGER as count
            FROM disruption_events
            WHERE timestamp_unix >= ${cutoff}
            GROUP BY event_type
          `) as Array<{ event_type: string; count: number }>

          // By severity
          const bySeverityRows = (yield* sql`
            SELECT severity, COUNT(*)::INTEGER as count
            FROM disruption_events
            WHERE timestamp_unix >= ${cutoff}
            GROUP BY severity
          `) as Array<{ severity: string; count: number }>

          // Average duration
          const avgRows = yield* sql`
            SELECT AVG(duration_seconds) as avg_duration
            FROM disruption_events
            WHERE timestamp_unix >= ${cutoff} AND duration_seconds IS NOT NULL
          `
          const avgRow = avgRows[0] as { avg_duration: number | null }

          const eventsByType: Record<string, number> = {}
          for (const row of byTypeRows) {
            eventsByType[row.event_type] = row.count
          }

          const eventsBySeverity: Record<string, number> = {}
          for (const row of bySeverityRows) {
            eventsBySeverity[row.severity] = row.count
          }

          return {
            period_hours: durationHours,
            total_events: totalRow?.count ?? 0,
            events_by_type: eventsByType,
            events_by_severity: eventsBySeverity,
            avg_duration_seconds: avgRow?.avg_duration ?? null,
          }
        }).pipe(Effect.mapError(mapSqlError("getDisruptionStats"))),
    }
  })
)
