/**
 * SpeedtestService - Effect-based speedtest execution and history management.
 *
 * Migrated from Python: backend/src/netpulse/services/speedtest.py
 *
 * Provides:
 * - Multi-tool speedtest execution (Ookla CLI, speedtest-cli)
 * - Network context detection (baseline, idle, light, busy)
 * - Pre-test latency probing
 * - History persistence via SignalRepository
 * - Tool availability detection
 */

import { Context, Effect, Layer, Ref, Schema, Duration } from "effect"
import { SignalRepository, type RepositoryError } from "./SignalRepository"
import {
  type SpeedtestResultRecord,
  type SpeedtestResultInsert,
  type NetworkContext,
} from "../schema/Signal"
import type { SignalData } from "./GatewayService"
import * as ChildProcess from "node:child_process"
import * as os from "node:os"

// ============================================
// Types
// ============================================

export interface SpeedtestResult {
  readonly timestamp: Date
  readonly timestamp_unix: number
  readonly download_mbps: number
  readonly upload_mbps: number
  readonly ping_ms: number
  readonly jitter_ms: number | null
  readonly server_name: string | null
  readonly server_location: string | null
  readonly server_host: string | null
  readonly server_id: number | null
  readonly client_ip: string | null
  readonly isp: string | null
  readonly tool: string
  readonly result_url: string | null
  readonly status: "success" | "error" | "timeout" | "busy"
  readonly error_message: string | null
  readonly triggered_by: string
  readonly network_context: NetworkContext
  readonly pre_test_latency_ms: number | null
}

export interface SpeedtestToolInfo {
  readonly available: readonly string[]
  readonly all_known: readonly string[]
  readonly preferred_order: readonly string[]
  readonly configured_server_id: number | null
  readonly timeout_seconds: number
}

export interface SpeedtestConfig {
  readonly preferred_tools: readonly string[]
  readonly ookla_server_id: number | null
  readonly timeout_seconds: number
  readonly idle_hours: readonly number[]
  readonly baseline_latency_ms: number
  readonly light_latency_multiplier: number
  readonly busy_latency_multiplier: number
}

// ============================================
// Error Types
// ============================================

export class SpeedtestError {
  readonly _tag = "SpeedtestError"
  constructor(
    readonly type: "no_tool" | "execution" | "parse" | "timeout" | "busy",
    readonly message: string,
    readonly cause?: unknown
  ) {}
}

// ============================================
// Tool Result (internal)
// ============================================

interface ToolResult {
  status: "success" | "error" | "timeout"
  download_mbps: number
  upload_mbps: number
  ping_ms: number
  jitter_ms: number | null
  server_name: string | null
  server_location: string | null
  server_host: string | null
  server_id: number | null
  client_ip: string | null
  isp: string | null
  tool: string
  result_url: string | null
  error_message: string | null
}

// ============================================
// Constants
// ============================================

const ALL_KNOWN_TOOLS = [
  "fast-cli", "ookla-speedtest", "speedtest-cli",
  "cdn-cloudflare", "cdn-aws", "cdn-google"
] as const

/**
 * CDN test URLs for realistic throughput measurement.
 * These bypass ISP speed test prioritization.
 */
const CDN_TEST_URLS = {
  cloudflare: "https://speed.cloudflare.com/__down?bytes=25000000",
  aws: "https://awscli.amazonaws.com/AWSCLIV2.pkg",
  google: "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-darwin-arm.tar.gz"
} as const

const DEFAULT_CONFIG: SpeedtestConfig = {
  // fast-cli (Netflix) is preferred as ISPs can't easily game it
  preferred_tools: ["fast-cli", "ookla-speedtest", "speedtest-cli"],
  ookla_server_id: null,
  timeout_seconds: 120,
  idle_hours: [2, 3, 4, 5],
  baseline_latency_ms: 20.0,
  light_latency_multiplier: 1.5,
  busy_latency_multiplier: 2.5,
}

// ============================================
// Helper Functions
// ============================================

/**
 * Run a command and return stdout/stderr
 */
const runCommand = (
  cmd: string[],
  timeout: number
): Effect.Effect<{ stdout: string; stderr: string; code: number }, SpeedtestError> =>
  Effect.async<{ stdout: string; stderr: string; code: number }, SpeedtestError>(
    (resume) => {
      const [executable, ...args] = cmd
      const isWindows = os.platform() === "win32"

      const proc = ChildProcess.spawn(executable, args, {
        shell: isWindows,
        windowsHide: true,
      })

      let stdout = ""
      let stderr = ""
      let resolved = false

      // Manual timeout handling (spawn doesn't support timeout option)
      const timeoutId = setTimeout(() => {
        if (!resolved) {
          resolved = true
          proc.kill("SIGTERM")
          resume(Effect.fail(new SpeedtestError("timeout", `Command timed out after ${timeout}s`)))
        }
      }, timeout * 1000)

      proc.stdout?.on("data", (data: Buffer) => {
        stdout += data.toString()
      })

      proc.stderr?.on("data", (data: Buffer) => {
        stderr += data.toString()
      })

      proc.on("close", (code) => {
        if (!resolved) {
          resolved = true
          clearTimeout(timeoutId)
          resume(Effect.succeed({ stdout, stderr, code: code ?? 1 }))
        }
      })

      proc.on("error", (err: Error & { code?: string }) => {
        if (!resolved) {
          resolved = true
          clearTimeout(timeoutId)
          resume(Effect.fail(new SpeedtestError("execution", err.message, err)))
        }
      })
    }
  )

/**
 * Check if Ookla speedtest CLI is available
 */
const isOoklaAvailable = (): Effect.Effect<boolean> =>
  runCommand(["speedtest", "--version"], 5).pipe(
    Effect.map(
      ({ stdout, code }) => code === 0 && stdout.toLowerCase().includes("ookla")
    ),
    Effect.catchAll(() => Effect.succeed(false))
  )

/**
 * Check if speedtest-cli (Python) is available
 */
const isSpeedtestCliAvailable = (): Effect.Effect<boolean> =>
  runCommand(["speedtest-cli", "--version"], 5).pipe(
    Effect.map(({ code }) => code === 0),
    Effect.catchAll(() => Effect.succeed(false))
  )

/**
 * Check if fast-cli (Netflix) is available via bunx
 */
const isFastCliAvailable = (): Effect.Effect<boolean> =>
  runCommand(["bunx", "fast-cli", "--help"], 10).pipe(
    Effect.map(({ code, stdout }) => code === 0 && stdout.includes("fast.com")),
    Effect.catchAll(() => Effect.succeed(false))
  )

/**
 * Check if curl is available (needed for CDN tests)
 */
const isCurlAvailable = (): Effect.Effect<boolean> =>
  runCommand(["curl", "--version"], 5).pipe(
    Effect.map(({ code }) => code === 0),
    Effect.catchAll(() => Effect.succeed(false))
  )

/**
 * Run CDN-based speed test using curl.
 * Downloads a file from a CDN and measures throughput.
 * Provides realistic speed measurements that ISPs can't easily prioritize.
 */
const runCdnTest = (
  cdnName: string,
  url: string,
  timeout: number
): Effect.Effect<ToolResult, SpeedtestError> =>
  Effect.gen(function* () {
    const toolName = `cdn-${cdnName}`

    // First, measure ping via HEAD request
    const pingResult = yield* runCommand(
      ["curl", "-s", "-o", "/dev/null", "-w", "%{time_connect}", "-I", url],
      10
    ).pipe(Effect.catchAll(() => Effect.succeed({ stdout: "", stderr: "", code: 1 })))

    const pingMs = pingResult.code === 0 && pingResult.stdout
      ? Math.round(parseFloat(pingResult.stdout) * 1000 * 10) / 10
      : 0

    // Download with timing stats
    // -w format: speed_download (bytes/sec), time_total (seconds)
    const { stdout, stderr, code } = yield* runCommand(
      [
        "curl", "-s", "-o", "/dev/null",
        "-w", "%{speed_download}|%{time_total}|%{http_code}",
        "--max-time", String(timeout),
        url
      ],
      timeout + 5
    )

    if (code !== 0) {
      return {
        status: "error" as const,
        download_mbps: 0,
        upload_mbps: 0,
        ping_ms: 0,
        jitter_ms: null,
        server_name: null,
        server_location: null,
        server_host: null,
        server_id: null,
        client_ip: null,
        isp: null,
        tool: toolName,
        result_url: null,
        error_message: stderr || `curl exit code ${code}`,
      }
    }

    // Parse curl output: speed_download|time_total|http_code
    const parts = stdout.trim().split("|")
    const speedBytesPerSec = parseFloat(parts[0] ?? "0")
    const httpCode = parseInt(parts[2] ?? "0", 10)

    if (httpCode < 200 || httpCode >= 400) {
      return {
        status: "error" as const,
        download_mbps: 0,
        upload_mbps: 0,
        ping_ms: 0,
        jitter_ms: null,
        server_name: null,
        server_location: null,
        server_host: null,
        server_id: null,
        client_ip: null,
        isp: null,
        tool: toolName,
        result_url: null,
        error_message: `HTTP ${httpCode}`,
      }
    }

    // Convert bytes/sec to Mbps (bits = bytes * 8, Mbps = bits / 1_000_000)
    const downloadMbps = Math.round((speedBytesPerSec * 8) / 1_000_000 * 100) / 100

    // Determine server name based on CDN
    const serverNames: Record<string, string> = {
      cloudflare: "Cloudflare CDN",
      aws: "AWS CloudFront",
      google: "Google CDN"
    }

    return {
      status: "success" as const,
      download_mbps: downloadMbps,
      upload_mbps: 0, // CDN tests are download-only
      ping_ms: pingMs,
      jitter_ms: null,
      server_name: serverNames[cdnName] ?? `${cdnName} CDN`,
      server_location: null,
      server_host: new URL(url).hostname,
      server_id: null,
      client_ip: null,
      isp: null,
      tool: toolName,
      result_url: url,
      error_message: null,
    }
  })

/**
 * Detect available speedtest tools
 */
const detectAvailableTools = (): Effect.Effect<string[]> =>
  Effect.gen(function* () {
    const available: string[] = []

    // Check fast-cli first (preferred)
    const fastOk = yield* isFastCliAvailable()
    if (fastOk) available.push("fast-cli")

    const ooklaOk = yield* isOoklaAvailable()
    if (ooklaOk) available.push("ookla-speedtest")

    const cliOk = yield* isSpeedtestCliAvailable()
    if (cliOk) available.push("speedtest-cli")

    // CDN tools require curl
    const curlOk = yield* isCurlAvailable()
    if (curlOk) {
      available.push("cdn-cloudflare")
      available.push("cdn-aws")
      available.push("cdn-google")
    }

    yield* Effect.logDebug(`Speedtest tools detected: ${available.join(", ") || "none"}`)
    return available
  })

/**
 * Run Ookla speedtest CLI
 */
const runOoklaSpeedtest = (
  timeout: number,
  serverId: number | null
): Effect.Effect<ToolResult, SpeedtestError> =>
  Effect.gen(function* () {
    const cmd = ["speedtest", "--format=json", "--accept-license", "--accept-gdpr"]
    if (serverId) {
      cmd.push("--server-id", String(serverId))
    }

    const { stdout, stderr, code } = yield* runCommand(cmd, timeout)

    if (code !== 0) {
      return {
        status: "error" as const,
        download_mbps: 0,
        upload_mbps: 0,
        ping_ms: 0,
        jitter_ms: null,
        server_name: null,
        server_location: null,
        server_host: null,
        server_id: null,
        client_ip: null,
        isp: null,
        tool: "ookla-speedtest",
        result_url: null,
        error_message: stderr || `Exit code ${code}`,
      }
    }

    const data = yield* Effect.try({
      try: () => JSON.parse(stdout) as Record<string, unknown>,
      catch: (e) => new SpeedtestError("parse", `JSON parse error: ${e}`),
    })

    const download = data.download as Record<string, number> | undefined
    const upload = data.upload as Record<string, number> | undefined
    const ping = data.ping as Record<string, number> | undefined
    const server = data.server as Record<string, unknown> | undefined
    const iface = data.interface as Record<string, string> | undefined
    const result = data.result as Record<string, string> | undefined

    return {
      status: "success" as const,
      download_mbps: Math.round(((download?.bandwidth ?? 0) * 8) / 1_000_000 * 100) / 100,
      upload_mbps: Math.round(((upload?.bandwidth ?? 0) * 8) / 1_000_000 * 100) / 100,
      ping_ms: Math.round((ping?.latency ?? 0) * 10) / 10,
      jitter_ms: ping?.jitter ? Math.round(ping.jitter * 10) / 10 : null,
      server_name: (server?.name as string) ?? null,
      server_location: (server?.location as string) ?? null,
      server_host: (server?.host as string) ?? null,
      server_id: (server?.id as number) ?? null,
      client_ip: iface?.externalIp ?? null,
      isp: (data.isp as string) ?? null,
      tool: "ookla-speedtest",
      result_url: result?.url ?? null,
      error_message: null,
    }
  })

/**
 * Run speedtest-cli (Python)
 */
const runSpeedtestCli = (timeout: number): Effect.Effect<ToolResult, SpeedtestError> =>
  Effect.gen(function* () {
    const { stdout, stderr, code } = yield* runCommand(
      ["speedtest-cli", "--json"],
      timeout
    )

    if (code !== 0) {
      return {
        status: "error" as const,
        download_mbps: 0,
        upload_mbps: 0,
        ping_ms: 0,
        jitter_ms: null,
        server_name: null,
        server_location: null,
        server_host: null,
        server_id: null,
        client_ip: null,
        isp: null,
        tool: "speedtest-cli",
        result_url: null,
        error_message: stderr || `Exit code ${code}`,
      }
    }

    const data = yield* Effect.try({
      try: () => JSON.parse(stdout) as Record<string, unknown>,
      catch: (e) => new SpeedtestError("parse", `JSON parse error: ${e}`),
    })

    const server = data.server as Record<string, unknown> | undefined
    const client = data.client as Record<string, string> | undefined

    return {
      status: "success" as const,
      download_mbps: Math.round(((data.download as number) ?? 0) / 1_000_000 * 100) / 100,
      upload_mbps: Math.round(((data.upload as number) ?? 0) / 1_000_000 * 100) / 100,
      ping_ms: Math.round(((data.ping as number) ?? 0) * 10) / 10,
      jitter_ms: null, // speedtest-cli doesn't provide jitter
      server_name: (server?.name as string) ?? null,
      server_location: server
        ? `${server.name as string}, ${server.country as string}`
        : null,
      server_host: (server?.host as string) ?? null,
      server_id: (server?.id as number) ?? null,
      client_ip: client?.ip ?? null,
      isp: client?.isp ?? null,
      tool: "speedtest-cli",
      result_url: null,
      error_message: null,
    }
  })

/**
 * Run fast-cli (Netflix) via bunx
 * Uses Netflix CDN - harder for ISPs to prioritize/game
 */
const runFastCli = (timeout: number): Effect.Effect<ToolResult, SpeedtestError> =>
  Effect.gen(function* () {
    // Run with --json and --upload flags for full data
    const { stdout, stderr, code } = yield* runCommand(
      ["bunx", "fast-cli", "--json", "--upload"],
      timeout
    )

    if (code !== 0) {
      return {
        status: "error" as const,
        download_mbps: 0,
        upload_mbps: 0,
        ping_ms: 0,
        jitter_ms: null,
        server_name: null,
        server_location: null,
        server_host: null,
        server_id: null,
        client_ip: null,
        isp: null,
        tool: "fast-cli",
        result_url: null,
        error_message: stderr || `Exit code ${code}`,
      }
    }

    const data = yield* Effect.try({
      try: () => JSON.parse(stdout) as Record<string, unknown>,
      catch: (e) => new SpeedtestError("parse", `JSON parse error: ${e}`),
    })

    // fast-cli JSON format:
    // { downloadSpeed, uploadSpeed, latency, bufferBloat, userLocation, serverLocations, userIp }
    const serverLocations = data.serverLocations as string[] | undefined

    return {
      status: "success" as const,
      download_mbps: Math.round(((data.downloadSpeed as number) ?? 0) * 100) / 100,
      upload_mbps: Math.round(((data.uploadSpeed as number) ?? 0) * 100) / 100,
      ping_ms: Math.round(((data.latency as number) ?? 0) * 10) / 10,
      jitter_ms: data.bufferBloat ? Math.round((data.bufferBloat as number) * 10) / 10 : null, // buffer bloat as proxy for jitter
      server_name: "Netflix CDN",
      server_location: serverLocations?.[0] ?? (data.userLocation as string) ?? null,
      server_host: null,
      server_id: null,
      client_ip: (data.userIp as string) ?? null,
      isp: null,
      tool: "fast-cli",
      result_url: "https://fast.com",
      error_message: null,
    }
  })

/**
 * Measure ping latency to a host
 */
const measurePingLatency = (
  host: string = "8.8.8.8",
  count: number = 3,
  timeout: number = 5
): Effect.Effect<number | null> =>
  Effect.gen(function* () {
    const isWindows = os.platform() === "win32"
    const cmd = isWindows
      ? ["ping", "-n", String(count), "-w", String(timeout * 1000), host]
      : ["ping", "-c", String(count), "-W", String(timeout), host]

    const result = yield* runCommand(cmd, timeout + 5).pipe(
      Effect.catchAll(() => Effect.succeed({ stdout: "", stderr: "", code: 1 }))
    )

    if (result.code !== 0) return null

    const output = result.stdout.toLowerCase()

    if (isWindows) {
      // Windows: "Average = 15ms"
      const lines = output.split("\n")
      for (const line of lines) {
        if (line.includes("average")) {
          const parts = line.split("=")
          if (parts.length >= 2) {
            const avgPart = parts[parts.length - 1].trim().replace("ms", "").trim()
            const parsed = parseFloat(avgPart)
            if (!isNaN(parsed)) return parsed
          }
        }
      }
    } else {
      // Unix: "rtt min/avg/max/mdev = 10.123/15.456/20.789/3.456 ms"
      const lines = output.split("\n")
      for (const line of lines) {
        if (line.includes("avg") || line.includes("rtt")) {
          if (line.includes("/")) {
            const parts = line.split("=")
            if (parts.length >= 2) {
              const stats = parts[parts.length - 1].trim().split("/")
              if (stats.length >= 2) {
                const parsed = parseFloat(stats[1])
                if (!isNaN(parsed)) return parsed
              }
            }
          }
        }
      }
    }

    return null
  })

/**
 * Infer network context from time and latency measurements
 */
const inferNetworkContext = (
  currentHour: number,
  preTestLatencyMs: number | null,
  config: SpeedtestConfig
): NetworkContext => {
  // Time-based: tests during configured idle hours are always baseline
  if (config.idle_hours.includes(currentHour)) {
    return "baseline"
  }

  // If no latency probe, we can't infer from latency
  if (preTestLatencyMs === null) {
    return "unknown"
  }

  // Latency-based inference
  const latencyRatio = preTestLatencyMs / config.baseline_latency_ms

  if (latencyRatio < config.light_latency_multiplier) {
    return "idle"
  } else if (latencyRatio < config.busy_latency_multiplier) {
    return "light"
  } else {
    return "busy"
  }
}

// ============================================
// Service Interface
// ============================================

export interface SpeedtestServiceShape {
  /**
   * Run a speedtest and store results
   */
  readonly runSpeedtest: (options?: {
    tool?: string
    serverId?: number
    triggeredBy?: string
    signalSnapshot?: SignalData
    contextOverride?: NetworkContext
    enableLatencyProbe?: boolean
  }) => Effect.Effect<SpeedtestResult, SpeedtestError | RepositoryError>

  /**
   * Get speedtest history
   */
  readonly getHistory: (
    limit?: number
  ) => Effect.Effect<readonly SpeedtestResultRecord[], RepositoryError>

  /**
   * Get list of available speedtest tools
   */
  readonly getAvailableTools: () => Effect.Effect<readonly string[]>

  /**
   * Get tool info including configuration
   */
  readonly getToolInfo: () => Effect.Effect<SpeedtestToolInfo>

  /**
   * Check if a speedtest is currently running
   */
  readonly isRunning: () => Effect.Effect<boolean>

  /**
   * Get the last speedtest result
   */
  readonly getLastResult: () => Effect.Effect<SpeedtestResult | null>
}

// ============================================
// Service Tag
// ============================================

export class SpeedtestService extends Context.Tag("SpeedtestService")<
  SpeedtestService,
  SpeedtestServiceShape
>() {}

// ============================================
// Live Implementation
// ============================================

export const SpeedtestServiceLive = Layer.effect(
  SpeedtestService,
  Effect.gen(function* () {
    const signalRepo = yield* SignalRepository

    // State
    const runningRef = yield* Ref.make(false)
    const lastResultRef = yield* Ref.make<SpeedtestResult | null>(null)
    const availableToolsRef = yield* Ref.make<readonly string[]>([])
    const configRef = yield* Ref.make<SpeedtestConfig>(DEFAULT_CONFIG)

    // Detect tools on startup
    const detectedTools = yield* detectAvailableTools()
    yield* Ref.set(availableToolsRef, detectedTools)
    yield* Effect.logInfo(`Speedtest tools available: ${detectedTools.join(", ") || "none"}`)

    /**
     * Select best available tool based on preference
     */
    const selectTool = (
      preferred: string | undefined,
      available: readonly string[],
      config: SpeedtestConfig
    ): string | null => {
      // Use explicit preference if provided and available
      if (preferred && available.includes(preferred)) {
        return preferred
      }

      // Use configured preference order
      for (const toolName of config.preferred_tools) {
        if (available.includes(toolName)) {
          return toolName
        }
      }

      // Fallback to first available
      return available[0] ?? null
    }

    /**
     * Run the selected tool
     */
    const runTool = (
      tool: string,
      timeout: number,
      serverId: number | null
    ): Effect.Effect<ToolResult, SpeedtestError> => {
      switch (tool) {
        case "fast-cli":
          return runFastCli(timeout)
        case "ookla-speedtest":
          return runOoklaSpeedtest(timeout, serverId)
        case "speedtest-cli":
          return runSpeedtestCli(timeout)
        case "cdn-cloudflare":
          return runCdnTest("cloudflare", CDN_TEST_URLS.cloudflare, timeout)
        case "cdn-aws":
          return runCdnTest("aws", CDN_TEST_URLS.aws, timeout)
        case "cdn-google":
          return runCdnTest("google", CDN_TEST_URLS.google, timeout)
        default:
          return Effect.fail(new SpeedtestError("no_tool", `Unknown tool: ${tool}`))
      }
    }

    const impl: SpeedtestServiceShape = {
      runSpeedtest: (options = {}) =>
        Effect.gen(function* () {
          // Check if already running
          const isRunning = yield* Ref.get(runningRef)
          if (isRunning) {
            const now = new Date()
            return {
              timestamp: now,
              timestamp_unix: now.getTime() / 1000,
              download_mbps: 0,
              upload_mbps: 0,
              ping_ms: 0,
              jitter_ms: null,
              server_name: null,
              server_location: null,
              server_host: null,
              server_id: null,
              client_ip: null,
              isp: null,
              tool: "unknown",
              result_url: null,
              status: "busy" as const,
              error_message: "Speed test already running",
              triggered_by: options.triggeredBy ?? "manual",
              network_context: "unknown" as const,
              pre_test_latency_ms: null,
            } satisfies SpeedtestResult
          }

          yield* Ref.set(runningRef, true)

          const result = yield* Effect.gen(function* () {
            const config = yield* Ref.get(configRef)
            const available = yield* Ref.get(availableToolsRef)
            const currentHour = new Date().getHours()

            // Pre-test latency probe
            let preTestLatency: number | null = null
            let networkContext: NetworkContext = "unknown"

            if (options.contextOverride !== undefined) {
              networkContext = options.contextOverride
              yield* Effect.logDebug(`Network context override: ${networkContext}`)
            } else if (options.enableLatencyProbe !== false) {
              preTestLatency = yield* measurePingLatency()
              networkContext = inferNetworkContext(currentHour, preTestLatency, config)
              yield* Effect.logDebug(
                `Network context detected: ${networkContext} (latency: ${preTestLatency}ms, hour: ${currentHour})`
              )
            } else if (config.idle_hours.includes(currentHour)) {
              networkContext = "baseline"
            }

            // Select tool
            const selectedTool = selectTool(options.tool, available, config)
            if (!selectedTool) {
              return yield* Effect.fail(
                new SpeedtestError(
                  "no_tool",
                  "No speedtest tool available. Install Ookla CLI or speedtest-cli."
                )
              )
            }

            const effectiveServerId = options.serverId ?? config.ookla_server_id

            yield* Effect.logInfo(
              `Starting speedtest with ${selectedTool} (server: ${effectiveServerId ?? "auto"}, context: ${networkContext})`
            )

            // Run speedtest
            const toolResult = yield* runTool(
              selectedTool,
              config.timeout_seconds,
              effectiveServerId
            )

            const now = new Date()
            const speedtestResult: SpeedtestResult = {
              timestamp: now,
              timestamp_unix: now.getTime() / 1000,
              download_mbps: toolResult.download_mbps,
              upload_mbps: toolResult.upload_mbps,
              ping_ms: toolResult.ping_ms,
              jitter_ms: toolResult.jitter_ms,
              server_name: toolResult.server_name,
              server_location: toolResult.server_location,
              server_host: toolResult.server_host,
              server_id: toolResult.server_id,
              client_ip: toolResult.client_ip,
              isp: toolResult.isp,
              tool: toolResult.tool,
              result_url: toolResult.result_url,
              status: toolResult.status,
              error_message: toolResult.error_message,
              triggered_by: options.triggeredBy ?? "manual",
              network_context: networkContext,
              pre_test_latency_ms: preTestLatency,
            }

            // Store result in database
            // Note: Convert null to undefined for Effect Schema optionalWith fields
            const dbRecord: SpeedtestResultInsert = {
              timestamp: now.toISOString(),
              timestamp_unix: now.getTime() / 1000,
              download_mbps: speedtestResult.download_mbps,
              upload_mbps: speedtestResult.upload_mbps,
              ping_ms: speedtestResult.ping_ms,
              jitter_ms: speedtestResult.jitter_ms ?? undefined,
              packet_loss_percent: undefined,
              server_name: speedtestResult.server_name ?? undefined,
              server_location: speedtestResult.server_location ?? undefined,
              server_host: speedtestResult.server_host ?? undefined,
              server_id: speedtestResult.server_id ?? undefined,
              client_ip: speedtestResult.client_ip ?? undefined,
              isp: speedtestResult.isp ?? undefined,
              tool: speedtestResult.tool,
              result_url: speedtestResult.result_url ?? undefined,
              signal_snapshot: options.signalSnapshot
                ? JSON.stringify(options.signalSnapshot)
                : undefined,
              status: speedtestResult.status,
              error_message: speedtestResult.error_message ?? undefined,
              triggered_by: speedtestResult.triggered_by,
              network_context: speedtestResult.network_context,
              pre_test_latency_ms: speedtestResult.pre_test_latency_ms ?? undefined,
            }

            yield* signalRepo.insertSpeedtest(dbRecord)

            yield* Ref.set(lastResultRef, speedtestResult)

            yield* Effect.logInfo(
              `Speedtest complete: ${speedtestResult.download_mbps} Mbps down, ${speedtestResult.upload_mbps} Mbps up, ${speedtestResult.ping_ms}ms ping`
            )

            return speedtestResult
          }).pipe(
            Effect.ensuring(Ref.set(runningRef, false))
          )

          return result
        }),

      getHistory: (limit = 100) => signalRepo.querySpeedtests(limit),

      getAvailableTools: () => Ref.get(availableToolsRef),

      getToolInfo: () =>
        Effect.gen(function* () {
          const available = yield* Ref.get(availableToolsRef)
          const config = yield* Ref.get(configRef)

          return {
            available,
            all_known: ALL_KNOWN_TOOLS,
            preferred_order: config.preferred_tools,
            configured_server_id: config.ookla_server_id,
            timeout_seconds: config.timeout_seconds,
          }
        }),

      isRunning: () => Ref.get(runningRef),

      getLastResult: () => Ref.get(lastResultRef),
    }

    return impl
  })
)

// ============================================
// Layer Composition Helper
// ============================================

export const makeSpeedtestServiceLayer = (
  signalRepoLayer: Layer.Layer<SignalRepository, RepositoryError>
): Layer.Layer<SpeedtestService, RepositoryError> =>
  Layer.provide(SpeedtestServiceLive, signalRepoLayer)
