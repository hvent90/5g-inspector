/**
 * PostgreSQL configuration for Effect SQL
 */
import { Config, Redacted } from "effect"
import { PgClient } from "@effect/sql-pg"

/**
 * PostgreSQL client layer - provides SqlClient to all services
 */
export const PgClientLive = PgClient.layerConfig({
  host: Config.succeed("localhost"),
  port: Config.succeed(5433),
  database: Config.succeed("netpulse"),
  username: Config.succeed("netpulse"),
  password: Config.succeed(Redacted.make("netpulse_secret")),
})
