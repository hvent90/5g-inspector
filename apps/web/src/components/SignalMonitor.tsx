/**
 * SignalMonitor - Real-time signal visualization with line charts
 *
 * Features:
 * - Live signal strength metrics (5G/LTE)
 * - Reusable MetricChart component
 * - Connection status indicators
 */

import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
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
  nr_rsrq: number | null;
  nr_rssi: number | null;
  lte_sinr: number | null;
  lte_rsrp: number | null;
  lte_rsrq: number | null;
  lte_rssi: number | null;
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

// ============================================================================
// MetricChart - Reusable chart component
// ============================================================================

interface MetricLine {
  dataKey: string;
  name: string;
  color: string;
  active: boolean;
}

interface ThresholdLine {
  value: number;
  color: string;
  label: string;
}

interface MetricChartProps {
  title: string;
  unit: string;
  data: HistoryPoint[];
  lines: MetricLine[];
  thresholds?: ThresholdLine[];
  timeRange: TimeRangeSeconds;
  onTimeRangeChange: (range: TimeRangeSeconds) => void;
  yDomainPadding?: number;
}

// Max points to render in chart (performance limit)
const MAX_CHART_POINTS = 300;

// Downsample data for chart rendering - take every Nth point
function downsampleForChart(data: HistoryPoint[], maxPoints: number): HistoryPoint[] {
  if (data.length <= maxPoints) return data;
  const step = Math.ceil(data.length / maxPoints);
  const result: HistoryPoint[] = [];
  for (let i = 0; i < data.length; i += step) {
    result.push(data[i]);
  }
  // Always include the last point for accurate "current" value
  if (result.length > 0 && data.length > 0 && result[result.length - 1] !== data[data.length - 1]) {
    result.push(data[data.length - 1]);
  }
  return result;
}

function MetricChart({
  title,
  unit,
  data,
  lines,
  thresholds = [],
  timeRange,
  onTimeRangeChange,
  yDomainPadding = 5,
}: MetricChartProps) {
  // Memoize filtered and downsampled data - only recalculate when data or timeRange changes
  const { chartData, xDomain } = useMemo(() => {
    const now = Date.now();
    const cutoff = now - timeRange * 1000;
    const filtered = data.filter(point => point.timestamp >= cutoff);
    const downsampled = downsampleForChart(filtered, MAX_CHART_POINTS);
    return {
      chartData: downsampled,
      xDomain: [cutoff, now] as [number, number],
    };
  }, [data, timeRange]);

  // Get active lines (memoized)
  const activeLines = useMemo(() => lines.filter(l => l.active), [lines]);

  // Calculate Y-axis domain based on actual data (memoized)
  const { yMin, yMax } = useMemo(() => {
    const allValues = chartData.flatMap(d =>
      activeLines.map(line => d[line.dataKey as keyof HistoryPoint] as number | null)
    ).filter((v): v is number => v !== null);

    const minVal = allValues.length > 0 ? Math.min(...allValues) : -10;
    const maxVal = allValues.length > 0 ? Math.max(...allValues) : 30;
    return {
      yMin: Math.floor(minVal / yDomainPadding) * yDomainPadding - yDomainPadding,
      yMax: Math.ceil(maxVal / yDomainPadding) * yDomainPadding + yDomainPadding,
    };
  }, [chartData, activeLines, yDomainPadding]);

  return (
    <div className="signal-chart-container">
      <div className="signal-chart-header">
        <span className="signal-chart-title">{title} ({unit})</span>
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
            {lines.map(line => (
              line.active ? (
                <span key={line.dataKey} className="legend-item-dynamic" style={{ '--line-color': line.color } as React.CSSProperties}>
                  {line.name}
                </span>
              ) : null
            ))}
          </div>
        </div>
      </div>
      {chartData.length === 0 ? (
        <div className="signal-chart-empty-inline">
          <span>Collecting data...</span>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={160}>
          <LineChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 10 }}>
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
              width={40}
              padding={{ top: 10, bottom: 10 }}
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
              formatter={(value, name) => {
                const line = lines.find(l => l.dataKey === name);
                return [
                  value !== undefined ? `${value} ${unit}` : 'N/A',
                  line?.name || name
                ];
              }}
            />
            {/* Reference lines for thresholds */}
            {thresholds.map(threshold => (
              <ReferenceLine
                key={threshold.value}
                y={threshold.value}
                stroke={threshold.color}
                strokeDasharray="3 3"
                opacity={0.5}
              />
            ))}
            {/* Data lines */}
            {activeLines.map(line => (
              <Line
                key={line.dataKey}
                type="monotone"
                dataKey={line.dataKey}
                stroke={line.color}
                strokeWidth={2}
                dot={false}
                connectNulls
                name={line.dataKey}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
      {thresholds.length > 0 && (
        <div className="signal-chart-thresholds">
          {thresholds.map(threshold => (
            <span key={threshold.value} className="threshold" style={{ borderColor: threshold.color, color: threshold.color }}>
              {threshold.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// SignalMonitor - Main component
// ============================================================================

// Max history to keep in memory (1 hour at 1s refresh = 3600 points)
const MAX_HISTORY_POINTS = 3600;

// Throttle interval for state updates (ms) - update UI every 2 seconds instead of every second
const HISTORY_UPDATE_INTERVAL = 2000;

export function SignalMonitor() {
  const [currentSignal, setCurrentSignal] = useState<SignalData | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [timeRange, setTimeRange] = useState<TimeRangeSeconds>(60);
  const historyRef = useRef<HistoryPoint[]>([]);
  const lastHistoryUpdateRef = useRef<number>(0);

  // Fetch current signal
  const fetchSignal = useCallback(async () => {
    try {
      const response = await fetch('/api/signal');
      if (response.ok) {
        const data: SignalData = await response.json();
        setCurrentSignal(data);
        setIsConnected(true);

        // Add to history with all metrics
        const now = Date.now();
        const point: HistoryPoint = {
          timestamp: now,
          nr_sinr: data.nr?.sinr ?? null,
          nr_rsrp: data.nr?.rsrp ?? null,
          nr_rsrq: data.nr?.rsrq ?? null,
          nr_rssi: data.nr?.rssi ?? null,
          lte_sinr: data.lte?.sinr ?? null,
          lte_rsrp: data.lte?.rsrp ?? null,
          lte_rsrq: data.lte?.rsrq ?? null,
          lte_rssi: data.lte?.rssi ?? null,
        };

        // Mutate ref directly (no array copy) - keep last N points
        if (historyRef.current.length >= MAX_HISTORY_POINTS) {
          historyRef.current.shift(); // Remove oldest
        }
        historyRef.current.push(point);

        // Throttle state updates to reduce re-renders
        if (now - lastHistoryUpdateRef.current >= HISTORY_UPDATE_INTERVAL) {
          lastHistoryUpdateRef.current = now;
          // Create new array reference only when we actually update state
          setHistory([...historyRef.current]);
        }
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

  // Chart configurations - memoized to prevent re-creates
  const sinrRsrqLines = useMemo<MetricLine[]>(() => [
    { dataKey: 'nr_sinr', name: '5G SINR', color: '#000080', active: has5g },
    { dataKey: 'nr_rsrq', name: '5G RSRQ', color: '#4040c0', active: has5g },
    { dataKey: 'lte_sinr', name: '4G SINR', color: '#008000', active: has4g },
    { dataKey: 'lte_rsrq', name: '4G RSRQ', color: '#40c040', active: has4g },
  ], [has5g, has4g]);

  const sinrThresholds = useMemo<ThresholdLine[]>(() => [
    { value: 20, color: '#008000', label: '20+ Excellent' },
    { value: 10, color: '#808000', label: '10-20 Good' },
    { value: 0, color: '#800000', label: '<10 Poor' },
  ], []);

  const rsrpRssiLines = useMemo<MetricLine[]>(() => [
    { dataKey: 'nr_rsrp', name: '5G RSRP', color: '#800080', active: has5g },
    { dataKey: 'nr_rssi', name: '5G RSSI', color: '#c040c0', active: has5g },
    { dataKey: 'lte_rsrp', name: '4G RSRP', color: '#808000', active: has4g },
    { dataKey: 'lte_rssi', name: '4G RSSI', color: '#c0c040', active: has4g },
  ], [has5g, has4g]);

  const rsrpThresholds = useMemo<ThresholdLine[]>(() => [
    { value: -80, color: '#008000', label: '-80+ Excellent' },
    { value: -100, color: '#808000', label: '-100 to -80 Good' },
    { value: -110, color: '#800000', label: '<-110 Poor' },
  ], []);

  return (
    <div className="signal-monitor signal-monitor-horizontal">
      {/* Left Sidebar: Status + Metrics */}
      <div className="signal-monitor-sidebar">
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
        <div className="signal-metrics-section signal-metrics-compact">
          <div className="signal-metrics-header">5G NR</div>
          {has5g ? (
            <div className="signal-metrics-grid-compact">
              <div className="signal-metric">
                <span className="signal-metric-label">SINR</span>
                <span className={`signal-metric-value ${getQualityClass(nrSinrBars)}`}>{nr?.sinr}</span>
                <span className="signal-metric-unit">dB</span>
              </div>
              <div className="signal-metric">
                <span className="signal-metric-label">RSRP</span>
                <span className={`signal-metric-value ${getQualityClass(nrRsrpBars)}`}>{nr?.rsrp}</span>
                <span className="signal-metric-unit">dBm</span>
              </div>
              <div className="signal-metric">
                <span className="signal-metric-label">RSRQ</span>
                <span className="signal-metric-value">{nr?.rsrq}</span>
                <span className="signal-metric-unit">dB</span>
              </div>
              <div className="signal-metric">
                <span className="signal-metric-label">RSSI</span>
                <span className="signal-metric-value">{nr?.rssi}</span>
                <span className="signal-metric-unit">dBm</span>
              </div>
            </div>
          ) : (
            <div className="signal-metrics-inactive">No 5G signal</div>
          )}
        </div>

        {/* Primary Metrics - 4G */}
        <div className={`signal-metrics-section signal-metrics-compact ${!has4g ? 'inactive' : ''}`}>
          <div className="signal-metrics-header">4G LTE {is5gSaMode && <span className="signal-metrics-note">(SA mode)</span>}</div>
          {has4g ? (
            <div className="signal-metrics-grid-compact">
              <div className="signal-metric">
                <span className="signal-metric-label">SINR</span>
                <span className={`signal-metric-value ${getQualityClass(lteSinrBars)}`}>{lte?.sinr}</span>
                <span className="signal-metric-unit">dB</span>
              </div>
              <div className="signal-metric">
                <span className="signal-metric-label">RSRP</span>
                <span className={`signal-metric-value ${getQualityClass(lteRsrpBars)}`}>{lte?.rsrp}</span>
                <span className="signal-metric-unit">dBm</span>
              </div>
              <div className="signal-metric">
                <span className="signal-metric-label">RSRQ</span>
                <span className="signal-metric-value">{lte?.rsrq}</span>
                <span className="signal-metric-unit">dB</span>
              </div>
              <div className="signal-metric">
                <span className="signal-metric-label">RSSI</span>
                <span className="signal-metric-value">{lte?.rssi}</span>
                <span className="signal-metric-unit">dBm</span>
              </div>
            </div>
          ) : (
            <div className="signal-metrics-inactive">{is5gSaMode ? '5G SA - No LTE' : 'No LTE signal'}</div>
          )}
        </div>
      </div>

      {/* Right: Charts Side-by-Side */}
      <div className="signal-monitor-charts">
        {/* SINR + RSRQ Chart */}
        <MetricChart
          title="SINR / RSRQ"
          unit="dB"
          data={history}
          lines={sinrRsrqLines}
          thresholds={sinrThresholds}
          timeRange={timeRange}
          onTimeRangeChange={setTimeRange}
        />

        {/* RSRP + RSSI Chart */}
        <MetricChart
          title="RSRP / RSSI"
          unit="dBm"
          data={history}
          lines={rsrpRssiLines}
          thresholds={rsrpThresholds}
          timeRange={timeRange}
          onTimeRangeChange={setTimeRange}
          yDomainPadding={10}
        />
      </div>
    </div>
  );
}

export default SignalMonitor;
