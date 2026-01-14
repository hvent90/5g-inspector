// Shared utilities and types for netpulse
// Add shared code here that can be used across apps

export const VERSION = "1.0.0";

export interface SignalData {
  rsrp: number;
  rsrq: number;
  sinr: number;
  timestamp: string;
}

export interface SpeedTestResult {
  download: number;
  upload: number;
  ping: number;
  timestamp: string;
}

export function formatSignalStrength(rsrp: number): string {
  if (rsrp >= -80) return "Excellent";
  if (rsrp >= -90) return "Good";
  if (rsrp >= -100) return "Fair";
  return "Poor";
}

export function formatSpeed(mbps: number): string {
  if (mbps >= 1000) {
    return `${(mbps / 1000).toFixed(2)} Gbps`;
  }
  return `${mbps.toFixed(2)} Mbps`;
}
