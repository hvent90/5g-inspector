/**
 * DiagnosticsService - Effect service for generating diagnostic reports.
 *
 * Migrated from Python: backend/src/netpulse/services/diagnostics.py
 *
 * Generates comprehensive diagnostic reports including:
 * - Signal metrics summary with statistics
 * - Disruption event detection
 * - Time-of-day performance patterns
 * - Tower/cell connection history
 * - Health score calculation
 * - Export to JSON/CSV formats
 */

import { Context, Effect, Layer } from "effect"
import type { SignalHistoryRecord } from "../schema/Signal"
import { SignalRepository, RepositoryError } from "./SignalRepository"

// ============================================
// Constants
// ============================================

/** Signal quality thresholds for disruption detection */
const DISRUPTION_THRESHOLDS = {
  nr_sinr: { poor: 0, critical: -5 },
  nr_rsrp: { poor: -100, critical: -110 },
  lte_sinr: { poor: 0, critical: -5 },
  lte_rsrp: { poor: -100, critical: -110 },
} as const

// ============================================
// Types
// ============================================

/** Statistical summary for a metric */
export interface SignalStats {
  readonly count: number
  readonly avg: number | null
  readonly min: number | null
  readonly max: number | null
  readonly stdDev: number | null
  readonly median: number | null
}

/** Signal metrics summary by network type */
export interface MetricsSummary {
  readonly sinr: SignalStats
  readonly rsrp: SignalStats
  readonly rsrq: SignalStats
  readonly rssi: SignalStats
}

/** Full signal summary report */
export interface SignalSummaryReport {
  readonly durationHours: number
  readonly sampleCount: number
  readonly startTime: string | null
  readonly endTime: string | null
  readonly "5g": MetricsSummary
  readonly "4g": MetricsSummary
}

/** A disruption event detected from signal data */
export interface DiagnosticDisruptionEvent {
  readonly startTime: string
  readonly startUnix: number
  readonly endTime: string
  readonly endUnix: number | null
  readonly durationSeconds: number
  readonly severity: "poor" | "critical"
  readonly affectedMetrics: readonly string[]
  readonly tower5g: number | null
  readonly tower4g: number | null
  readonly bands5g: string | null
  readonly bands4g: string | null
}

/** Disruption analysis report */
export interface DisruptionReport {
  readonly durationHours: number
  readonly totalDisruptions: number
  readonly criticalCount: number
  readonly events: readonly DiagnosticDisruptionEvent[]
}

/** Hourly performance pattern */
export interface HourlyPattern {
  readonly hourLabel: string
  readonly sampleCount: number
  readonly "5gSinrAvg": number | null
  readonly "5gRsrpAvg": number | null
  readonly "4gSinrAvg": number | null
  readonly "4gRsrpAvg": number | null
}

/** Time-of-day pattern analysis */
export interface TimePatternReport {
  readonly durationHours: number
  readonly hourlyPatterns: Record<number, HourlyPattern>
  readonly bestHour: { hour: number | null; "5gSinrAvg": number | null }
  readonly worstHour: { hour: number | null; "5gSinrAvg": number | null }
}

/** A tower/cell handoff event */
export interface TowerChange {
  readonly timestamp: string
  readonly timestampUnix: number
  readonly type: "5G" | "4G"
  readonly fromTower: number | null
  readonly fromCell: number | null
  readonly toTower: number | null
  readonly toCell: number | null
  readonly bands: string | null
}

/** Tower usage summary */
export interface TowerUsageSummary {
  readonly durationSeconds: number
  readonly durationFormatted: string
  readonly percentage: number
}

/** Tower connection history report */
export interface TowerHistoryReport {
  readonly durationHours: number
  readonly totalChanges: number
  readonly towerChanges: readonly TowerChange[]
  readonly towerSummary: Record<string, TowerUsageSummary>
  readonly unique5gTowers: number
  readonly unique4gTowers: number
}

/** Health score breakdown */
export interface HealthScore {
  readonly overall: number
  readonly grade: "A" | "B" | "C" | "D" | "F"
  readonly breakdown: {
    readonly "5gSinr"?: number
    readonly "5gRsrp"?: number
    readonly stability: number
    readonly towerStability: number
  }
}

/** Full diagnostic report */
export interface DiagnosticReport {
  readonly generatedAt: string
  readonly durationHours: number
  readonly signalSummary: SignalSummaryReport
  readonly disruptions: DisruptionReport
  readonly timePatterns: TimePatternReport
  readonly towerHistory: TowerHistoryReport
  readonly healthScore: HealthScore
}

// ============================================
// Helper Functions
// ============================================

/** Calculate statistical summary for a list of values */
const calculateStatistics = (values: readonly (number | null | undefined)[]): SignalStats => {
  const cleanValues = values.filter((v): v is number => v != null)

  if (cleanValues.length === 0) {
    return {
      count: 0,
      avg: null,
      min: null,
      max: null,
      stdDev: null,
      median: null,
    }
  }

  const sum = cleanValues.reduce((a, b) => a + b, 0)
  const avg = sum / cleanValues.length
  const min = Math.min(...cleanValues)
  const max = Math.max(...cleanValues)

  // Standard deviation
  let stdDev: number | null = null
  if (cleanValues.length > 1) {
    const squaredDiffs = cleanValues.map((v) => Math.pow(v - avg, 2))
    const avgSquaredDiff = squaredDiffs.reduce((a, b) => a + b, 0) / (cleanValues.length - 1)
    stdDev = Math.round(Math.sqrt(avgSquaredDiff) * 100) / 100
  } else {
    stdDev = 0
  }

  // Median
  const sorted = [...cleanValues].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  const median =
    sorted.length % 2 === 0
      ? (sorted[mid - 1] + sorted[mid]) / 2
      : sorted[mid]

  return {
    count: cleanValues.length,
    avg: Math.round(avg * 100) / 100,
    min: Math.round(min * 100) / 100,
    max: Math.round(max * 100) / 100,
    stdDev,
    median: Math.round(median * 100) / 100,
  }
}

/** Format duration in seconds to human readable string */
const formatDuration = (seconds: number): string => {
  if (seconds < 60) {
    return `${Math.floor(seconds)}s`
  } else if (seconds < 3600) {
    const mins = Math.floor(seconds / 60)
    const secs = Math.floor(seconds % 60)
    return `${mins}m ${secs}s`
  } else {
    const hours = Math.floor(seconds / 3600)
    const mins = Math.floor((seconds % 3600) / 60)
    return `${hours}h ${mins}m`
  }
}

/** Create empty metrics summary */
const emptyMetricsSummary = (): MetricsSummary => ({
  sinr: { count: 0, avg: null, min: null, max: null, stdDev: null, median: null },
  rsrp: { count: 0, avg: null, min: null, max: null, stdDev: null, median: null },
  rsrq: { count: 0, avg: null, min: null, max: null, stdDev: null, median: null },
  rssi: { count: 0, avg: null, min: null, max: null, stdDev: null, median: null },
})

// ============================================
// Service Tag
// ============================================

export class DiagnosticsService extends Context.Tag("DiagnosticsService")<
  DiagnosticsService,
  {
    /**
     * Get signal metrics summary with statistics.
     */
    readonly getSignalMetricsSummary: (
      durationHours?: number
    ) => Effect.Effect<SignalSummaryReport, RepositoryError>

    /**
     * Detect signal disruption events based on thresholds.
     */
    readonly detectDisruptions: (
      durationHours?: number
    ) => Effect.Effect<DisruptionReport, RepositoryError>

    /**
     * Analyze signal performance patterns by time of day.
     */
    readonly getTimeOfDayPatterns: (
      durationHours?: number
    ) => Effect.Effect<TimePatternReport, RepositoryError>

    /**
     * Get history of tower/cell connections.
     */
    readonly getTowerConnectionHistory: (
      durationHours?: number
    ) => Effect.Effect<TowerHistoryReport, RepositoryError>

    /**
     * Generate comprehensive diagnostic report.
     */
    readonly generateFullReport: (
      durationHours?: number
    ) => Effect.Effect<DiagnosticReport, RepositoryError>

    /**
     * Export report to JSON format.
     */
    readonly exportToJson: (report: DiagnosticReport) => string

    /**
     * Export report to CSV format.
     */
    readonly exportToCsv: (report: DiagnosticReport) => string
  }
>() {}

// ============================================
// Service Implementation
// ============================================

export const DiagnosticsServiceLive = Layer.effect(
  DiagnosticsService,
  Effect.gen(function* () {
    const repo = yield* SignalRepository

    /**
     * Query signal history for a duration.
     */
    const queryHistoryForDuration = (durationHours: number) => {
      const durationMinutes = durationHours * 60
      return repo.querySignalHistory({
        duration_minutes: durationMinutes,
        resolution: "full",
      })
    }

    /**
     * Get signal metrics summary with statistics.
     */
    const getSignalMetricsSummary = (durationHours = 24): Effect.Effect<SignalSummaryReport, RepositoryError> =>
      Effect.gen(function* () {
        const rows = yield* queryHistoryForDuration(durationHours)

        if (rows.length === 0) {
          return {
            durationHours,
            sampleCount: 0,
            startTime: null,
            endTime: null,
            "5g": emptyMetricsSummary(),
            "4g": emptyMetricsSummary(),
          }
        }

        return {
          durationHours,
          sampleCount: rows.length,
          startTime: rows[0].timestamp,
          endTime: rows[rows.length - 1].timestamp,
          "5g": {
            sinr: calculateStatistics(rows.map((r) => r.nr_sinr)),
            rsrp: calculateStatistics(rows.map((r) => r.nr_rsrp)),
            rsrq: calculateStatistics(rows.map((r) => r.nr_rsrq)),
            rssi: calculateStatistics(rows.map((r) => r.nr_rssi)),
          },
          "4g": {
            sinr: calculateStatistics(rows.map((r) => r.lte_sinr)),
            rsrp: calculateStatistics(rows.map((r) => r.lte_rsrp)),
            rsrq: calculateStatistics(rows.map((r) => r.lte_rsrq)),
            rssi: calculateStatistics(rows.map((r) => r.lte_rssi)),
          },
        }
      })

    /**
     * Detect signal disruption events based on thresholds.
     */
    const detectDisruptions = (durationHours = 24): Effect.Effect<DisruptionReport, RepositoryError> =>
      Effect.gen(function* () {
        const rows = yield* queryHistoryForDuration(durationHours)

        // Mutable working type for building disruption events
        interface WorkingDisruption {
          startTime: string
          startUnix: number
          severity: "poor" | "critical"
          affectedMetrics: readonly string[]
          tower5g: number | null
          tower4g: number | null
          bands5g: string | null
          bands4g: string | null
        }

        const disruptions: DiagnosticDisruptionEvent[] = []
        let inDisruption = false
        let disruptionStart = 0
        let disruptionData: Partial<WorkingDisruption> = {}

        for (const row of rows) {
          let isPoor = false
          let severity: "poor" | "critical" = "poor"
          const affectedMetrics: string[] = []

          // Check 5G metrics
          if (row.nr_sinr != null) {
            if (row.nr_sinr <= DISRUPTION_THRESHOLDS.nr_sinr.critical) {
              isPoor = true
              severity = "critical"
              affectedMetrics.push(`5G SINR: ${row.nr_sinr}dB`)
            } else if (row.nr_sinr <= DISRUPTION_THRESHOLDS.nr_sinr.poor) {
              isPoor = true
              affectedMetrics.push(`5G SINR: ${row.nr_sinr}dB`)
            }
          }

          if (row.nr_rsrp != null) {
            if (row.nr_rsrp <= DISRUPTION_THRESHOLDS.nr_rsrp.critical) {
              isPoor = true
              severity = "critical"
              affectedMetrics.push(`5G RSRP: ${row.nr_rsrp}dBm`)
            } else if (row.nr_rsrp <= DISRUPTION_THRESHOLDS.nr_rsrp.poor) {
              isPoor = true
              affectedMetrics.push(`5G RSRP: ${row.nr_rsrp}dBm`)
            }
          }

          // Check 4G metrics
          if (row.lte_sinr != null) {
            if (row.lte_sinr <= DISRUPTION_THRESHOLDS.lte_sinr.critical) {
              isPoor = true
              severity = "critical"
              affectedMetrics.push(`4G SINR: ${row.lte_sinr}dB`)
            } else if (row.lte_sinr <= DISRUPTION_THRESHOLDS.lte_sinr.poor) {
              isPoor = true
              affectedMetrics.push(`4G SINR: ${row.lte_sinr}dB`)
            }
          }

          if (row.lte_rsrp != null) {
            if (row.lte_rsrp <= DISRUPTION_THRESHOLDS.lte_rsrp.critical) {
              isPoor = true
              severity = "critical"
              affectedMetrics.push(`4G RSRP: ${row.lte_rsrp}dBm`)
            } else if (row.lte_rsrp <= DISRUPTION_THRESHOLDS.lte_rsrp.poor) {
              isPoor = true
              affectedMetrics.push(`4G RSRP: ${row.lte_rsrp}dBm`)
            }
          }

          if (isPoor && !inDisruption) {
            // Start of disruption
            inDisruption = true
            disruptionStart = row.timestamp_unix
            disruptionData = {
              startTime: row.timestamp,
              startUnix: row.timestamp_unix,
              severity,
              affectedMetrics,
              tower5g: row.nr_gnb_id ?? null,
              tower4g: row.lte_enb_id ?? null,
              bands5g: row.nr_bands ?? null,
              bands4g: row.lte_bands ?? null,
            }
          } else if (!isPoor && inDisruption) {
            // End of disruption
            inDisruption = false
            disruptions.push({
              ...disruptionData,
              endTime: row.timestamp,
              endUnix: row.timestamp_unix,
              durationSeconds: Math.round((row.timestamp_unix - disruptionStart) * 10) / 10,
            } as DiagnosticDisruptionEvent)
            disruptionData = {}
          } else if (isPoor && inDisruption) {
            // Update severity if worse
            if (severity === "critical") {
              disruptionData.severity = "critical"
            }
          }
        }

        // Handle ongoing disruption
        if (inDisruption && disruptionData.startTime) {
          disruptions.push({
            ...disruptionData,
            endTime: "ongoing",
            endUnix: null,
            durationSeconds: Math.round((Date.now() / 1000 - disruptionStart) * 10) / 10,
          } as DiagnosticDisruptionEvent)
        }

        return {
          durationHours,
          totalDisruptions: disruptions.length,
          criticalCount: disruptions.filter((d) => d.severity === "critical").length,
          events: disruptions,
        }
      })

    /**
     * Analyze signal performance patterns by time of day.
     */
    const getTimeOfDayPatterns = (durationHours = 168): Effect.Effect<TimePatternReport, RepositoryError> =>
      Effect.gen(function* () {
        const rows = yield* queryHistoryForDuration(durationHours)

        // Group by hour of day
        const hourlyData: Map<number, {
          nrSinr: number[]
          nrRsrp: number[]
          lteSinr: number[]
          lteRsrp: number[]
        }> = new Map()

        for (let i = 0; i < 24; i++) {
          hourlyData.set(i, { nrSinr: [], nrRsrp: [], lteSinr: [], lteRsrp: [] })
        }

        for (const row of rows) {
          const hour = new Date(row.timestamp_unix * 1000).getHours()
          const data = hourlyData.get(hour)!

          if (row.nr_sinr != null) data.nrSinr.push(row.nr_sinr)
          if (row.nr_rsrp != null) data.nrRsrp.push(row.nr_rsrp)
          if (row.lte_sinr != null) data.lteSinr.push(row.lte_sinr)
          if (row.lte_rsrp != null) data.lteRsrp.push(row.lte_rsrp)
        }

        // Calculate statistics per hour
        const patterns: Record<number, HourlyPattern> = {}
        const validHours: [number, number][] = []

        for (let hour = 0; hour < 24; hour++) {
          const data = hourlyData.get(hour)!
          const avg5gSinr = data.nrSinr.length > 0
            ? Math.round((data.nrSinr.reduce((a, b) => a + b, 0) / data.nrSinr.length) * 100) / 100
            : null
          const avg5gRsrp = data.nrRsrp.length > 0
            ? Math.round((data.nrRsrp.reduce((a, b) => a + b, 0) / data.nrRsrp.length) * 100) / 100
            : null
          const avg4gSinr = data.lteSinr.length > 0
            ? Math.round((data.lteSinr.reduce((a, b) => a + b, 0) / data.lteSinr.length) * 100) / 100
            : null
          const avg4gRsrp = data.lteRsrp.length > 0
            ? Math.round((data.lteRsrp.reduce((a, b) => a + b, 0) / data.lteRsrp.length) * 100) / 100
            : null

          patterns[hour] = {
            hourLabel: `${hour.toString().padStart(2, "0")}:00`,
            sampleCount: data.nrSinr.length,
            "5gSinrAvg": avg5gSinr,
            "5gRsrpAvg": avg5gRsrp,
            "4gSinrAvg": avg4gSinr,
            "4gRsrpAvg": avg4gRsrp,
          }

          if (avg5gSinr != null) {
            validHours.push([hour, avg5gSinr])
          }
        }

        // Find best and worst hours
        let bestHour: { hour: number | null; "5gSinrAvg": number | null } = { hour: null, "5gSinrAvg": null }
        let worstHour: { hour: number | null; "5gSinrAvg": number | null } = { hour: null, "5gSinrAvg": null }

        if (validHours.length > 0) {
          const best = validHours.reduce((a, b) => (a[1] > b[1] ? a : b))
          const worst = validHours.reduce((a, b) => (a[1] < b[1] ? a : b))
          bestHour = { hour: best[0], "5gSinrAvg": best[1] }
          worstHour = { hour: worst[0], "5gSinrAvg": worst[1] }
        }

        return {
          durationHours,
          hourlyPatterns: patterns,
          bestHour,
          worstHour,
        }
      })

    /**
     * Get history of tower/cell connections.
     */
    const getTowerConnectionHistory = (durationHours = 24): Effect.Effect<TowerHistoryReport, RepositoryError> =>
      Effect.gen(function* () {
        const rows = yield* queryHistoryForDuration(durationHours)

        if (rows.length === 0) {
          return {
            durationHours,
            totalChanges: 0,
            towerChanges: [],
            towerSummary: {},
            unique5gTowers: 0,
            unique4gTowers: 0,
          }
        }

        const changes: TowerChange[] = []
        let last5gTower: { gnb: number | null; cid: number | null; bands: string | null } | null = null
        let last4gTower: { enb: number | null; cid: number | null; bands: string | null } | null = null
        const towerDurations: Map<string, number> = new Map()
        let lastTimestamp: number | null = null

        for (const row of rows) {
          const current5g = { gnb: row.nr_gnb_id ?? null, cid: row.nr_cid ?? null, bands: row.nr_bands ?? null }
          const current4g = { enb: row.lte_enb_id ?? null, cid: row.lte_cid ?? null, bands: row.lte_bands ?? null }

          // Track duration on current tower
          if (lastTimestamp != null) {
            const duration = row.timestamp_unix - lastTimestamp
            if (last5gTower?.gnb != null) {
              const key = `5G-${last5gTower.gnb}`
              towerDurations.set(key, (towerDurations.get(key) ?? 0) + duration)
            }
            if (last4gTower?.enb != null) {
              const key = `4G-${last4gTower.enb}`
              towerDurations.set(key, (towerDurations.get(key) ?? 0) + duration)
            }
          }

          // Check for 5G tower change
          if (current5g.gnb != null && (last5gTower == null || current5g.gnb !== last5gTower.gnb || current5g.cid !== last5gTower.cid)) {
            if (last5gTower != null) {
              changes.push({
                timestamp: row.timestamp,
                timestampUnix: row.timestamp_unix,
                type: "5G",
                fromTower: last5gTower.gnb,
                fromCell: last5gTower.cid,
                toTower: current5g.gnb,
                toCell: current5g.cid,
                bands: current5g.bands,
              })
            }
            last5gTower = current5g
          }

          // Check for 4G tower change
          if (current4g.enb != null && (last4gTower == null || current4g.enb !== last4gTower.enb || current4g.cid !== last4gTower.cid)) {
            if (last4gTower != null) {
              changes.push({
                timestamp: row.timestamp,
                timestampUnix: row.timestamp_unix,
                type: "4G",
                fromTower: last4gTower.enb,
                fromCell: last4gTower.cid,
                toTower: current4g.enb,
                toCell: current4g.cid,
                bands: current4g.bands,
              })
            }
            last4gTower = current4g
          }

          lastTimestamp = row.timestamp_unix
        }

        // Calculate tower summary
        const totalDuration = durationHours * 3600
        const towerSummary: Record<string, TowerUsageSummary> = {}

        for (const [towerId, duration] of towerDurations) {
          towerSummary[towerId] = {
            durationSeconds: Math.round(duration * 10) / 10,
            durationFormatted: formatDuration(duration),
            percentage: totalDuration > 0 ? Math.round((duration / totalDuration) * 1000) / 10 : 0,
          }
        }

        const unique5g = [...towerDurations.keys()].filter((k) => k.startsWith("5G")).length
        const unique4g = [...towerDurations.keys()].filter((k) => k.startsWith("4G")).length

        return {
          durationHours,
          totalChanges: changes.length,
          towerChanges: changes.slice(-50), // Last 50 changes
          towerSummary,
          unique5gTowers: unique5g,
          unique4gTowers: unique4g,
        }
      })

    /**
     * Calculate overall connection health score (0-100).
     */
    const calculateHealthScore = (
      signalSummary: SignalSummaryReport,
      disruptions: DisruptionReport,
      towerHistory: TowerHistoryReport
    ): HealthScore => {
      const scores: number[] = []
      const breakdown: Record<string, number> = {}

      // 5G SINR score
      const sinr5g = signalSummary["5g"].sinr.avg
      if (sinr5g != null) {
        let sinrScore: number
        if (sinr5g >= 20) {
          sinrScore = 100
        } else if (sinr5g >= 10) {
          sinrScore = 70
        } else if (sinr5g >= 0) {
          sinrScore = 40
        } else {
          sinrScore = 20
        }
        scores.push(sinrScore)
        breakdown["5gSinr"] = sinrScore
      }

      // 5G RSRP score
      const rsrp5g = signalSummary["5g"].rsrp.avg
      if (rsrp5g != null) {
        let rsrpScore: number
        if (rsrp5g >= -80) {
          rsrpScore = 100
        } else if (rsrp5g >= -90) {
          rsrpScore = 70
        } else if (rsrp5g >= -100) {
          rsrpScore = 40
        } else {
          rsrpScore = 20
        }
        scores.push(rsrpScore)
        breakdown["5gRsrp"] = rsrpScore
      }

      // Disruption penalty (stability score)
      let stabilityScore: number
      if (disruptions.totalDisruptions === 0) {
        stabilityScore = 100
      } else if (disruptions.totalDisruptions <= 5) {
        stabilityScore = 80 - disruptions.criticalCount * 10
      } else if (disruptions.totalDisruptions <= 20) {
        stabilityScore = 50 - disruptions.criticalCount * 5
      } else {
        stabilityScore = Math.max(0, 30 - disruptions.criticalCount * 5)
      }
      scores.push(stabilityScore)
      breakdown.stability = stabilityScore

      // Tower stability score
      let towerScore: number
      if (towerHistory.totalChanges <= 2) {
        towerScore = 100
      } else if (towerHistory.totalChanges <= 10) {
        towerScore = 70
      } else if (towerHistory.totalChanges <= 50) {
        towerScore = 40
      } else {
        towerScore = 20
      }
      scores.push(towerScore)
      breakdown.towerStability = towerScore

      // Calculate overall
      const overall = scores.length > 0
        ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length)
        : 0

      // Determine grade
      let grade: "A" | "B" | "C" | "D" | "F"
      if (overall >= 90) {
        grade = "A"
      } else if (overall >= 80) {
        grade = "B"
      } else if (overall >= 70) {
        grade = "C"
      } else if (overall >= 60) {
        grade = "D"
      } else {
        grade = "F"
      }

      return {
        overall,
        grade,
        breakdown: breakdown as HealthScore["breakdown"],
      }
    }

    /**
     * Generate comprehensive diagnostic report.
     */
    const generateFullReport = (durationHours = 24): Effect.Effect<DiagnosticReport, RepositoryError> =>
      Effect.gen(function* () {
        const signalSummary = yield* getSignalMetricsSummary(durationHours)
        const disruptions = yield* detectDisruptions(durationHours)
        const timePatterns = yield* getTimeOfDayPatterns(Math.min(durationHours, 168))
        const towerHistory = yield* getTowerConnectionHistory(durationHours)

        const healthScore = calculateHealthScore(signalSummary, disruptions, towerHistory)

        return {
          generatedAt: new Date().toISOString(),
          durationHours,
          signalSummary,
          disruptions,
          timePatterns,
          towerHistory,
          healthScore,
        }
      })

    /**
     * Export report to JSON format.
     */
    const exportToJson = (report: DiagnosticReport): string => {
      return JSON.stringify(report, null, 2)
    }

    /**
     * Export report to CSV format.
     */
    const exportToCsv = (report: DiagnosticReport): string => {
      const lines: string[] = []

      // Header
      lines.push("=== NETPULSE DIAGNOSTIC REPORT ===")
      lines.push(`Generated: ${report.generatedAt}`)
      lines.push(`Duration: ${report.durationHours} hours`)
      lines.push(`Health Score: ${report.healthScore.overall}/100 (${report.healthScore.grade})`)
      lines.push("")

      // Signal metrics
      lines.push("=== SIGNAL METRICS SUMMARY ===")
      lines.push("Network,Metric,Average,Min,Max,Std Dev,Median,Samples")

      for (const network of ["5g", "4g"] as const) {
        const data = report.signalSummary[network]
        for (const [metric, stats] of Object.entries(data)) {
          lines.push([
            network.toUpperCase(),
            metric.toUpperCase(),
            stats.avg ?? "",
            stats.min ?? "",
            stats.max ?? "",
            stats.stdDev ?? "",
            stats.median ?? "",
            stats.count,
          ].join(","))
        }
      }
      lines.push("")

      // Disruptions
      lines.push("=== DISRUPTION EVENTS ===")
      lines.push("Start Time,End Time,Duration (s),Severity,Tower 5G,Tower 4G,Affected Metrics")

      for (const event of report.disruptions.events) {
        lines.push([
          event.startTime,
          event.endTime,
          event.durationSeconds,
          event.severity,
          event.tower5g ?? "",
          event.tower4g ?? "",
          `"${event.affectedMetrics.join("; ")}"`,
        ].join(","))
      }
      lines.push("")

      // Time patterns
      lines.push("=== TIME OF DAY PATTERNS ===")
      lines.push("Hour,Samples,5G SINR Avg,5G RSRP Avg,4G SINR Avg,4G RSRP Avg")

      for (let hour = 0; hour < 24; hour++) {
        const pattern = report.timePatterns.hourlyPatterns[hour]
        if (pattern) {
          lines.push([
            pattern.hourLabel,
            pattern.sampleCount,
            pattern["5gSinrAvg"] ?? "",
            pattern["5gRsrpAvg"] ?? "",
            pattern["4gSinrAvg"] ?? "",
            pattern["4gRsrpAvg"] ?? "",
          ].join(","))
        }
      }
      lines.push("")

      // Tower history
      lines.push("=== TOWER CONNECTION SUMMARY ===")
      lines.push("Tower ID,Duration,Percentage")

      for (const [towerId, stats] of Object.entries(report.towerHistory.towerSummary)) {
        lines.push([
          towerId,
          stats.durationFormatted,
          `${stats.percentage}%`,
        ].join(","))
      }

      return lines.join("\n")
    }

    return {
      getSignalMetricsSummary,
      detectDisruptions,
      getTimeOfDayPatterns,
      getTowerConnectionHistory,
      generateFullReport,
      exportToJson,
      exportToCsv,
    }
  })
)

// ============================================
// Convenience Layer with SignalRepository
// ============================================

import { SignalRepositoryLive, makeSqliteConnectionLayer } from "./SignalRepository"

/**
 * Create a fully-wired DiagnosticsService with SQLite backing.
 */
export const makeDiagnosticsServiceLayer = (dbPath: string) => {
  const sqliteLayer = makeSqliteConnectionLayer(dbPath)
  const repoLayer = Layer.provide(SignalRepositoryLive, sqliteLayer)
  return Layer.provide(DiagnosticsServiceLive, repoLayer)
}
