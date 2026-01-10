import "./styles.css";
import { formatSignalStrength, formatSpeed } from "@tmobile-dashboard/shared";
import { createElement } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./components";

// Re-export shared utilities for use in templates
console.log("Shared utilities loaded:", { formatSignalStrength, formatSpeed });

interface SignalMetrics {
  sinr: number;
  rsrp: number;
  rsrq: number;
  rssi: number;
  bands: string[];
  tower_id?: number;
  cell_id?: number;
  quality?: string;
}

interface SignalData {
  timestamp: string;
  timestamp_unix: number;
  nr?: SignalMetrics;
  lte?: SignalMetrics;
  registration_status?: string;
  connection_mode?: string;
  device_uptime?: number | null;
}


const REFRESH_INTERVAL = 1000;

function $(id: string): HTMLElement | null {
  return document.getElementById(id);
}

function getQuality(type: "sinr" | "rsrp", val: number | null): string {
  if (val === null || val === undefined) return "";
  if (type === "sinr") {
    return val >= 20 ? "good" : val >= 10 ? "ok" : "bad";
  }
  return val >= -80 ? "good" : val >= -100 ? "ok" : "bad";
}

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function updateMetric(id: string, value: number | null | undefined, qualityType?: "sinr" | "rsrp") {
  const el = $(id);
  if (!el) return;

  if (value === null || value === undefined) {
    el.textContent = "--";
    el.className = "metric-value";
  } else {
    el.textContent = String(value);
    el.className = "metric-value";
    if (qualityType) {
      const quality = getQuality(qualityType, value);
      if (quality) el.classList.add(quality);
    }
  }
}

function updateText(id: string, value: string | number | null | undefined) {
  const el = $(id);
  if (el) {
    el.textContent = value !== null && value !== undefined ? String(value) : "--";
  }
}

async function fetchSignal(): Promise<SignalData | null> {
  try {
    const response = await fetch("/api/signal");
    if (!response.ok) throw new Error("Fetch failed");
    return await response.json();
  } catch {
    return null;
  }
}

function updateUI(data: SignalData) {
  const status = $("status");
  if (status) {
    status.textContent = "Connected";
    status.classList.add("connected");
  }

  // 5G (NR) metrics
  const nr = data.nr;
  updateMetric("sinr-5g", nr?.sinr, "sinr");
  updateMetric("rsrp-5g", nr?.rsrp, "rsrp");
  updateMetric("rsrq-5g", nr?.rsrq);
  updateMetric("rssi-5g", nr?.rssi);
  updateText("band-5g", nr?.bands?.join(", ")?.toUpperCase());

  // 4G (LTE) metrics
  const lte = data.lte;
  updateMetric("sinr-4g", lte?.sinr, "sinr");
  updateMetric("rsrp-4g", lte?.rsrp, "rsrp");
  updateMetric("rsrq-4g", lte?.rsrq);
  updateMetric("rssi-4g", lte?.rssi);
  updateText("band-4g", lte?.bands?.join(", ")?.toUpperCase());

  // Connection info
  updateText("tower-id", nr?.tower_id || lte?.tower_id);
  updateText("cell-id", nr?.cell_id || lte?.cell_id);
  updateText("registration", data.registration_status);

  if (data.device_uptime) {
    updateText("uptime", formatUptime(data.device_uptime));
  }

  // Connection mode
  updateText("connection-mode", data.connection_mode);
}

async function refresh() {
  const data = await fetchSignal();
  if (data) {
    updateUI(data);
  } else {
    const status = $("status");
    if (status) {
      status.textContent = "Disconnected";
      status.classList.remove("connected");
    }
  }
}

// React mounting for Base UI components
function initReactComponents(): void {
  // Mount Report Controls
  const reportMount = $("report-controls-mount");
  if (reportMount) {
    const root = createRoot(reportMount);
    root.render(createElement(App, { component: 'report' }));
  }

  // Mount Speed Test Controls
  const speedtestMount = $("speedtest-controls-mount");
  if (speedtestMount) {
    const root = createRoot(speedtestMount);
    root.render(createElement(App, { component: 'speedtest' }));
  }
}

// Network Quality Monitoring Types
interface NetworkQualityResult {
  target_name?: string;
  target_host: string;
  packet_loss_percent: number;
  jitter_ms: number;
  // Backend returns latency_avg from /trigger, ping_ms from database queries
  latency_avg?: number | null;
  ping_ms?: number | null;
  status?: string;
  timestamp: string;
}

interface NetworkQualityStatus {
  config: {
    enabled: boolean;
    interval_minutes: number;
  };
  stats: {
    is_running: boolean;
    tests_completed: number;
    last_test_time: number;
    next_test_time: number;
    next_test_in_seconds: number | null;
  };
}

let nqIsMonitoring = false;
// @ts-ignore - Used for future cleanup functionality
let nqStatusInterval: ReturnType<typeof setInterval> | null = null;

function getQualityClass(type: "loss" | "jitter" | "latency", value: number | null): string {
  if (value === null || value === undefined) return "";
  if (type === "loss") {
    return value <= 1 ? "good" : value <= 5 ? "ok" : "bad";
  }
  if (type === "jitter") {
    return value <= 10 ? "good" : value <= 30 ? "ok" : "bad";
  }
  // latency
  return value <= 50 ? "good" : value <= 100 ? "ok" : "bad";
}

function updateNQResults(results: NetworkQualityResult[]): void {
  const resultsEl = $("nq-results");
  if (!resultsEl) return;

  results.forEach((result, index) => {
    const i = index + 1;
    if (i > 3) return; // Only show first 3 targets

    const targetEl = $(`nq-target-${i}`);
    if (targetEl) {
      targetEl.style.display = "block";
    }

    // Backend returns target_name from /trigger, but not from database queries
    updateText(`nq-name-${i}`, result.target_name || result.target_host);
    updateText(`nq-host-${i}`, result.target_host);

    const lossEl = $(`nq-loss-${i}`);
    if (lossEl) {
      lossEl.textContent = result.packet_loss_percent !== null ? `${result.packet_loss_percent}%` : "--%";
      lossEl.className = "nq-metric-value";
      const quality = getQualityClass("loss", result.packet_loss_percent);
      if (quality) lossEl.classList.add(quality);
    }

    const jitterEl = $(`nq-jitter-${i}`);
    if (jitterEl) {
      jitterEl.textContent = result.jitter_ms !== null ? `${result.jitter_ms} ms` : "-- ms";
      jitterEl.className = "nq-metric-value";
      const quality = getQualityClass("jitter", result.jitter_ms);
      if (quality) jitterEl.classList.add(quality);
    }

    // Backend returns latency_avg from /trigger, ping_ms from database queries
    const latency = result.latency_avg ?? result.ping_ms ?? null;
    const latencyEl = $(`nq-latency-${i}`);
    if (latencyEl) {
      latencyEl.textContent = latency !== null ? `${latency} ms` : "-- ms";
      latencyEl.className = "nq-metric-value";
      const quality = getQualityClass("latency", latency);
      if (quality) latencyEl.classList.add(quality);
    }
  });

  resultsEl.style.display = "block";

  // Update last test time
  if (results.length > 0 && results[0].timestamp) {
    const lastTestEl = $("nq-last-test");
    if (lastTestEl) {
      const date = new Date(results[0].timestamp);
      lastTestEl.textContent = `Last test: ${date.toLocaleTimeString()}`;
    }
  }
}

async function fetchNQStatus(): Promise<NetworkQualityStatus | null> {
  try {
    // Backend has separate /config and /stats endpoints - fetch both
    const [configRes, statsRes] = await Promise.all([
      fetch("/api/network-quality/config"),
      fetch("/api/network-quality/stats"),
    ]);
    if (!configRes.ok || !statsRes.ok) throw new Error("Fetch failed");
    const config = await configRes.json();
    const stats = await statsRes.json();
    return { config, stats };
  } catch {
    return null;
  }
}

async function fetchNQLatest(): Promise<NetworkQualityResult[]> {
  try {
    // Backend endpoint is /api/network-quality (returns { count, results })
    const response = await fetch("/api/network-quality");
    if (!response.ok) throw new Error("Fetch failed");
    const data = await response.json();
    return data.results || [];
  } catch {
    return [];
  }
}

async function updateNQStatusUI(): Promise<void> {
  const status = await fetchNQStatus();
  const statusEl = $("nq-status");
  const toggleBtn = $("nq-toggle-btn") as HTMLButtonElement | null;

  if (status) {
    nqIsMonitoring = status.stats.is_running;

    if (statusEl) {
      statusEl.textContent = status.stats.is_running ? "Running" : "Stopped";
      statusEl.className = "section-badge" + (status.stats.is_running ? " connected" : "");
    }

    if (toggleBtn) {
      toggleBtn.textContent = status.stats.is_running ? "Stop Monitoring" : "Start Monitoring";
    }

    // Update next test time
    const nextTestEl = $("nq-next-test");
    if (nextTestEl && status.stats.next_test_in_seconds !== null && status.stats.is_running) {
      const mins = Math.floor(status.stats.next_test_in_seconds / 60);
      const secs = Math.floor(status.stats.next_test_in_seconds % 60);
      nextTestEl.textContent = ` | Next: ${mins}m ${secs}s`;
    } else if (nextTestEl) {
      nextTestEl.textContent = "";
    }

    // Fetch and display latest results if we have any
    if (status.stats.tests_completed > 0) {
      const latest = await fetchNQLatest();
      if (latest.length > 0) {
        updateNQResults(latest);
      }
    }
  }
}

async function toggleNQMonitoring(): Promise<void> {
  const toggleBtn = $("nq-toggle-btn") as HTMLButtonElement | null;
  const errorEl = $("nq-error");

  if (!toggleBtn) return;

  toggleBtn.disabled = true;
  toggleBtn.textContent = "...";

  if (errorEl) {
    errorEl.style.display = "none";
  }

  try {
    const endpoint = nqIsMonitoring ? "/api/network-quality/stop" : "/api/network-quality/start";
    const response = await fetch(endpoint, { method: "POST" });

    if (!response.ok) {
      throw new Error("Failed to toggle monitoring");
    }

    await updateNQStatusUI();
  } catch (err) {
    if (errorEl) {
      errorEl.textContent = err instanceof Error ? err.message : "Failed to toggle monitoring";
      errorEl.style.display = "block";
    }
  } finally {
    toggleBtn.disabled = false;
    toggleBtn.textContent = nqIsMonitoring ? "Stop Monitoring" : "Start Monitoring";
  }
}

async function runNQTestNow(): Promise<void> {
  const testBtn = $("nq-test-btn") as HTMLButtonElement | null;
  const errorEl = $("nq-error");

  if (!testBtn) return;

  testBtn.disabled = true;
  testBtn.textContent = "Testing...";

  if (errorEl) {
    errorEl.style.display = "none";
  }

  try {
    const response = await fetch("/api/network-quality/trigger", { method: "POST" });

    if (!response.ok) {
      throw new Error("Test failed");
    }

    const data = await response.json();

    if (data.results && data.results.length > 0) {
      updateNQResults(data.results);
    }
  } catch (err) {
    if (errorEl) {
      errorEl.textContent = err instanceof Error ? err.message : "Test failed";
      errorEl.style.display = "block";
    }
  } finally {
    testBtn.disabled = false;
    testBtn.textContent = "Test Now";
  }
}

function initNetworkQuality(): void {
  const toggleBtn = $("nq-toggle-btn");
  const testBtn = $("nq-test-btn");

  if (toggleBtn) {
    toggleBtn.addEventListener("click", toggleNQMonitoring);
  }

  if (testBtn) {
    testBtn.addEventListener("click", runNQTestNow);
  }

  // Initial status fetch
  updateNQStatusUI();

  // Poll for status updates every 5 seconds
  nqStatusInterval = setInterval(updateNQStatusUI, 5000);
}

// Congestion Analysis Types
interface CongestionProofReport {
  generated_at: string;
  period_days: number;
  signal_analysis: {
    acceptable_percentage: number;
    metrics_5g?: {
      sinr?: { avg?: number };
      rsrp?: { avg?: number };
    };
    conclusion: string;
  };
  speed_vs_signal: {
    total_tests: number;
    tests_with_acceptable_signal: number;
    tests_with_poor_speed_despite_good_signal: number;
    statistics?: {
      avg_download_with_good_signal?: number;
    };
    correlation?: {
      r?: number;
      strength?: string;
      interpretation?: string;
    };
    conclusion: string;
  };
  time_patterns: {
    period_comparison?: {
      off_peak?: { avg_speed?: number; avg_sinr?: number };
      peak?: { avg_speed?: number; avg_sinr?: number };
      speed_ratio?: number;
    };
    conclusion: string;
  };
  evidence_summary: Array<{
    claim: string;
    data: string;
    metric: string | object;
  }>;
  overall_conclusion: string;
}

async function loadCongestionAnalysis(): Promise<void> {
  const btn = $("congestion-btn") as HTMLButtonElement | null;
  const status = $("congestion-status");
  const content = $("congestion-content");
  const error = $("congestion-error");
  const durationSelect = $("congestion-duration") as HTMLSelectElement | null;

  if (!btn || !status || !content || !error || !durationSelect) return;

  const days = parseInt(durationSelect.value);

  btn.disabled = true;
  btn.textContent = "Analyzing...";
  status.textContent = "Loading";
  error.style.display = "none";

  try {
    const response = await fetch(`/api/congestion-proof?days=${days}`);
    const data: CongestionProofReport = await response.json();

    // Signal Quality Summary
    const signalMetrics = data.signal_analysis?.metrics_5g;
    updateText("cg-sinr-avg", signalMetrics?.sinr?.avg !== undefined ? `${signalMetrics.sinr.avg} dB` : "--");
    updateText("cg-rsrp-avg", signalMetrics?.rsrp?.avg !== undefined ? `${signalMetrics.rsrp.avg} dBm` : "--");

    const acceptablePct = data.signal_analysis?.acceptable_percentage;
    const acceptableEl = $("cg-acceptable-pct");
    if (acceptableEl) {
      acceptableEl.textContent = acceptablePct !== undefined ? `${acceptablePct}%` : "--%";
      acceptableEl.className = "congestion-metric-value";
      if (acceptablePct !== undefined) {
        acceptableEl.classList.add(acceptablePct >= 70 ? "good" : acceptablePct >= 50 ? "ok" : "bad");
      }
    }
    updateText("cg-signal-conclusion", data.signal_analysis?.conclusion || "--");

    // Speed vs Signal Correlation
    const speedSignal = data.speed_vs_signal;
    updateText("cg-total-tests", speedSignal?.total_tests?.toString() || "--");
    updateText("cg-good-signal-tests", speedSignal?.tests_with_acceptable_signal?.toString() || "--");

    const poorSpeedEl = $("cg-poor-speed-good-signal");
    if (poorSpeedEl) {
      const poorSpeedCount = speedSignal?.tests_with_poor_speed_despite_good_signal;
      poorSpeedEl.textContent = poorSpeedCount !== undefined ? poorSpeedCount.toString() : "--";
      poorSpeedEl.className = "congestion-metric-value";
      if (poorSpeedCount !== undefined && poorSpeedCount > 0) {
        poorSpeedEl.classList.add("bad");
      }
    }

    const correlation = speedSignal?.correlation;
    const correlationEl = $("cg-correlation");
    if (correlationEl && correlation?.r !== undefined) {
      correlationEl.textContent = `${correlation.r} (${correlation.strength || "unknown"})`;
    } else if (correlationEl) {
      correlationEl.textContent = "--";
    }

    const avgSpeed = speedSignal?.statistics?.avg_download_with_good_signal;
    updateText("cg-avg-speed", avgSpeed !== undefined ? `${avgSpeed} Mbps` : "-- Mbps");
    updateText("cg-correlation-conclusion", speedSignal?.conclusion || "--");

    // Time Patterns
    const timePeriods = data.time_patterns?.period_comparison;
    const offPeak = timePeriods?.off_peak;
    const peak = timePeriods?.peak;

    const offPeakSpeedEl = $("cg-offpeak-speed");
    if (offPeakSpeedEl) {
      offPeakSpeedEl.textContent = offPeak?.avg_speed !== undefined && offPeak.avg_speed !== null ? `${offPeak.avg_speed} Mbps` : "-- Mbps";
      offPeakSpeedEl.className = "congestion-time-speed";
      if (offPeak?.avg_speed !== undefined && offPeak.avg_speed !== null && offPeak.avg_speed > 50) {
        offPeakSpeedEl.classList.add("good");
      }
    }
    updateText("cg-offpeak-sinr", offPeak?.avg_sinr !== undefined && offPeak.avg_sinr !== null ? `SINR: ${offPeak.avg_sinr} dB` : "SINR: --");

    const peakSpeedEl = $("cg-peak-speed");
    if (peakSpeedEl) {
      peakSpeedEl.textContent = peak?.avg_speed !== undefined && peak.avg_speed !== null ? `${peak.avg_speed} Mbps` : "-- Mbps";
      peakSpeedEl.className = "congestion-time-speed";
      if (peak?.avg_speed !== undefined && peak.avg_speed !== null && peak.avg_speed < 25) {
        peakSpeedEl.classList.add("bad");
      }
    }
    updateText("cg-peak-sinr", peak?.avg_sinr !== undefined && peak.avg_sinr !== null ? `SINR: ${peak.avg_sinr} dB` : "SINR: --");

    const speedRatio = timePeriods?.speed_ratio;
    const ratioEl = $("cg-speed-ratio");
    if (ratioEl) {
      ratioEl.textContent = speedRatio !== undefined && speedRatio !== null ? `Speed Ratio: ${speedRatio}x` : "Speed Ratio: --x";
      if (speedRatio !== undefined && speedRatio !== null && speedRatio > 2) {
        ratioEl.classList.add("congestion-ratio-warning");
      }
    }
    updateText("cg-time-conclusion", data.time_patterns?.conclusion || "--");

    // Evidence Summary
    const evidenceList = $("cg-evidence-list");
    if (evidenceList) {
      evidenceList.innerHTML = "";
      const evidence = data.evidence_summary || [];
      if (evidence.length > 0) {
        evidence.forEach((item) => {
          const div = document.createElement("div");
          div.className = "congestion-evidence-item";
          div.innerHTML = `<strong>${item.claim}:</strong> ${item.data}`;
          evidenceList.appendChild(div);
        });
      } else {
        evidenceList.innerHTML = "<p>No conclusive evidence yet. More speed tests needed.</p>";
      }
    }

    // Overall Conclusion
    const conclusionEl = $("cg-overall-conclusion");
    if (conclusionEl) {
      conclusionEl.textContent = data.overall_conclusion || "--";
      conclusionEl.className = "congestion-overall-conclusion";
      if (data.overall_conclusion?.includes("CONFIRMED") || data.overall_conclusion?.includes("CONGESTION DETECTED")) {
        conclusionEl.classList.add("congestion-confirmed");
      }
    }

    content.style.display = "block";
    status.textContent = "Ready";
  } catch (err) {
    error.textContent = err instanceof Error ? err.message : "Failed to analyze congestion";
    error.style.display = "block";
    content.style.display = "none";
    status.textContent = "Error";
  } finally {
    btn.disabled = false;
    btn.textContent = "Analyze";
  }
}

function initCongestionAnalysis(): void {
  const congestionBtn = $("congestion-btn");

  if (congestionBtn) {
    congestionBtn.addEventListener("click", loadCongestionAnalysis);
  }
}

function init() {
  refresh();
  setInterval(refresh, REFRESH_INTERVAL);
  initReactComponents();
  initNetworkQuality();
  initCongestionAnalysis();
}

document.addEventListener("DOMContentLoaded", init);
