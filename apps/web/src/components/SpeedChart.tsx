/**
 * SpeedChart - Retro ASCII-style speed test history visualization
 *
 * Features:
 * - Bar chart showing download/upload speeds over time
 * - Summary statistics (avg, min, max)
 * - Time labels with Windows 95 aesthetic
 */

import { useState, useEffect, useCallback } from 'react';

interface SpeedTestResult {
  download_mbps: number | null;
  upload_mbps: number | null;
  ping_ms: number | null;
  timestamp: string;
  tool?: string;
  status: string;
}

// ASCII block characters for vertical bars
const BAR_CHARS = [' ', '\u2581', '\u2582', '\u2583', '\u2584', '\u2585', '\u2586', '\u2587', '\u2588'];

function valueToBarHeight(value: number, max: number, levels: number = 8): number {
  if (value <= 0 || max <= 0) return 0;
  return Math.min(levels, Math.ceil((value / max) * levels));
}

function getSpeedQuality(mbps: number | null): string {
  if (mbps === null) return '';
  if (mbps >= 100) return 'good';
  if (mbps >= 25) return 'ok';
  return 'bad';
}

function formatTime(timestamp: string): string {
  const date = new Date(timestamp);
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

interface VerticalBarProps {
  value: number;
  max: number;
  label?: string;
  quality: string;
  height?: number;
}

function VerticalBar({ value, max, label, quality, height = 8 }: VerticalBarProps) {
  const barHeight = valueToBarHeight(value, max, height);

  return (
    <div className="vbar-container">
      <div className="vbar-stack" style={{ height: `${height * 6}px` }}>
        {Array.from({ length: height }, (_, i) => {
          const level = height - i;
          const isActive = level <= barHeight;
          return (
            <div
              key={i}
              className={`vbar-segment ${isActive ? `active ${quality}` : ''}`}
            />
          );
        })}
      </div>
      {label && <div className="vbar-label">{label}</div>}
    </div>
  );
}

interface StatBoxProps {
  label: string;
  value: string;
  unit?: string;
  quality?: string;
}

function StatBox({ label, value, unit, quality }: StatBoxProps) {
  return (
    <div className="stat-box">
      <div className="stat-box-label">{label}</div>
      <div className={`stat-box-value ${quality || ''}`}>
        {value}
        {unit && <span className="stat-box-unit">{unit}</span>}
      </div>
    </div>
  );
}

export function SpeedChart() {
  const [results, setResults] = useState<SpeedTestResult[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const fetchResults = useCallback(async () => {
    try {
      const response = await fetch('/api/speedtest/history?limit=12');
      if (response.ok) {
        const data = await response.json();
        const validResults = (data.results || data || []).filter(
          (r: SpeedTestResult) => r.status === 'success' && r.download_mbps !== null
        );
        setResults(validResults);
      }
    } catch {
      // Silently fail
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchResults();
    const interval = setInterval(fetchResults, 30000);
    return () => clearInterval(interval);
  }, [fetchResults]);

  // Calculate statistics
  const downloads = results.map(r => r.download_mbps ?? 0).filter(v => v > 0);
  const uploads = results.map(r => r.upload_mbps ?? 0).filter(v => v > 0);
  const pings = results.map(r => r.ping_ms ?? 0).filter(v => v > 0);

  const avgDownload = downloads.length > 0 ? downloads.reduce((a, b) => a + b, 0) / downloads.length : 0;
  const maxDownload = downloads.length > 0 ? Math.max(...downloads) : 0;
  const minDownload = downloads.length > 0 ? Math.min(...downloads) : 0;

  const avgUpload = uploads.length > 0 ? uploads.reduce((a, b) => a + b, 0) / uploads.length : 0;
  const avgPing = pings.length > 0 ? pings.reduce((a, b) => a + b, 0) / pings.length : 0;

  // Chart max (round up to nice number)
  const chartMax = Math.ceil(maxDownload / 50) * 50 || 200;

  // Reverse for display (newest on right)
  const displayResults = [...results].reverse().slice(-10);

  if (isLoading) {
    return (
      <div className="speed-chart loading">
        <div className="speed-chart-loading">Loading speed data...</div>
      </div>
    );
  }

  if (results.length === 0) {
    return (
      <div className="speed-chart empty">
        <div className="speed-chart-empty">
          <div className="empty-icon">\u23F1</div>
          <div className="empty-text">No speed tests yet</div>
          <div className="empty-hint">Run a speed test to see data here</div>
        </div>
      </div>
    );
  }

  return (
    <div className="speed-chart">
      {/* Summary Stats Row */}
      <div className="speed-chart-stats">
        <StatBox
          label="Avg Down"
          value={avgDownload.toFixed(0)}
          unit="Mbps"
          quality={getSpeedQuality(avgDownload)}
        />
        <StatBox
          label="Avg Up"
          value={avgUpload.toFixed(0)}
          unit="Mbps"
          quality={getSpeedQuality(avgUpload)}
        />
        <StatBox
          label="Avg Ping"
          value={avgPing.toFixed(0)}
          unit="ms"
          quality={avgPing <= 30 ? 'good' : avgPing <= 60 ? 'ok' : 'bad'}
        />
        <StatBox
          label="Tests"
          value={results.length.toString()}
        />
      </div>

      {/* Min/Max Row */}
      <div className="speed-chart-minmax">
        <span className="minmax-item">
          <span className="minmax-label">Peak:</span>
          <span className="minmax-value good">{maxDownload.toFixed(0)} Mbps</span>
        </span>
        <span className="minmax-item">
          <span className="minmax-label">Low:</span>
          <span className="minmax-value bad">{minDownload.toFixed(0)} Mbps</span>
        </span>
      </div>

      {/* Vertical Bar Chart */}
      <div className="speed-chart-bars">
        <div className="chart-y-axis">
          <span className="y-label">{chartMax}</span>
          <span className="y-label">{chartMax / 2}</span>
          <span className="y-label">0</span>
        </div>
        <div className="chart-bars-area">
          {displayResults.map((result, idx) => (
            <div key={idx} className="chart-bar-group">
              <VerticalBar
                value={result.download_mbps ?? 0}
                max={chartMax}
                quality={getSpeedQuality(result.download_mbps)}
                height={8}
              />
              <div className="chart-bar-time">{formatTime(result.timestamp)}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Legend */}
      <div className="speed-chart-legend">
        <span className="legend-item">
          <span className="legend-color download"></span>
          Download (Mbps)
        </span>
      </div>

      {/* ASCII Sparkline Summary */}
      <div className="speed-chart-sparkline">
        <span className="sparkline-label">Trend:</span>
        <span className="sparkline-chars">
          {displayResults.map((r, i) => {
            const pct = ((r.download_mbps ?? 0) / chartMax) * 100;
            const charIdx = Math.min(8, Math.max(0, Math.floor(pct / 12.5)));
            const quality = getSpeedQuality(r.download_mbps);
            return (
              <span key={i} className={`spark-char ${quality}`}>
                {BAR_CHARS[charIdx]}
              </span>
            );
          })}
        </span>
      </div>
    </div>
  );
}

export default SpeedChart;
