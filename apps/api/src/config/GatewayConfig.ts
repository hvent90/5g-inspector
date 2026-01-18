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
  pollIntervalMs: 2000,
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
