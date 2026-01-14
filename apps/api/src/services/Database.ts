/**
 * Database service using Effect and better-sqlite3.
 *
 * Provides a tagged service for SQLite database access that can be
 * injected into other services and route handlers.
 */

import Database from "better-sqlite3"
import { Context, Effect, Layer } from "effect"

// Database configuration
export interface DatabaseConfig {
  readonly path: string
  readonly walMode: boolean
}

// Default configuration
const defaultConfig: DatabaseConfig = {
  path: process.env.DB_PATH ?? "signal_history.db",
  walMode: false,
}

/**
 * Database service interface
 */
export interface DatabaseService {
  readonly db: Database.Database
  readonly query: <T>(sql: string, params?: unknown[]) => Effect.Effect<T[]>
  readonly run: (
    sql: string,
    params?: unknown[]
  ) => Effect.Effect<Database.RunResult>
  readonly close: () => Effect.Effect<void>
}

/**
 * Database service tag for Effect context
 */
export class DatabaseServiceTag extends Context.Tag("DatabaseService")<
  DatabaseServiceTag,
  DatabaseService
>() {}

/**
 * Create a live database service layer
 */
export const DatabaseServiceLive = (
  config: DatabaseConfig = defaultConfig
): Layer.Layer<DatabaseServiceTag> =>
  Layer.scoped(
    DatabaseServiceTag,
    Effect.acquireRelease(
      Effect.sync(() => {
        const db = new Database(config.path)

        // Enable WAL mode for better concurrent access
        if (config.walMode) {
          db.pragma("journal_mode = WAL")
        }

        const service: DatabaseService = {
          db,

          query: <T>(sql: string, params: unknown[] = []) =>
            Effect.try({
              try: () => db.prepare(sql).all(...params) as T[],
              catch: (error) =>
                new Error(`Database query failed: ${String(error)}`),
            }),

          run: (sql: string, params: unknown[] = []) =>
            Effect.try({
              try: () => db.prepare(sql).run(...params),
              catch: (error) =>
                new Error(`Database run failed: ${String(error)}`),
            }),

          close: () =>
            Effect.sync(() => {
              db.close()
            }),
        }

        return service
      }),
      (service) => service.close()
    )
  )
