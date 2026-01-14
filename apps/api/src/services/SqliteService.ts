/**
 * SQLite service using Effect for signal data persistence
 * Uses better-sqlite3 for synchronous SQLite operations wrapped in Effect
 */
import { Context, Effect, Layer, Queue, Schedule, Fiber, Ref } from "effect"
import Database from "better-sqlite3"
import type { SignalData, SignalRecord } from "../models/Signal"
import { DbConfigService, type DbConfig } from "../config/GatewayConfig"

// ============================================
// Error Types
// ============================================

export class SqliteError {
  readonly _tag = "SqliteError"
  constructor(
    readonly operation: string,
    readonly cause: unknown
  ) {}
}

// ============================================
// SqliteService Interface
// ============================================

export interface SqliteService {
  /**
   * Insert a signal data record into the database
   */
  readonly insertSignal: (data: SignalData) => Effect.Effect<void, SqliteError>

  /**
   * Queue a signal for batched insertion
   */
  readonly queueSignal: (data: SignalData) => Effect.Effect<void, never>

  /**
   * Get recent signal history
   */
  readonly getHistory: (
    minutes: number,
    resolution?: number
  ) => Effect.Effect<SignalRecord[], SqliteError>

  /**
   * Get database statistics
   */
  readonly getStats: () => Effect.Effect<DbStats, SqliteError>

  /**
   * Start the batch flush background fiber
   */
  readonly startBatchFlush: () => Effect.Effect<void, never>

  /**
   * Stop the batch flush background fiber
   */
  readonly stopBatchFlush: () => Effect.Effect<void, never>

  /**
   * Flush any pending signals immediately
   */
  readonly flush: () => Effect.Effect<void, SqliteError>
}

export interface DbStats {
  readonly totalRecords: number
  readonly oldestRecord: string | null
  readonly newestRecord: string | null
  readonly dbSizeBytes: number
  readonly pendingWrites: number
}

// ============================================
// SqliteService Tag
// ============================================

export class SqliteServiceTag extends Context.Tag("SqliteService")<
  SqliteServiceTag,
  SqliteService
>() {}

// ============================================
// Implementation
// ============================================

const createSignalHistoryTable = `
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
`

const insertSignalSql = `
INSERT INTO signal_history (
  timestamp, timestamp_unix,
  nr_sinr, nr_rsrp, nr_rsrq, nr_rssi, nr_bands, nr_gnb_id, nr_cid,
  lte_sinr, lte_rsrp, lte_rsrq, lte_rssi, lte_bands, lte_enb_id, lte_cid,
  registration_status, device_uptime
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
`

/**
 * Create the SqliteService implementation
 */
const makeSqliteService = (config: DbConfig): Effect.Effect<SqliteService, SqliteError> =>
  Effect.gen(function* () {
    // Initialize database
    const db = yield* Effect.try({
      try: () => {
        const database = new Database(config.path)
        if (config.walMode) {
          database.pragma("journal_mode = WAL")
        }
        database.exec(createSignalHistoryTable)
        return database
      },
      catch: (error) => new SqliteError("initialize", error),
    })

    // Prepare statements
    const insertStmt = db.prepare(insertSignalSql)

    // Batch queue for signal data
    const batchQueue = yield* Queue.unbounded<SignalData>()
    const flushFiberRef = yield* Ref.make<Fiber.Fiber<void, never> | null>(null)

    // Convert SignalData to database row
    const signalToRow = (data: SignalData) => [
      data.timestamp.toISOString(),
      data.timestamp_unix,
      data.nr.sinr,
      data.nr.rsrp,
      data.nr.rsrq,
      data.nr.rssi,
      data.nr.bands ? JSON.stringify(data.nr.bands) : null,
      data.nr.tower_id,
      data.nr.cell_id,
      data.lte.sinr,
      data.lte.rsrp,
      data.lte.rsrq,
      data.lte.rssi,
      data.lte.bands ? JSON.stringify(data.lte.bands) : null,
      data.lte.tower_id,
      data.lte.cell_id,
      data.registration_status,
      data.device_uptime,
    ]

    // Flush batch to database
    const flushBatch = Effect.gen(function* () {
      const signals = yield* Queue.takeAll(batchQueue)
      if (signals.length === 0) return

      yield* Effect.try({
        try: () => {
          const transaction = db.transaction((rows: unknown[][]) => {
            for (const row of rows) {
              insertStmt.run(...row)
            }
          })
          transaction(signals.map(signalToRow).toArray())
        },
        catch: (error) => new SqliteError("batch_insert", error),
      })

      yield* Effect.logDebug(`Flushed ${signals.length} signals to database`)
    })

    // Background flush loop
    const flushLoop = Effect.repeat(
      flushBatch.pipe(Effect.catchAll((e) => Effect.logError(`Batch flush error: ${e.operation}`))),
      Schedule.spaced(config.batchIntervalSeconds * 1000)
    )

    const service: SqliteService = {
      insertSignal: (data) =>
        Effect.try({
          try: () => {
            insertStmt.run(...signalToRow(data))
          },
          catch: (error) => new SqliteError("insert", error),
        }),

      queueSignal: (data) => Queue.offer(batchQueue, data),

      getHistory: (minutes, resolution = 1) =>
        Effect.try({
          try: () => {
            const cutoff = Date.now() / 1000 - minutes * 60
            const sql =
              resolution > 1
                ? `
                SELECT * FROM signal_history
                WHERE timestamp_unix >= ?
                AND rowid % ? = 0
                ORDER BY timestamp_unix DESC
              `
                : `
                SELECT * FROM signal_history
                WHERE timestamp_unix >= ?
                ORDER BY timestamp_unix DESC
              `
            const params = resolution > 1 ? [cutoff, resolution] : [cutoff]
            return db.prepare(sql).all(...params) as SignalRecord[]
          },
          catch: (error) => new SqliteError("get_history", error),
        }),

      getStats: () =>
        Effect.gen(function* () {
          const stats = yield* Effect.try({
            try: () => {
              const countRow = db.prepare("SELECT COUNT(*) as count FROM signal_history").get() as {
                count: number
              }
              const oldestRow = db
                .prepare("SELECT timestamp FROM signal_history ORDER BY timestamp_unix ASC LIMIT 1")
                .get() as { timestamp: string } | undefined
              const newestRow = db
                .prepare("SELECT timestamp FROM signal_history ORDER BY timestamp_unix DESC LIMIT 1")
                .get() as { timestamp: string } | undefined

              // Get file size using Bun.file
              let dbSizeBytes = 0
              try {
                const file = Bun.file(config.path)
                dbSizeBytes = file.size
              } catch {
                // File might not exist yet
              }

              return {
                totalRecords: countRow.count,
                oldestRecord: oldestRow?.timestamp ?? null,
                newestRecord: newestRow?.timestamp ?? null,
                dbSizeBytes,
              }
            },
            catch: (error) => new SqliteError("get_stats", error),
          })

          const pendingWrites = yield* Queue.size(batchQueue)

          return {
            ...stats,
            pendingWrites,
          }
        }),

      startBatchFlush: () =>
        Effect.gen(function* () {
          const currentFiber = yield* Ref.get(flushFiberRef)
          if (currentFiber !== null) return

          const fiber = yield* Effect.fork(flushLoop)
          yield* Ref.set(flushFiberRef, fiber)
          yield* Effect.logInfo("Started batch flush background task")
        }),

      stopBatchFlush: () =>
        Effect.gen(function* () {
          const fiber = yield* Ref.get(flushFiberRef)
          if (fiber === null) return

          yield* Fiber.interrupt(fiber)
          yield* Ref.set(flushFiberRef, null)
          // Final flush
          yield* flushBatch.pipe(Effect.catchAll(() => Effect.void))
          yield* Effect.logInfo("Stopped batch flush background task")
        }),

      flush: () => flushBatch,
    }

    return service
  })

// ============================================
// Layer
// ============================================

export const SqliteServiceLive = Layer.effect(
  SqliteServiceTag,
  Effect.gen(function* () {
    const config = yield* DbConfigService
    return yield* makeSqliteService(config)
  })
)
