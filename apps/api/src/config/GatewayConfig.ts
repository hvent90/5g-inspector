/**
 * Gateway configuration using Effect Config
 */
import { Config, Context, Layer } from "effect"

/**
 * Gateway configuration interface
 */
export interface GatewayConfig {
  readonly host: string
  readonly port: number
  readonly pollIntervalMs: number
  readonly timeoutSeconds: number
  readonly failureThreshold: number
  readonly recoveryTimeoutSeconds: number
  readonly sinrDropThresholdDb: number
}

/**
 * GatewayConfig service tag
 */
export class GatewayConfigService extends Context.Tag("GatewayConfigService")<
  GatewayConfigService,
  GatewayConfig
>() {}

/**
 * Default gateway configuration
 */
const defaultConfig: GatewayConfig = {
  host: "192.168.12.1",
  port: 80,
  pollIntervalMs: 200,
  timeoutSeconds: 2.0,
  failureThreshold: 3,
  recoveryTimeoutSeconds: 30,
  sinrDropThresholdDb: 10.0,
}

/**
 * Load gateway config from environment with defaults
 */
export const gatewayConfig = Config.all({
  host: Config.string("GATEWAY_HOST").pipe(
    Config.withDefault(defaultConfig.host)
  ),
  port: Config.number("GATEWAY_PORT").pipe(
    Config.withDefault(defaultConfig.port)
  ),
  pollIntervalMs: Config.number("GATEWAY_POLL_INTERVAL_MS").pipe(
    Config.withDefault(defaultConfig.pollIntervalMs)
  ),
  timeoutSeconds: Config.number("GATEWAY_TIMEOUT_SECONDS").pipe(
    Config.withDefault(defaultConfig.timeoutSeconds)
  ),
  failureThreshold: Config.number("GATEWAY_FAILURE_THRESHOLD").pipe(
    Config.withDefault(defaultConfig.failureThreshold)
  ),
  recoveryTimeoutSeconds: Config.number("GATEWAY_RECOVERY_TIMEOUT_SECONDS").pipe(
    Config.withDefault(defaultConfig.recoveryTimeoutSeconds)
  ),
  sinrDropThresholdDb: Config.number("GATEWAY_SINR_DROP_THRESHOLD_DB").pipe(
    Config.withDefault(defaultConfig.sinrDropThresholdDb)
  ),
})

/**
 * Gateway configuration layer
 */
export const GatewayConfigLive = Layer.effect(
  GatewayConfigService,
  Config.map(gatewayConfig, (config) => config as GatewayConfig)
)

/**
 * Database configuration interface
 */
export interface DbConfig {
  readonly path: string
  readonly batchIntervalSeconds: number
  readonly retentionDays: number
  readonly walMode: boolean
}

/**
 * DbConfig service tag
 */
export class DbConfigService extends Context.Tag("DbConfigService")<
  DbConfigService,
  DbConfig
>() {}

/**
 * Default database configuration
 */
const defaultDbConfig: DbConfig = {
  path: "signal_history.db",
  batchIntervalSeconds: 5,
  retentionDays: 30,
  walMode: false,
}

/**
 * Load database config from environment with defaults
 */
export const dbConfig = Config.all({
  path: Config.string("DB_PATH").pipe(
    Config.withDefault(defaultDbConfig.path)
  ),
  batchIntervalSeconds: Config.number("DB_BATCH_INTERVAL_SECONDS").pipe(
    Config.withDefault(defaultDbConfig.batchIntervalSeconds)
  ),
  retentionDays: Config.number("DB_RETENTION_DAYS").pipe(
    Config.withDefault(defaultDbConfig.retentionDays)
  ),
  walMode: Config.boolean("DB_WAL_MODE").pipe(
    Config.withDefault(defaultDbConfig.walMode)
  ),
})

/**
 * Database configuration layer
 */
export const DbConfigLive = Layer.effect(
  DbConfigService,
  Config.map(dbConfig, (config) => config as DbConfig)
)
