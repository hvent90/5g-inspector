/**
 * SpeedTestControls - Speed test scheduler and manual trigger controls
 *
 * Provides:
 * - Manual speed test trigger
 * - Scheduler start/stop controls
 * - Scheduler interval configuration
 * - Recent speed test results display
 */

import { useState, useEffect, useCallback } from 'react';
import { Button } from './Button';
import { showErrorToast, showSuccessToast } from './Toast';

interface SpeedTestResult {
  download_mbps: number | null;
  upload_mbps: number | null;
  ping_ms: number | null;
  jitter_ms: number | null;
  packet_loss_percent: number | null;
  status: string;
  error_message?: string;
  network_context?: string;
  triggered_by?: string;
  timestamp: string;
  server_name?: string;
  server_location?: string;
  tool?: string;
}

interface SchedulerConfig {
  enabled: boolean;
  interval_minutes: number;
  time_window_start?: number;
  time_window_end?: number;
  run_on_weekends?: boolean;
}

interface ToolInfo {
  available: string[];
  all_known: string[];
  preferred_order: string[];
}

// Tool metadata for display
const TOOL_METADATA: Record<string, { label: string; description: string; hasUpload: boolean }> = {
  'ookla-speedtest': { label: 'Ookla', description: 'Official Speedtest CLI', hasUpload: true },
  'speedtest-cli': { label: 'speedtest-cli', description: 'Python speedtest', hasUpload: true },
  'fast-cli': { label: 'Fast.com', description: 'Netflix CDN (download only)', hasUpload: false },
};

interface SchedulerStats {
  is_running: boolean;
  tests_completed: number;
  tests_failed: number;
  last_test_time: string | null;
  next_test_time: string | null;
  next_test_in_seconds: number | null;
  average_download_mbps: number | null;
  average_upload_mbps: number | null;
}

interface SpeedTestStatus {
  running: boolean;
  last_result: SpeedTestResult | null;
}

function getSpeedQuality(mbps: number | null): string {
  if (mbps === null) return '';
  if (mbps >= 100) return 'good';
  if (mbps >= 25) return 'ok';
  return 'bad';
}

function getPingQuality(ms: number | null): string {
  if (ms === null) return '';
  if (ms <= 30) return 'good';
  if (ms <= 60) return 'ok';
  return 'bad';
}

function formatTime(timestamp: string | null): string {
  if (!timestamp) return '--';
  const date = new Date(timestamp);
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatNextTest(seconds: number | null): string {
  if (seconds === null) return '--';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  if (mins > 0) return `${mins}m ${secs}s`;
  return `${secs}s`;
}

export function SpeedTestControls() {
  const [isTestRunning, setIsTestRunning] = useState(false);
  const [schedulerRunning, setSchedulerRunning] = useState(false);
  const [config, setConfig] = useState<SchedulerConfig | null>(null);
  const [stats, setStats] = useState<SchedulerStats | null>(null);
  const [results, setResults] = useState<SpeedTestResult[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [intervalValue, setIntervalValue] = useState(30);
  const [toolInfo, setToolInfo] = useState<ToolInfo | null>(null);
  const [selectedTool, setSelectedTool] = useState<string>('');

  // Fetch available speedtest tools
  const fetchToolInfo = useCallback(async () => {
    try {
      const response = await fetch('/api/speedtest/tools');
      if (response.ok) {
        const data: ToolInfo = await response.json();
        setToolInfo(data);
        // Default to first available tool that has upload, or first available
        if (!selectedTool && data.available.length > 0) {
          const withUpload = data.available.find(t => TOOL_METADATA[t]?.hasUpload);
          setSelectedTool(withUpload || data.available[0]);
        }
      }
    } catch {
      // Silently fail
    }
  }, [selectedTool]);

  // Fetch scheduler config and stats
  const fetchSchedulerStatus = useCallback(async () => {
    try {
      const [configRes, statsRes] = await Promise.all([
        fetch('/api/scheduler/config'),
        fetch('/api/scheduler/stats'),
      ]);

      if (configRes.ok) {
        const configData = await configRes.json();
        setConfig(configData);
        setIntervalValue(configData.interval_minutes || 30);
      }

      if (statsRes.ok) {
        const statsData = await statsRes.json();
        setStats(statsData);
        setSchedulerRunning(statsData.is_running);
      }
    } catch {
      // Silently fail on status fetch
    }
  }, []);

  // Fetch speed test status
  const fetchTestStatus = useCallback(async () => {
    try {
      const response = await fetch('/api/speedtest/status');
      if (response.ok) {
        const data: SpeedTestStatus = await response.json();
        setIsTestRunning(data.running);
      }
    } catch {
      // Silently fail
    }
  }, []);

  // Fetch recent results
  const fetchResults = useCallback(async () => {
    try {
      const response = await fetch('/api/speedtest/history?limit=5');
      if (response.ok) {
        const data = await response.json();
        setResults(data.results || data || []);
      }
    } catch {
      // Silently fail
    }
  }, []);

  // Initial fetch and polling
  useEffect(() => {
    fetchToolInfo();
    fetchSchedulerStatus();
    fetchTestStatus();
    fetchResults();

    const interval = setInterval(() => {
      fetchSchedulerStatus();
      fetchTestStatus();
      fetchResults();
    }, 5000);

    return () => clearInterval(interval);
  }, [fetchToolInfo, fetchSchedulerStatus, fetchTestStatus, fetchResults]);

  // Run manual speed test
  const handleRunTest = useCallback(async () => {
    setIsLoading(true);
    setIsTestRunning(true);

    try {
      const url = selectedTool ? `/api/speedtest?tool=${selectedTool}` : '/api/speedtest';
      const response = await fetch(url, { method: 'POST' });
      const data = await response.json();

      if (data.status === 'success') {
        const uploadInfo = data.upload_mbps ? ` / ${data.upload_mbps?.toFixed(1)} up` : '';
        showSuccessToast(`Speed test complete: ${data.download_mbps?.toFixed(1)} Mbps down${uploadInfo}`);
        fetchResults();
      } else {
        throw new Error(data.error_message || 'Speed test failed');
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Speed test failed';
      showErrorToast(message);
    } finally {
      setIsLoading(false);
      setIsTestRunning(false);
    }
  }, [fetchResults, selectedTool]);

  // Toggle scheduler
  const handleToggleScheduler = useCallback(async () => {
    const endpoint = schedulerRunning ? '/api/scheduler/stop' : '/api/scheduler/start';

    try {
      const response = await fetch(endpoint, { method: 'POST' });
      if (!response.ok) throw new Error('Failed to toggle scheduler');

      await fetchSchedulerStatus();
      showSuccessToast(schedulerRunning ? 'Scheduler stopped' : 'Scheduler started');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to toggle scheduler';
      showErrorToast(message);
    }
  }, [schedulerRunning, fetchSchedulerStatus]);

  // Update interval
  const handleUpdateInterval = useCallback(async () => {
    try {
      const response = await fetch('/api/scheduler/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ interval_minutes: intervalValue }),
      });

      if (!response.ok) throw new Error('Failed to update interval');

      await fetchSchedulerStatus();
      showSuccessToast(`Interval updated to ${intervalValue} minutes`);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update interval';
      showErrorToast(message);
    }
  }, [intervalValue, fetchSchedulerStatus]);

  const latestResult = results[0];

  return (
    <div className="speedtest-controls">
      {/* Manual Test Section */}
      <div className="speedtest-section">
        <div className="speedtest-row">
          <Button
            variant="primary"
            disabled={isLoading || isTestRunning}
            onClick={handleRunTest}
          >
            {isTestRunning ? 'Testing...' : 'Run Speed Test'}
          </Button>
          {toolInfo && toolInfo.available.length > 1 && (
            <select
              className="speedtest-tool-select"
              value={selectedTool}
              onChange={(e) => setSelectedTool(e.target.value)}
              disabled={isTestRunning}
            >
              {toolInfo.available.map((tool) => {
                const meta = TOOL_METADATA[tool];
                return (
                  <option key={tool} value={tool}>
                    {meta?.label || tool}{!meta?.hasUpload ? ' (DL only)' : ''}
                  </option>
                );
              })}
            </select>
          )}
        </div>

        {latestResult && (
          <div className="speedtest-results">
            <div className="speedtest-metrics">
              <div className="speedtest-metric">
                <span className="speedtest-metric-label">Download</span>
                <span className={`speedtest-metric-value ${getSpeedQuality(latestResult.download_mbps)}`}>
                  {latestResult.download_mbps?.toFixed(1) ?? '--'}
                </span>
                <span className="speedtest-metric-unit">Mbps</span>
              </div>
              <div className="speedtest-metric">
                <span className="speedtest-metric-label">Upload</span>
                <span className={`speedtest-metric-value ${getSpeedQuality(latestResult.upload_mbps)}`}>
                  {latestResult.upload_mbps?.toFixed(1) ?? '--'}
                </span>
                <span className="speedtest-metric-unit">Mbps</span>
              </div>
              <div className="speedtest-metric">
                <span className="speedtest-metric-label">Ping</span>
                <span className={`speedtest-metric-value ${getPingQuality(latestResult.ping_ms)}`}>
                  {latestResult.ping_ms?.toFixed(0) ?? '--'}
                </span>
                <span className="speedtest-metric-unit">ms</span>
              </div>
            </div>
            <div className="speedtest-meta">
              <span className="speedtest-time">{formatTime(latestResult.timestamp)}</span>
              {latestResult.tool && (
                <span className="speedtest-tool-badge">
                  {TOOL_METADATA[latestResult.tool]?.label || latestResult.tool}
                </span>
              )}
              {latestResult.network_context && (
                <span className={`speedtest-context speedtest-context-${latestResult.network_context}`}>
                  {latestResult.network_context}
                </span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Scheduler Section */}
      <div className="speedtest-section speedtest-scheduler">
        <div className="speedtest-scheduler-header">
          <span className="speedtest-scheduler-title">Auto Scheduler</span>
          <span className={`speedtest-scheduler-status ${schedulerRunning ? 'running' : ''}`}>
            {schedulerRunning ? 'Running' : 'Stopped'}
          </span>
        </div>

        <div className="speedtest-scheduler-controls">
          <div className="speedtest-interval-row">
            <label className="speedtest-interval-label">Interval</label>
            <select
              className="speedtest-interval-select"
              value={intervalValue}
              onChange={(e) => setIntervalValue(Number(e.target.value))}
            >
              <option value={15}>15 min</option>
              <option value={30}>30 min</option>
              <option value={60}>1 hour</option>
              <option value={120}>2 hours</option>
              <option value={240}>4 hours</option>
            </select>
            {config && intervalValue !== config.interval_minutes && (
              <Button variant="secondary" onClick={handleUpdateInterval}>
                Save
              </Button>
            )}
          </div>

          <div className="speedtest-scheduler-actions">
            <Button
              variant={schedulerRunning ? 'secondary' : 'primary'}
              onClick={handleToggleScheduler}
            >
              {schedulerRunning ? 'Stop' : 'Start'}
            </Button>
          </div>
        </div>

        {stats && schedulerRunning && (
          <div className="speedtest-scheduler-stats">
            <div className="speedtest-stat">
              <span className="speedtest-stat-label">Tests</span>
              <span className="speedtest-stat-value">{stats.tests_completed}</span>
            </div>
            <div className="speedtest-stat">
              <span className="speedtest-stat-label">Avg Down</span>
              <span className="speedtest-stat-value">
                {stats.average_download_mbps?.toFixed(0) ?? '--'} Mbps
              </span>
            </div>
            <div className="speedtest-stat">
              <span className="speedtest-stat-label">Next</span>
              <span className="speedtest-stat-value">
                {formatNextTest(stats.next_test_in_seconds)}
              </span>
            </div>
          </div>
        )}
      </div>

      {/* History Section */}
      {results.length > 1 && (
        <div className="speedtest-section speedtest-history">
          <div className="speedtest-history-title">Recent Tests</div>
          <div className="speedtest-history-list">
            {results.slice(1, 4).map((result, idx) => (
              <div key={idx} className="speedtest-history-item">
                <span className="speedtest-history-time">{formatTime(result.timestamp)}</span>
                <span className={`speedtest-history-speed ${getSpeedQuality(result.download_mbps)}`}>
                  {result.download_mbps?.toFixed(0) ?? '--'} / {result.upload_mbps?.toFixed(0) ?? '--'} Mbps
                </span>
                <span className="speedtest-history-ping">{result.ping_ms?.toFixed(0) ?? '--'} ms</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default SpeedTestControls;
