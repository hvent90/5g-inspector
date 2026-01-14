/**
 * SignalRepository - Effect-based data access for signal metrics.
 *
 * Uses bun:sqlite for SQLite access and Effect.Service for dependency injection.
 * Provides CRUD operations for: signal_history, speedtest_results, disruption_events
 */

import { Context, Effect, Layer, Schema } from "effect"
import { Database } from "bun:sqlite"
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
// Database Service (dependency)
// ============================================

export class SqliteConnection extends Context.Tag("SqliteConnection")<
  SqliteConnection,
  { readonly db: Database }
>() {}

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
    const { db } = yield* SqliteConnection

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

    return {
      // ============================================
      // Signal History Operations
      // ============================================

      insertSignalHistory: (records) =>
        Effect.try({
          try: () => {
            const stmt = db.prepare(`
              INSERT INTO signal_history (
                timestamp, timestamp_unix,
                nr_sinr, nr_rsrp, nr_rsrq, nr_rssi, nr_bands, nr_gnb_id, nr_cid,
                lte_sinr, lte_rsrp, lte_rsrq, lte_rssi, lte_bands, lte_enb_id, lte_cid,
                registration_status, device_uptime
              ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            `)

            const insertMany = db.transaction(
              (items: ReadonlyArray<SignalHistoryInsert>) => {
                for (const item of items) {
                  stmt.run(
                    item.timestamp,
                    item.timestamp_unix,
                    item.nr_sinr,
                    item.nr_rsrp,
                    item.nr_rsrq,
                    item.nr_rssi,
                    item.nr_bands,
                    item.nr_gnb_id,
                    item.nr_cid,
                    item.lte_sinr,
                    item.lte_rsrp,
                    item.lte_rsrq,
                    item.lte_rssi,
                    item.lte_bands,
                    item.lte_enb_id,
                    item.lte_cid,
                    item.registration_status,
                    item.device_uptime
                  )
                }
                return items.length
              }
            )

            return insertMany(records)
          },
          catch: (e) =>
            new RepositoryError(
              "insertSignalHistory",
              `Failed to insert signal history: ${e}`,
              e
            ),
        }),

      querySignalHistory: (params) =>
        Effect.gen(function* () {
          const durationMinutes = params.duration_minutes ?? 60
          const resolution = params.resolution ?? "auto"
          const limit = params.limit

          const cutoff = Date.now() / 1000 - durationMinutes * 60

          let rows: unknown[]

          if (resolution === "full" || durationMinutes <= 5) {
            // Return all data points
            let query = `
              SELECT * FROM signal_history
              WHERE timestamp_unix >= ?
              ORDER BY timestamp_unix ASC
            `
            const queryParams: (number | undefined)[] = [cutoff]

            if (limit) {
              query += " LIMIT ?"
              queryParams.push(limit)
            }

            rows = yield* Effect.try({
              try: () => db.prepare(query).all(...queryParams),
              catch: (e) =>
                new RepositoryError(
                  "querySignalHistory",
                  `Query failed: ${e}`,
                  e
                ),
            })
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

            let query = `
              SELECT
                MIN(id) as id,
                MIN(timestamp) as timestamp,
                (CAST(timestamp_unix / ? AS INTEGER) * ?) as timestamp_unix,
                AVG(nr_sinr) as nr_sinr,
                AVG(nr_rsrp) as nr_rsrp,
                AVG(nr_rsrq) as nr_rsrq,
                AVG(nr_rssi) as nr_rssi,
                MAX(nr_bands) as nr_bands,
                MAX(nr_gnb_id) as nr_gnb_id,
                MAX(nr_cid) as nr_cid,
                AVG(lte_sinr) as lte_sinr,
                AVG(lte_rsrp) as lte_rsrp,
                AVG(lte_rsrq) as lte_rsrq,
                AVG(lte_rssi) as lte_rssi,
                MAX(lte_bands) as lte_bands,
                MAX(lte_enb_id) as lte_enb_id,
                MAX(lte_cid) as lte_cid,
                MAX(registration_status) as registration_status,
                MAX(device_uptime) as device_uptime
              FROM signal_history
              WHERE timestamp_unix >= ?
              GROUP BY CAST(timestamp_unix / ? AS INTEGER)
              ORDER BY timestamp_unix ASC
            `
            const queryParams: number[] = [
              bucketSeconds,
              bucketSeconds,
              cutoff,
              bucketSeconds,
            ]

            if (limit) {
              query += " LIMIT ?"
              queryParams.push(limit)
            }

            rows = yield* Effect.try({
              try: () => db.prepare(query).all(...queryParams),
              catch: (e) =>
                new RepositoryError(
                  "querySignalHistory",
                  `Query failed: ${e}`,
                  e
                ),
            })
          }

          return yield* parseRows(rows, SignalHistoryRecord)
        }),

      getLatestSignal: () =>
        Effect.gen(function* () {
          const row = yield* Effect.try({
            try: () =>
              db
                .prepare(
                  "SELECT * FROM signal_history ORDER BY timestamp_unix DESC LIMIT 1"
                )
                .get(),
            catch: (e) =>
              new RepositoryError(
                "getLatestSignal",
                `Query failed: ${e}`,
                e
              ),
          })

          if (!row) return null

          return yield* Schema.decodeUnknown(SignalHistoryRecord)(row).pipe(
            Effect.mapError(
              (e) =>
                new RepositoryError(
                  "getLatestSignal",
                  `Parse failed: ${e.message}`,
                  e
                )
            )
          )
        }),

      getTowerHistory: (durationMinutes) =>
        Effect.gen(function* () {
          const cutoff = Date.now() / 1000 - durationMinutes * 60

          const rows = yield* Effect.try({
            try: () =>
              db
                .prepare(
                  `
                SELECT
                  timestamp, timestamp_unix,
                  nr_gnb_id, nr_cid, nr_bands,
                  lte_enb_id, lte_cid, lte_bands
                FROM signal_history
                WHERE timestamp_unix >= ?
                ORDER BY timestamp_unix ASC
              `
                )
                .all(cutoff) as Array<{
                timestamp: string
                timestamp_unix: number
                nr_gnb_id: number | null
                nr_cid: number | null
                lte_enb_id: number | null
                lte_cid: number | null
              }>,
            catch: (e) =>
              new RepositoryError(
                "getTowerHistory",
                `Query failed: ${e}`,
                e
              ),
          })

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
        }),

      // ============================================
      // Speedtest Operations
      // ============================================

      insertSpeedtest: (result) =>
        Effect.try({
          try: () => {
            const stmt = db.prepare(`
              INSERT INTO speedtest_results (
                timestamp, timestamp_unix, download_mbps, upload_mbps, ping_ms,
                jitter_ms, packet_loss_percent, server_name, server_location,
                server_host, server_id, client_ip, isp, tool, result_url,
                signal_snapshot, status, error_message, triggered_by,
                network_context, pre_test_latency_ms
              ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            `)

            const info = stmt.run(
              result.timestamp,
              result.timestamp_unix,
              result.download_mbps,
              result.upload_mbps,
              result.ping_ms,
              result.jitter_ms ?? null,
              result.packet_loss_percent ?? null,
              result.server_name ?? null,
              result.server_location ?? null,
              result.server_host ?? null,
              result.server_id ?? null,
              result.client_ip ?? null,
              result.isp ?? null,
              result.tool,
              result.result_url ?? null,
              result.signal_snapshot ?? null,
              result.status,
              result.error_message ?? null,
              result.triggered_by,
              result.network_context,
              result.pre_test_latency_ms ?? null
            )
            return info.lastInsertRowid as number
          },
          catch: (e) =>
            new RepositoryError(
              "insertSpeedtest",
              `Failed to insert speedtest: ${e}`,
              e
            ),
        }),

      querySpeedtests: (limit) =>
        Effect.gen(function* () {
          const rows = yield* Effect.try({
            try: () =>
              db
                .prepare(
                  `
                SELECT * FROM speedtest_results
                ORDER BY timestamp_unix DESC
                LIMIT ?
              `
                )
                .all(limit),
            catch: (e) =>
              new RepositoryError(
                "querySpeedtests",
                `Query failed: ${e}`,
                e
              ),
          })

          return yield* parseRows(rows, SpeedtestResultRecord)
        }),

      getLatestSpeedtest: () =>
        Effect.gen(function* () {
          const row = yield* Effect.try({
            try: () =>
              db
                .prepare(
                  "SELECT * FROM speedtest_results ORDER BY timestamp_unix DESC LIMIT 1"
                )
                .get(),
            catch: (e) =>
              new RepositoryError(
                "getLatestSpeedtest",
                `Query failed: ${e}`,
                e
              ),
          })

          if (!row) return null

          return yield* Schema.decodeUnknown(SpeedtestResultRecord)(row).pipe(
            Effect.mapError(
              (e) =>
                new RepositoryError(
                  "getLatestSpeedtest",
                  `Parse failed: ${e.message}`,
                  e
                )
            )
          )
        }),

      // ============================================
      // Disruption Operations
      // ============================================

      insertDisruption: (event) =>
        Effect.try({
          try: () => {
            const stmt = db.prepare(`
              INSERT INTO disruption_events (
                timestamp, timestamp_unix, event_type, severity, description,
                before_state, after_state, duration_seconds, resolved, resolved_at
              ) VALUES (
                @timestamp, @timestamp_unix, @event_type, @severity, @description,
                @before_state, @after_state, @duration_seconds, @resolved, @resolved_at
              )
            `)

            const info = stmt.run(event)
            return info.lastInsertRowid as number
          },
          catch: (e) =>
            new RepositoryError(
              "insertDisruption",
              `Failed to insert disruption: ${e}`,
              e
            ),
        }),

      resolveDisruption: (eventId, durationSeconds, resolvedAt, afterState) =>
        Effect.try({
          try: () => {
            let stmt: ReturnType<typeof db.prepare>
            let info: ReturnType<ReturnType<typeof db.prepare>["run"]>

            if (afterState !== undefined) {
              stmt = db.prepare(`
                UPDATE disruption_events
                SET resolved = 1, duration_seconds = ?, resolved_at = ?, after_state = ?
                WHERE id = ?
              `)
              info = stmt.run(durationSeconds, resolvedAt, afterState, eventId)
            } else {
              stmt = db.prepare(`
                UPDATE disruption_events
                SET resolved = 1, duration_seconds = ?, resolved_at = ?
                WHERE id = ?
              `)
              info = stmt.run(durationSeconds, resolvedAt, eventId)
            }

            return info.changes > 0
          },
          catch: (e) =>
            new RepositoryError(
              "resolveDisruption",
              `Failed to resolve disruption: ${e}`,
              e
            ),
        }),

      queryDisruptions: (durationHours) =>
        Effect.gen(function* () {
          const cutoff = Date.now() / 1000 - durationHours * 60 * 60

          const rows = yield* Effect.try({
            try: () =>
              db
                .prepare(
                  `
                SELECT * FROM disruption_events
                WHERE timestamp_unix >= ?
                ORDER BY timestamp_unix DESC
              `
                )
                .all(cutoff),
            catch: (e) =>
              new RepositoryError(
                "queryDisruptions",
                `Query failed: ${e}`,
                e
              ),
          })

          return yield* parseRows(rows, DisruptionEventRecord)
        }),

      getDisruptionStats: (durationHours) =>
        Effect.gen(function* () {
          const cutoff = Date.now() / 1000 - durationHours * 60 * 60

          // Total count
          const totalRow = yield* Effect.try({
            try: () =>
              db
                .prepare(
                  "SELECT COUNT(*) as count FROM disruption_events WHERE timestamp_unix >= ?"
                )
                .get(cutoff) as { count: number },
            catch: (e) =>
              new RepositoryError(
                "getDisruptionStats",
                `Query failed: ${e}`,
                e
              ),
          })

          // By type
          const byTypeRows = yield* Effect.try({
            try: () =>
              db
                .prepare(
                  `
                SELECT event_type, COUNT(*) as count
                FROM disruption_events
                WHERE timestamp_unix >= ?
                GROUP BY event_type
              `
                )
                .all(cutoff) as Array<{ event_type: string; count: number }>,
            catch: (e) =>
              new RepositoryError(
                "getDisruptionStats",
                `Query failed: ${e}`,
                e
              ),
          })

          // By severity
          const bySeverityRows = yield* Effect.try({
            try: () =>
              db
                .prepare(
                  `
                SELECT severity, COUNT(*) as count
                FROM disruption_events
                WHERE timestamp_unix >= ?
                GROUP BY severity
              `
                )
                .all(cutoff) as Array<{ severity: string; count: number }>,
            catch: (e) =>
              new RepositoryError(
                "getDisruptionStats",
                `Query failed: ${e}`,
                e
              ),
          })

          // Average duration
          const avgRow = yield* Effect.try({
            try: () =>
              db
                .prepare(
                  `
                SELECT AVG(duration_seconds) as avg_duration
                FROM disruption_events
                WHERE timestamp_unix >= ? AND duration_seconds IS NOT NULL
              `
                )
                .get(cutoff) as { avg_duration: number | null },
            catch: (e) =>
              new RepositoryError(
                "getDisruptionStats",
                `Query failed: ${e}`,
                e
              ),
          })

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
        }),
    }
  })
)

// ============================================
// Database Schema Initialization
// ============================================

const initializeSchema = `
-- Signal history table
CREATE TABLE IF NOT EXISTS signal_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL,
  timestamp_unix REAL NOT NULL,
  nr_sinr REAL,
  nr_rsrp REAL,
  nr_rsrq REAL,
  nr_rssi REAL,
  nr_bands TEXT,
  nr_gnb_id INTEGER,
  nr_cid INTEGER,
  lte_sinr REAL,
  lte_rsrp REAL,
  lte_rsrq REAL,
  lte_rssi REAL,
  lte_bands TEXT,
  lte_enb_id INTEGER,
  lte_cid INTEGER,
  registration_status TEXT,
  device_uptime INTEGER
);
CREATE INDEX IF NOT EXISTS idx_signal_timestamp ON signal_history(timestamp_unix);

-- Speedtest results table
CREATE TABLE IF NOT EXISTS speedtest_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL,
  timestamp_unix REAL NOT NULL,
  download_mbps REAL NOT NULL,
  upload_mbps REAL NOT NULL,
  ping_ms REAL NOT NULL,
  jitter_ms REAL,
  packet_loss_percent REAL,
  server_name TEXT,
  server_location TEXT,
  server_host TEXT,
  server_id INTEGER,
  client_ip TEXT,
  isp TEXT,
  tool TEXT NOT NULL,
  result_url TEXT,
  signal_snapshot TEXT,
  status TEXT NOT NULL,
  error_message TEXT,
  triggered_by TEXT NOT NULL,
  network_context TEXT NOT NULL DEFAULT 'unknown',
  pre_test_latency_ms REAL
);
CREATE INDEX IF NOT EXISTS idx_speedtest_timestamp ON speedtest_results(timestamp_unix);

-- Disruption events table
CREATE TABLE IF NOT EXISTS disruption_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL,
  timestamp_unix REAL NOT NULL,
  event_type TEXT NOT NULL,
  severity TEXT NOT NULL,
  description TEXT NOT NULL,
  before_state TEXT,
  after_state TEXT,
  duration_seconds REAL,
  resolved INTEGER NOT NULL DEFAULT 0,
  resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_disruption_timestamp ON disruption_events(timestamp_unix);
`

// ============================================
// Helper to create SqliteConnection Layer
// ============================================

export const makeSqliteConnectionLayer = (
  dbPath: string
): Layer.Layer<SqliteConnection> =>
  Layer.sync(SqliteConnection, () => {
    const db = new Database(dbPath)

    // Use DELETE journal mode for simpler container mounting (no WAL/SHM files)
    db.exec("PRAGMA journal_mode = DELETE")

    // Initialize schema (creates tables if they don't exist)
    db.exec(initializeSchema)

    return { db }
  })

// ============================================
// Convenience function for running repository operations
// ============================================

export const runWithRepository = <A, E>(
  effect: Effect.Effect<A, E, SignalRepository>,
  dbPath: string
): Effect.Effect<A, E | RepositoryError> => {
  const SqliteLayer = makeSqliteConnectionLayer(dbPath)
  const RepoLayer = Layer.provide(SignalRepositoryLive, SqliteLayer)

  return Effect.provide(effect, RepoLayer)
}
