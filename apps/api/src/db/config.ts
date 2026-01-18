/**
 * PostgreSQL configuration for Effect SQL
 */
import { Config, Layer, Redacted } from "effect"
import { PgClient } from "@effect/sql-pg"

/**
 * PostgreSQL client layer - provides SqlClient to all services
 */
export const PgClientLive = PgClient.layerConfig({
  host: Config.string("DB_HOST").pipe(Config.withDefault("localhost")),
  port: Config.number("DB_PORT").pipe(Config.withDefault(5432)),
  database: Config.string("DB_NAME").pipe(Config.withDefault("netpulse")),
  username: Config.string("DB_USER").pipe(Config.withDefault("netpulse")),
  password: Config.redacted("DB_PASSWORD").pipe(
    Config.withDefault(Redacted.make("netpulse_secret"))
  ),
})
