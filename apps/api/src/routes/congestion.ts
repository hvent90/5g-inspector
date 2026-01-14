/**
 * Congestion Analysis routes - endpoint for network congestion proof analysis.
 *
 * Endpoints:
 * - GET /api/congestion-proof - Generate congestion proof report
 *
 * This analyzes signal data and speedtest results to detect network congestion patterns:
 * - Correlates signal quality with speed test results
 * - Compares peak hours vs off-peak performance
 * - Identifies instances of poor speed despite good signal (indicator of congestion)
 */

import { HttpRouter, HttpServerRequest, HttpServerResponse } from "@effect/platform"
import { Effect, Schema } from "effect"
import { SignalRepository } from "../services/SignalRepository.js"
import type { SignalHistoryRecord, SpeedtestResultRecord } from "../schema/Signal.js"

// Query params for congestion endpoint
const CongestionQuerySchema = Schema.Struct({
  days: Schema.optional(Schema.NumberFromString),
})

// ============================================
// Types
// ============================================

interface CongestionProofReport {
  generated_at: string
  period_days: number
  signal_analysis: {
    acceptable_percentage: number
    metrics_5g?: {
      sinr?: { avg?: number }
      rsrp?: { avg?: number }
    }
    conclusion: string
  }
  speed_vs_signal: {
    total_tests: number
    tests_with_acceptable_signal: number
    tests_with_poor_speed_despite_good_signal: number
    statistics?: {
      avg_download_with_good_signal?: number
    }
    correlation?: {
      r?: number
      strength?: string
      interpretation?: string
    }
    conclusion: string
  }
  time_patterns: {
    period_comparison?: {
      off_peak?: { avg_speed?: number; avg_sinr?: number }
      peak?: { avg_speed?: number; avg_sinr?: number }
      speed_ratio?: number
    }
    conclusion: string
  }
  evidence_summary: Array<{
    claim: string
    data: string
    metric: string | object
  }>
  overall_conclusion: string
}

// ============================================
// Analysis Helpers
// ============================================

// Signal quality thresholds for "acceptable" signal
const ACCEPTABLE_SINR = 5 // dB - above this is acceptable
const ACCEPTABLE_RSRP = -95 // dBm - above this is acceptable
const POOR_SPEED_THRESHOLD = 25 // Mbps - below this with good signal indicates congestion

// Peak hours (typically evening)
const PEAK_HOURS = [18, 19, 20, 21, 22] // 6pm - 10pm
// Off-peak hours (early morning)
const OFF_PEAK_HOURS = [2, 3, 4, 5, 6] // 2am - 6am

/**
 * Check if a signal record has acceptable quality
 */
function hasAcceptableSignal(record: SignalHistoryRecord): boolean {
  const sinr = record.nr_sinr ?? record.lte_sinr
  const rsrp = record.nr_rsrp ?? record.lte_rsrp

  if (sinr == null && rsrp == null) return false

  const sinrOk = sinr != null ? sinr >= ACCEPTABLE_SINR : true
  const rsrpOk = rsrp != null ? rsrp >= ACCEPTABLE_RSRP : true

  return sinrOk && rsrpOk
}

/**
 * Calculate Pearson correlation coefficient
 */
function calculateCorrelation(xs: number[], ys: number[]): number | null {
  if (xs.length !== ys.length || xs.length < 3) return null

  const n = xs.length
  const sumX = xs.reduce((a, b) => a + b, 0)
  const sumY = ys.reduce((a, b) => a + b, 0)
  const sumXY = xs.reduce((sum, x, i) => sum + x * ys[i], 0)
  const sumX2 = xs.reduce((sum, x) => sum + x * x, 0)
  const sumY2 = ys.reduce((sum, y) => sum + y * y, 0)

  const numerator = n * sumXY - sumX * sumY
  const denominator = Math.sqrt((n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY))

  if (denominator === 0) return null

  return Math.round((numerator / denominator) * 1000) / 1000
}

/**
 * Interpret correlation strength
 */
function interpretCorrelation(r: number): { strength: string; interpretation: string } {
  const absR = Math.abs(r)

  if (absR < 0.2) {
    return {
      strength: "negligible",
      interpretation: "Signal and speed show almost no relationship - speed issues likely NOT due to signal quality",
    }
  } else if (absR < 0.4) {
    return {
      strength: "weak",
      interpretation: "Weak relationship between signal and speed - other factors (like congestion) may dominate",
    }
  } else if (absR < 0.6) {
    return {
      strength: "moderate",
      interpretation: "Moderate correlation - both signal quality and network congestion may affect speeds",
    }
  } else if (absR < 0.8) {
    return {
      strength: "strong",
      interpretation: "Strong correlation - signal quality is a significant factor in speed performance",
    }
  } else {
    return {
      strength: "very strong",
      interpretation: "Very strong correlation - speed is heavily dependent on signal quality",
    }
  }
}

/**
 * Get SINR value at speedtest time by finding closest signal record
 */
function getSinrAtTime(
  speedtestUnix: number,
  signalRecords: readonly SignalHistoryRecord[],
  windowSeconds = 120
): number | null {
  // Find signal records within the time window
  const nearby = signalRecords.filter(
    (r) => Math.abs(r.timestamp_unix - speedtestUnix) <= windowSeconds
  )

  if (nearby.length === 0) return null

  // Find the closest one
  const closest = nearby.reduce((prev, curr) =>
    Math.abs(curr.timestamp_unix - speedtestUnix) < Math.abs(prev.timestamp_unix - speedtestUnix)
      ? curr
      : prev
  )

  return closest.nr_sinr ?? closest.lte_sinr ?? null
}

/**
 * Get RSRP value at speedtest time by finding closest signal record
 */
function getRsrpAtTime(
  speedtestUnix: number,
  signalRecords: readonly SignalHistoryRecord[],
  windowSeconds = 120
): number | null {
  const nearby = signalRecords.filter(
    (r) => Math.abs(r.timestamp_unix - speedtestUnix) <= windowSeconds
  )

  if (nearby.length === 0) return null

  const closest = nearby.reduce((prev, curr) =>
    Math.abs(curr.timestamp_unix - speedtestUnix) < Math.abs(prev.timestamp_unix - speedtestUnix)
      ? curr
      : prev
  )

  return closest.nr_rsrp ?? closest.lte_rsrp ?? null
}

/**
 * Generate the congestion proof analysis report
 */
function generateCongestionReport(
  signalRecords: readonly SignalHistoryRecord[],
  speedtestRecords: readonly SpeedtestResultRecord[],
  periodDays: number
): CongestionProofReport {
  const now = new Date().toISOString()

  // Filter to successful speedtests only
  const successfulTests = speedtestRecords.filter((t) => t.status === "success")

  // ============================================
  // Signal Analysis
  // ============================================
  const acceptableSignalCount = signalRecords.filter(hasAcceptableSignal).length
  const acceptablePercentage =
    signalRecords.length > 0
      ? Math.round((acceptableSignalCount / signalRecords.length) * 100)
      : 0

  // Calculate average 5G metrics
  const sinrValues = signalRecords.map((r) => r.nr_sinr).filter((v): v is number => v != null)
  const rsrpValues = signalRecords.map((r) => r.nr_rsrp).filter((v): v is number => v != null)

  const avgSinr =
    sinrValues.length > 0
      ? Math.round((sinrValues.reduce((a, b) => a + b, 0) / sinrValues.length) * 10) / 10
      : undefined

  const avgRsrp =
    rsrpValues.length > 0
      ? Math.round((rsrpValues.reduce((a, b) => a + b, 0) / rsrpValues.length) * 10) / 10
      : undefined

  let signalConclusion: string
  if (acceptablePercentage >= 90) {
    signalConclusion = "Signal quality is consistently excellent - poor speeds unlikely due to signal issues"
  } else if (acceptablePercentage >= 70) {
    signalConclusion = "Signal quality is generally acceptable - occasional poor signals may affect speeds"
  } else if (acceptablePercentage >= 50) {
    signalConclusion = "Signal quality is inconsistent - signal issues may contribute to speed problems"
  } else {
    signalConclusion = "Signal quality is frequently poor - speeds may be affected by both signal and congestion"
  }

  // ============================================
  // Speed vs Signal Correlation
  // ============================================
  const testsWithSinr: Array<{ download: number; sinr: number; goodSignal: boolean }> = []

  for (const test of successfulTests) {
    const sinr = getSinrAtTime(test.timestamp_unix, signalRecords)
    const rsrp = getRsrpAtTime(test.timestamp_unix, signalRecords)

    if (sinr != null) {
      const goodSignal = sinr >= ACCEPTABLE_SINR && (rsrp == null || rsrp >= ACCEPTABLE_RSRP)
      testsWithSinr.push({
        download: test.download_mbps,
        sinr,
        goodSignal,
      })
    }
  }

  const testsWithGoodSignal = testsWithSinr.filter((t) => t.goodSignal)
  const testsWithPoorSpeedDespiteGoodSignal = testsWithGoodSignal.filter(
    (t) => t.download < POOR_SPEED_THRESHOLD
  )

  const avgDownloadWithGoodSignal =
    testsWithGoodSignal.length > 0
      ? Math.round(
          (testsWithGoodSignal.reduce((sum, t) => sum + t.download, 0) / testsWithGoodSignal.length) * 10
        ) / 10
      : undefined

  // Calculate correlation between SINR and download speed
  let correlation: CongestionProofReport["speed_vs_signal"]["correlation"] | undefined
  if (testsWithSinr.length >= 3) {
    const r = calculateCorrelation(
      testsWithSinr.map((t) => t.sinr),
      testsWithSinr.map((t) => t.download)
    )
    if (r != null) {
      const interp = interpretCorrelation(r)
      correlation = {
        r,
        strength: interp.strength,
        interpretation: interp.interpretation,
      }
    }
  }

  let speedSignalConclusion: string
  if (testsWithPoorSpeedDespiteGoodSignal.length > 0 && testsWithGoodSignal.length > 0) {
    const poorSpeedPct = Math.round(
      (testsWithPoorSpeedDespiteGoodSignal.length / testsWithGoodSignal.length) * 100
    )
    if (poorSpeedPct >= 50) {
      speedSignalConclusion = `STRONG CONGESTION INDICATOR: ${poorSpeedPct}% of tests with good signal had poor speeds (<${POOR_SPEED_THRESHOLD} Mbps)`
    } else if (poorSpeedPct >= 25) {
      speedSignalConclusion = `MODERATE CONGESTION INDICATOR: ${poorSpeedPct}% of tests with good signal had poor speeds`
    } else {
      speedSignalConclusion = `MILD CONGESTION: ${poorSpeedPct}% of tests with good signal had suboptimal speeds`
    }
  } else if (testsWithSinr.length === 0) {
    speedSignalConclusion = "Insufficient data - need more speedtests with signal data to analyze"
  } else {
    speedSignalConclusion = "No clear congestion pattern - speeds generally match signal quality expectations"
  }

  // ============================================
  // Time Pattern Analysis
  // ============================================
  const peakTests = successfulTests.filter((t) => {
    const hour = new Date(t.timestamp_unix * 1000).getHours()
    return PEAK_HOURS.includes(hour)
  })

  const offPeakTests = successfulTests.filter((t) => {
    const hour = new Date(t.timestamp_unix * 1000).getHours()
    return OFF_PEAK_HOURS.includes(hour)
  })

  const peakSignal = signalRecords.filter((r) => {
    const hour = new Date(r.timestamp_unix * 1000).getHours()
    return PEAK_HOURS.includes(hour)
  })

  const offPeakSignal = signalRecords.filter((r) => {
    const hour = new Date(r.timestamp_unix * 1000).getHours()
    return OFF_PEAK_HOURS.includes(hour)
  })

  const avgPeakSpeed =
    peakTests.length > 0
      ? Math.round((peakTests.reduce((sum, t) => sum + t.download_mbps, 0) / peakTests.length) * 10) / 10
      : null

  const avgOffPeakSpeed =
    offPeakTests.length > 0
      ? Math.round((offPeakTests.reduce((sum, t) => sum + t.download_mbps, 0) / offPeakTests.length) * 10) /
        10
      : null

  const avgPeakSinr =
    peakSignal.length > 0
      ? Math.round(
          (peakSignal
            .map((r) => r.nr_sinr ?? r.lte_sinr)
            .filter((v): v is number => v != null)
            .reduce((a, b) => a + b, 0) /
            peakSignal.filter((r) => (r.nr_sinr ?? r.lte_sinr) != null).length) *
            10
        ) / 10
      : null

  const avgOffPeakSinr =
    offPeakSignal.length > 0
      ? Math.round(
          (offPeakSignal
            .map((r) => r.nr_sinr ?? r.lte_sinr)
            .filter((v): v is number => v != null)
            .reduce((a, b) => a + b, 0) /
            offPeakSignal.filter((r) => (r.nr_sinr ?? r.lte_sinr) != null).length) *
            10
        ) / 10
      : null

  const speedRatio =
    avgPeakSpeed != null && avgOffPeakSpeed != null && avgPeakSpeed > 0
      ? Math.round((avgOffPeakSpeed / avgPeakSpeed) * 10) / 10
      : null

  let timePatternConclusion: string
  if (speedRatio != null && speedRatio > 2) {
    timePatternConclusion = `CONGESTION DETECTED: Off-peak speeds are ${speedRatio}x faster than peak hours - classic congestion pattern`
  } else if (speedRatio != null && speedRatio > 1.5) {
    timePatternConclusion = `MODERATE CONGESTION: Off-peak speeds are ${speedRatio}x faster than peak hours`
  } else if (speedRatio != null && speedRatio > 1.2) {
    timePatternConclusion = `MILD TIME VARIANCE: Off-peak speeds are ${speedRatio}x faster than peak hours`
  } else if (peakTests.length < 3 || offPeakTests.length < 3) {
    timePatternConclusion =
      "Insufficient data - need more speedtests during peak and off-peak hours for time pattern analysis"
  } else {
    timePatternConclusion = "No significant time-based pattern - speeds consistent across peak and off-peak hours"
  }

  // ============================================
  // Evidence Summary
  // ============================================
  const evidenceSummary: CongestionProofReport["evidence_summary"] = []

  if (testsWithPoorSpeedDespiteGoodSignal.length > 0) {
    evidenceSummary.push({
      claim: "Poor speeds despite good signal",
      data: `${testsWithPoorSpeedDespiteGoodSignal.length} out of ${testsWithGoodSignal.length} tests had <${POOR_SPEED_THRESHOLD} Mbps with SINR >${ACCEPTABLE_SINR}dB`,
      metric: {
        count: testsWithPoorSpeedDespiteGoodSignal.length,
        percentage: Math.round((testsWithPoorSpeedDespiteGoodSignal.length / testsWithGoodSignal.length) * 100),
      },
    })
  }

  if (speedRatio != null && speedRatio > 1.5) {
    evidenceSummary.push({
      claim: "Peak vs off-peak speed disparity",
      data: `Off-peak average: ${avgOffPeakSpeed} Mbps, Peak average: ${avgPeakSpeed} Mbps (${speedRatio}x difference)`,
      metric: { ratio: speedRatio, peak: avgPeakSpeed, offPeak: avgOffPeakSpeed },
    })
  }

  if (correlation && correlation.r != null && Math.abs(correlation.r) < 0.4) {
    evidenceSummary.push({
      claim: "Weak signal-speed correlation",
      data: `Pearson r=${correlation.r} (${correlation.strength}) - signal quality does not strongly predict speed`,
      metric: correlation.r.toString(),
    })
  }

  if (acceptablePercentage >= 70 && avgDownloadWithGoodSignal != null && avgDownloadWithGoodSignal < 50) {
    evidenceSummary.push({
      claim: "Good signal with suboptimal speeds",
      data: `Signal acceptable ${acceptablePercentage}% of time, but average download only ${avgDownloadWithGoodSignal} Mbps`,
      metric: { acceptablePct: acceptablePercentage, avgSpeed: avgDownloadWithGoodSignal },
    })
  }

  // ============================================
  // Overall Conclusion
  // ============================================
  let congestionScore = 0

  // Score based on poor speed with good signal
  if (testsWithGoodSignal.length > 0) {
    const poorSpeedPct =
      (testsWithPoorSpeedDespiteGoodSignal.length / testsWithGoodSignal.length) * 100
    if (poorSpeedPct >= 50) congestionScore += 3
    else if (poorSpeedPct >= 25) congestionScore += 2
    else if (poorSpeedPct >= 10) congestionScore += 1
  }

  // Score based on peak vs off-peak ratio
  if (speedRatio != null) {
    if (speedRatio > 2) congestionScore += 3
    else if (speedRatio > 1.5) congestionScore += 2
    else if (speedRatio > 1.2) congestionScore += 1
  }

  // Score based on weak correlation
  if (correlation && correlation.r != null) {
    if (Math.abs(correlation.r) < 0.2) congestionScore += 2
    else if (Math.abs(correlation.r) < 0.4) congestionScore += 1
  }

  let overallConclusion: string
  if (congestionScore >= 5) {
    overallConclusion =
      "CONGESTION CONFIRMED: Multiple strong indicators suggest network capacity issues rather than signal problems"
  } else if (congestionScore >= 3) {
    overallConclusion =
      "CONGESTION LIKELY: Several indicators point to network congestion affecting performance"
  } else if (congestionScore >= 1) {
    overallConclusion =
      "POSSIBLE CONGESTION: Some indicators suggest occasional congestion may affect speeds"
  } else if (successfulTests.length < 5) {
    overallConclusion =
      "INSUFFICIENT DATA: Need more speedtest results to determine congestion patterns. Run tests at different times of day."
  } else {
    overallConclusion =
      "NO CONGESTION DETECTED: Speed performance appears consistent with signal quality"
  }

  return {
    generated_at: now,
    period_days: periodDays,
    signal_analysis: {
      acceptable_percentage: acceptablePercentage,
      metrics_5g: avgSinr != null || avgRsrp != null
        ? {
            sinr: avgSinr != null ? { avg: avgSinr } : undefined,
            rsrp: avgRsrp != null ? { avg: avgRsrp } : undefined,
          }
        : undefined,
      conclusion: signalConclusion,
    },
    speed_vs_signal: {
      total_tests: successfulTests.length,
      tests_with_acceptable_signal: testsWithGoodSignal.length,
      tests_with_poor_speed_despite_good_signal: testsWithPoorSpeedDespiteGoodSignal.length,
      statistics:
        avgDownloadWithGoodSignal != null
          ? { avg_download_with_good_signal: avgDownloadWithGoodSignal }
          : undefined,
      correlation,
      conclusion: speedSignalConclusion,
    },
    time_patterns: {
      period_comparison:
        avgPeakSpeed != null || avgOffPeakSpeed != null
          ? {
              off_peak:
                avgOffPeakSpeed != null || avgOffPeakSinr != null
                  ? { avg_speed: avgOffPeakSpeed ?? undefined, avg_sinr: avgOffPeakSinr ?? undefined }
                  : undefined,
              peak:
                avgPeakSpeed != null || avgPeakSinr != null
                  ? { avg_speed: avgPeakSpeed ?? undefined, avg_sinr: avgPeakSinr ?? undefined }
                  : undefined,
              speed_ratio: speedRatio ?? undefined,
            }
          : undefined,
      conclusion: timePatternConclusion,
    },
    evidence_summary: evidenceSummary,
    overall_conclusion: overallConclusion,
  }
}

// ============================================
// Routes
// ============================================

/**
 * Congestion proof routes
 */
export const CongestionRoutes = HttpRouter.empty.pipe(
  // GET /api/congestion-proof - Generate congestion proof report
  HttpRouter.get(
    "/api/congestion-proof",
    Effect.gen(function* () {
      const request = yield* HttpServerRequest.HttpServerRequest
      const url = new URL(request.url, "http://localhost")

      const queryParams = yield* Schema.decodeUnknown(CongestionQuerySchema)({
        days: url.searchParams.get("days") ?? undefined,
      }).pipe(Effect.catchAll(() => Effect.succeed({ days: undefined })))

      const days = queryParams.days ?? 7

      const repo = yield* SignalRepository

      // Query signal history for the period
      const durationMinutes = days * 24 * 60
      const signalRecords = yield* repo.querySignalHistory({
        duration_minutes: durationMinutes,
        resolution: "full",
      })

      // Query speedtest history (get all tests in period)
      // We'll fetch a generous amount and filter by timestamp
      const cutoffUnix = Date.now() / 1000 - days * 24 * 60 * 60
      const allSpeedtests = yield* repo.querySpeedtests(1000)
      const speedtestRecords = allSpeedtests.filter((t) => t.timestamp_unix >= cutoffUnix)

      const report = generateCongestionReport(signalRecords, speedtestRecords, days)

      return HttpServerResponse.json(report)
    }).pipe(
      Effect.catchAll((error) =>
        Effect.succeed(
          HttpServerResponse.json(
            { error: `Failed to generate congestion report: ${error}` },
            { status: 500 }
          )
        )
      )
    )
  )
)
