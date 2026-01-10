// Signal metrics interface
interface SignalMetrics {
  sinr: number | null;
  rsrp: number | null;
  rsrq: number | null;
  rssi: number | null;
  bands: string[];
}

// API response types
interface SignalData {
  signal: {
    "5g": SignalMetrics & { gNBID?: number; cid?: number };
    "4g": SignalMetrics & { eNBID?: number; cid?: number };
    generic: { registration?: string };
  };
  device: { name?: string; softwareVersion?: string };
  time: { upTime?: number };
}

// Signal quality thresholds
const THRESHOLDS = {
  sinr: { good: 20, ok: 10 },
  rsrp: { good: -80, ok: -100 },
};

function getQuality(type: "sinr" | "rsrp", val: number | null): string {
  if (val === null) return "unknown";
  const t = THRESHOLDS[type];
  return val >= t.good ? "good" : val >= t.ok ? "ok" : "bad";
}

async function fetchSignal(): Promise<SignalData | null> {
  try {
    const response = await fetch("/api/signal");
    if (!response.ok) throw new Error("Fetch failed");
    return await response.json();
  } catch (error) {
    console.error("Failed to fetch signal:", error);
    return null;
  }
}

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export type { SignalData, SignalMetrics };
export { fetchSignal, getQuality, formatUptime };
