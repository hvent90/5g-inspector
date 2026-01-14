/**
 * SignalMonitor - Real-time signal visualization with line charts
 *
 * Features:
 * - Live signal strength metrics (5G/LTE)
 * - Line charts with proper X/Y axis labels
 * - Connection status indicators
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';

interface SignalMetrics {
  sinr: number | null;
  rsrp: number | null;
  rsrq: number | null;
  rssi: number | null;
  bands: string[];
  quality?: string;
}

interface SignalData {
  timestamp: string;
  nr?: SignalMetrics;
  lte?: SignalMetrics;
  connection_mode?: string;
}

interface HistoryPoint {
  timestamp: number;
  nr_sinr: number | null;
  nr_rsrp: number | null;
  lte_sinr: number | null;
  lte_rsrp: number | null;
}

// Map SINR to signal bars (0-5)
function sinrToBars(sinr: number | null): number {
  if (sinr === null) return 0;
  if (sinr >= 20) return 5;
  if (sinr >= 13) return 4;
  if (sinr >= 10) return 3;
  if (sinr >= 5) return 2;
  if (sinr >= 0) return 1;
  return 0;
}

// Map RSRP to signal bars (0-5)
function rsrpToBars(rsrp: number | null): number {
  if (rsrp === null) return 0;
  if (rsrp >= -80) return 5;
  if (rsrp >= -90) return 4;
  if (rsrp >= -100) return 3;
  if (rsrp >= -110) return 2;
  if (rsrp >= -120) return 1;
  return 0;
}

// Map value to quality class
function getQualityClass(bars: number): string {
  if (bars >= 4) return 'good';
  if (bars >= 2) return 'ok';
  return 'bad';
}

// Check if signal metrics have actual data
function hasSignalData(metrics: SignalMetrics | undefined): boolean {
  if (!metrics) return false;
  return metrics.sinr !== null || metrics.rsrp !== null;
}

// Format connection mode for display
function formatConnectionMode(mode: string | undefined): { label: string; description: string } {
  switch (mode) {
    case 'SA':
      return { label: '5G SA', description: '5G Standalone' };
    case 'NSA':
      return { label: '5G NSA', description: '5G Non-Standalone (LTE anchor)' };
    case 'LTE':
      return { label: 'LTE', description: 'LTE only' };
    case 'No Signal':
      return { label: 'NO SIGNAL', description: 'No cellular connection' };
    default:
      return { label: mode || 'CONNECTED', description: '' };
  }
}

// Format timestamp for X-axis
function formatTime(timestamp: number): string {
  const date = new Date(timestamp);
  return date.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// Chart data point for recharts
interface ChartDataPoint {
  timestamp: number;
  time: string;
  nr_sinr: number | null;
  lte_sinr: number | null;
}

// Time range options in seconds
const TIME_RANGES = [
  { label: '1m', seconds: 60 },
  { label: '5m', seconds: 300 },
  { label: '10m', seconds: 600 },
  { label: '30m', seconds: 1800 },
  { label: '1h', seconds: 3600 },
  { label: '12h', seconds: 43200 },
  { label: '24h', seconds: 86400 },
  { label: '7d', seconds: 604800 },
  { label: '30d', seconds: 2592000 },
] as const;

type TimeRangeSeconds = typeof TIME_RANGES[number]['seconds'];

interface SignalChartProps {
  data: HistoryPoint[];
  has5g: boolean;
  has4g: boolean;
  is5gSaMode: boolean;
  timeRange: TimeRangeSeconds;
  onTimeRangeChange: (range: TimeRangeSeconds) => void;
}

// Format timestamp for X-axis based on time range
function formatTimeForRange(timestamp: number, rangeSeconds: number): string {
  const date = new Date(timestamp);
  if (rangeSeconds <= 600) {
    // Up to 10 min: show HH:MM:SS
    return date.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } else if (rangeSeconds <= 86400) {
    // Up to 24h: show HH:MM
    return date.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' });
  } else {
    // Longer: show MM/DD HH:MM
    return `${date.getMonth() + 1}/${date.getDate()} ${date.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' })}`;
  }
}

function SignalChart({ data, has5g, has4g, is5gSaMode, timeRange, onTimeRangeChange }: SignalChartProps) {
  // Filter data based on time range
  const now = Date.now();
  const cutoff = now - timeRange * 1000;
  const filteredData = data.filter(point => point.timestamp >= cutoff);

  // Transform data for recharts - use timestamp as X value for proper domain
  const chartData: ChartDataPoint[] = filteredData.map(point => ({
    timestamp: point.timestamp,
    time: formatTimeForRange(point.timestamp, timeRange),
    nr_sinr: point.nr_sinr,
    lte_sinr: point.lte_sinr,
  }));

  // Calculate Y-axis domain based on actual data
  const allValues = chartData.flatMap(d => [d.nr_sinr, d.lte_sinr]).filter((v): v is number => v !== null);
  const minVal = allValues.length > 0 ? Math.min(...allValues) : -10;
  const maxVal = allValues.length > 0 ? Math.max(...allValues) : 30;
  const yMin = Math.floor(minVal / 5) * 5 - 5;
  const yMax = Math.ceil(maxVal / 5) * 5 + 5;

  // X-axis domain: full time range from cutoff to now
  const xDomain: [number, number] = [cutoff, now];

  return (
    <div className="signal-chart-container">
      <div className="signal-chart-header">
        <span className="signal-chart-title">SINR History (dB)</span>
        <div className="signal-chart-controls">
          <div className="time-range-selector">
            {TIME_RANGES.map(({ label, seconds }) => (
              <button
                key={seconds}
                className={`time-range-btn ${timeRange === seconds ? 'active' : ''}`}
                onClick={() => onTimeRangeChange(seconds)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="signal-chart-legend">
            {has5g && <span className="legend-5g">5G NR</span>}
            {has4g && <span className="legend-4g">4G LTE</span>}
            {is5gSaMode && !has4g && <span className="legend-inactive">4G: SA Mode</span>}
          </div>
        </div>
      </div>
      {chartData.length === 0 ? (
        <div className="signal-chart-empty-inline">
          <span>Collecting data...</span>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={160}>
          <LineChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#808080" opacity={0.3} />
            <XAxis
              dataKey="timestamp"
              type="number"
              domain={xDomain}
              scale="time"
              tick={{ fontSize: 9, fill: '#404040' }}
              tickLine={{ stroke: '#808080' }}
              axisLine={{ stroke: '#808080' }}
              tickFormatter={(ts) => formatTimeForRange(ts, timeRange)}
              minTickGap={50}
            />
            <YAxis
              domain={[yMin, yMax]}
              tick={{ fontSize: 9, fill: '#404040' }}
              tickLine={{ stroke: '#808080' }}
              axisLine={{ stroke: '#808080' }}
              tickFormatter={(value) => `${value}`}
              width={35}
            />
            <Tooltip
              contentStyle={{
                background: '#c0c0c0',
                border: '2px solid',
                borderColor: '#fff #808080 #808080 #fff',
                fontSize: '10px',
                padding: '4px 8px',
              }}
              labelStyle={{ fontWeight: 600, marginBottom: '4px' }}
              labelFormatter={(ts) => new Date(ts as number).toLocaleString()}
              formatter={(value, name) => [
                value !== undefined ? `${value} dB` : 'N/A',
                name === 'nr_sinr' ? '5G SINR' : '4G SINR'
              ]}
            />
            {/* Reference lines for signal quality thresholds */}
            <ReferenceLine y={20} stroke="#008000" strokeDasharray="3 3" opacity={0.5} />
            <ReferenceLine y={10} stroke="#808000" strokeDasharray="3 3" opacity={0.5} />
            <ReferenceLine y={0} stroke="#800000" strokeDasharray="3 3" opacity={0.5} />
            {has5g && (
              <Line
                type="monotone"
                dataKey="nr_sinr"
                stroke="#000080"
                strokeWidth={2}
                dot={false}
                connectNulls
                name="nr_sinr"
                isAnimationActive={false}
              />
            )}
            {has4g && (
              <Line
                type="monotone"
                dataKey="lte_sinr"
                stroke="#008000"
                strokeWidth={2}
                dot={false}
                connectNulls
                name="lte_sinr"
                isAnimationActive={false}
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      )}
      <div className="signal-chart-thresholds">
        <span className="threshold good">20+ Excellent</span>
        <span className="threshold ok">10-20 Good</span>
        <span className="threshold bad">&lt;10 Poor</span>
      </div>
    </div>
  );
}

// Max history to keep (7 days at 1s refresh = 604800 points)
// Note: For very long sessions, memory usage will grow
const MAX_HISTORY_POINTS = 604800;

export function SignalMonitor() {
  const [currentSignal, setCurrentSignal] = useState<SignalData | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [timeRange, setTimeRange] = useState<TimeRangeSeconds>(60);
  const historyRef = useRef<HistoryPoint[]>([]);

  // Fetch current signal
  const fetchSignal = useCallback(async () => {
    try {
      const response = await fetch('/api/signal');
      if (response.ok) {
        const data: SignalData = await response.json();
        setCurrentSignal(data);
        setIsConnected(true);

        // Add to history (keep up to 1 hour of data)
        const point: HistoryPoint = {
          timestamp: Date.now(),
          nr_sinr: data.nr?.sinr ?? null,
          nr_rsrp: data.nr?.rsrp ?? null,
          lte_sinr: data.lte?.sinr ?? null,
          lte_rsrp: data.lte?.rsrp ?? null,
        };
        historyRef.current = [...historyRef.current.slice(-(MAX_HISTORY_POINTS - 1)), point];
        setHistory(historyRef.current);
      } else {
        setIsConnected(false);
      }
    } catch {
      setIsConnected(false);
    }
  }, []);

  // Poll for updates
  useEffect(() => {
    fetchSignal();
    const interval = setInterval(fetchSignal, 1000);
    return () => clearInterval(interval);
  }, [fetchSignal]);

  const nr = currentSignal?.nr;
  const lte = currentSignal?.lte;
  const connectionMode = currentSignal?.connection_mode;
  const modeInfo = formatConnectionMode(connectionMode);

  // Determine if 4G is inactive (SA mode = 5G only, no LTE)
  const has5g = hasSignalData(nr);
  const has4g = hasSignalData(lte);
  const is5gSaMode = has5g && !has4g;

  const nrSinrBars = sinrToBars(nr?.sinr ?? null);
  const nrRsrpBars = rsrpToBars(nr?.rsrp ?? null);
  const lteSinrBars = sinrToBars(lte?.sinr ?? null);
  const lteRsrpBars = rsrpToBars(lte?.rsrp ?? null);

  return (
    <div className="signal-monitor">
      {/* Connection Status */}
      <div className="signal-status-row">
        <div className={`signal-status-indicator ${isConnected ? 'connected' : ''}`}>
          {isConnected ? '\u25CF' : '\u25CB'}
        </div>
        <span className={`signal-status-text ${connectionMode === 'SA' ? 'mode-sa' : connectionMode === 'NSA' ? 'mode-nsa' : ''}`}>
          {isConnected ? modeInfo.label : 'Disconnected'}
        </span>
        {modeInfo.description && (
          <span className="signal-mode-desc" title={modeInfo.description}>
            {is5gSaMode ? '(No LTE anchor)' : ''}
          </span>
        )}
        {nr?.bands && nr.bands.length > 0 && (
          <span className="signal-bands">{nr.bands.join(', ').toUpperCase()}</span>
        )}
      </div>

      {/* Primary Metrics - 5G */}
      <div className="signal-metrics-section">
        <div className="signal-metrics-header">5G NR</div>
        {has5g ? (
          <div className="signal-metrics-grid">
            <div className="signal-metric">
              <span className="signal-metric-label">SINR</span>
              <span className={`signal-metric-value ${getQualityClass(nrSinrBars)}`}>{nr?.sinr}</span>
              <span className="signal-metric-unit">dB</span>
              <span className="signal-metric-desc">Signal quality</span>
            </div>
            <div className="signal-metric">
              <span className="signal-metric-label">RSRP</span>
              <span className={`signal-metric-value ${getQualityClass(nrRsrpBars)}`}>{nr?.rsrp}</span>
              <span className="signal-metric-unit">dBm</span>
              <span className="signal-metric-desc">Signal power</span>
            </div>
            <div className="signal-metric">
              <span className="signal-metric-label">RSRQ</span>
              <span className="signal-metric-value">{nr?.rsrq}</span>
              <span className="signal-metric-unit">dB</span>
              <span className="signal-metric-desc">Signal quality ratio</span>
            </div>
            <div className="signal-metric">
              <span className="signal-metric-label">RSSI</span>
              <span className="signal-metric-value">{nr?.rssi}</span>
              <span className="signal-metric-unit">dBm</span>
              <span className="signal-metric-desc">Total received power</span>
            </div>
          </div>
        ) : (
          <div className="signal-metrics-inactive">No 5G signal</div>
        )}
      </div>

      {/* Primary Metrics - 4G */}
      <div className={`signal-metrics-section ${!has4g ? 'inactive' : ''}`}>
        <div className="signal-metrics-header">4G LTE {is5gSaMode && <span className="signal-metrics-note">(Not used in SA mode)</span>}</div>
        {has4g ? (
          <div className="signal-metrics-grid">
            <div className="signal-metric">
              <span className="signal-metric-label">SINR</span>
              <span className={`signal-metric-value ${getQualityClass(lteSinrBars)}`}>{lte?.sinr}</span>
              <span className="signal-metric-unit">dB</span>
              <span className="signal-metric-desc">Signal quality</span>
            </div>
            <div className="signal-metric">
              <span className="signal-metric-label">RSRP</span>
              <span className={`signal-metric-value ${getQualityClass(lteRsrpBars)}`}>{lte?.rsrp}</span>
              <span className="signal-metric-unit">dBm</span>
              <span className="signal-metric-desc">Signal power</span>
            </div>
            <div className="signal-metric">
              <span className="signal-metric-label">RSRQ</span>
              <span className="signal-metric-value">{lte?.rsrq}</span>
              <span className="signal-metric-unit">dB</span>
              <span className="signal-metric-desc">Signal quality ratio</span>
            </div>
            <div className="signal-metric">
              <span className="signal-metric-label">RSSI</span>
              <span className="signal-metric-value">{lte?.rssi}</span>
              <span className="signal-metric-unit">dBm</span>
              <span className="signal-metric-desc">Total received power</span>
            </div>
          </div>
        ) : (
          <div className="signal-metrics-inactive">{is5gSaMode ? '5G Standalone - no LTE anchor' : 'No LTE signal'}</div>
        )}
      </div>

      {/* SINR Trend Chart */}
      <SignalChart
        data={history}
        has5g={has5g}
        has4g={has4g}
        is5gSaMode={is5gSaMode}
        timeRange={timeRange}
        onTimeRangeChange={setTimeRange}
      />

    </div>
  );
}

export default SignalMonitor;
