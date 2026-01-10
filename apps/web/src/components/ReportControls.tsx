/**
 * ReportControls - Diagnostic report controls using Base UI components
 *
 * Provides:
 * - Duration select dropdown
 * - Generate button
 * - Download CSV/JSON buttons
 * - Error toast for failures
 */

import { useState, useCallback } from 'react';
import { DurationSelect } from './DurationSelect';
import { Button } from './Button';
import { showErrorToast } from './Toast';

/**
 * Map duration string (24h, 7d, 30d) to hours for backend API
 */
function durationToHours(duration: string): number {
  switch (duration) {
    case '24h':
      return 24;
    case '7d':
      return 168;
    case '30d':
      return 720;
    default:
      return 24;
  }
}

interface ReportSummary {
  generated_at?: string;
  duration_hours?: number;
  health_score: {
    overall: number;
    grade: string;
    breakdown?: {
      '5g_sinr'?: number;
      '5g_rsrp'?: number;
      stability?: number;
      tower_stability?: number;
    };
  };
  signal_summary?: {
    '5g'?: {
      sinr?: { avg?: number };
      rsrp?: { avg?: number };
    };
    '4g'?: {
      sinr?: { avg?: number };
      rsrp?: { avg?: number };
    };
    sample_count?: number;
  };
  disruptions?: {
    total_disruptions?: number;
    critical_count?: number;
    events?: unknown[];
  };
  tower_history?: {
    total_changes?: number;
    unique_5g_towers?: number;
    unique_4g_towers?: number;
  };
  time_patterns?: unknown;
  error?: string;
}

interface ReportControlsProps {
  onReportLoaded?: (data: ReportSummary) => void;
}

export function ReportControls({ onReportLoaded }: ReportControlsProps) {
  const [duration, setDuration] = useState<string>('7d');
  const [isLoading, setIsLoading] = useState(false);
  const [hasReport, setHasReport] = useState(false);

  const handleDurationChange = useCallback((value: string | null) => {
    if (value) {
      setDuration(value);
    }
  }, []);

  const handleGenerate = useCallback(async () => {
    setIsLoading(true);

    try {
      const hours = durationToHours(duration);
      const response = await fetch(`/api/diagnostics?duration=${hours}`);
      const data: ReportSummary = await response.json();

      if (data.error) {
        throw new Error(data.error);
      }

      setHasReport(true);

      // Update the vanilla TS UI elements
      updateReportUI(data);

      // Notify parent if callback provided
      onReportLoaded?.(data);

    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to generate report';
      showErrorToast(message);
      setHasReport(false);
    } finally {
      setIsLoading(false);
    }
  }, [duration, onReportLoaded]);

  const handleDownload = useCallback((format: 'csv' | 'json') => {
    const hours = durationToHours(duration);
    const url = `/api/diagnostics/export/${format}?duration=${hours}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = `tmobile_diagnostic_report.${format}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }, [duration]);

  return (
    <div className="report-controls-react">
      <div className="report-controls-row">
        <DurationSelect
          value={duration}
          onChange={handleDurationChange}
          className="flex-1"
        />
        <Button
          variant="primary"
          disabled={isLoading}
          onClick={handleGenerate}
        >
          {isLoading ? 'Generating...' : 'Generate'}
        </Button>
      </div>
      {hasReport && (
        <div className="report-actions-row">
          <Button
            variant="primary"
            onClick={() => handleDownload('csv')}
          >
            Download CSV
          </Button>
          <Button
            variant="secondary"
            onClick={() => handleDownload('json')}
          >
            Export JSON
          </Button>
        </div>
      )}
    </div>
  );
}

// Helper to update vanilla TS DOM elements
function updateReportUI(data: ReportSummary): void {
  const $ = (id: string) => document.getElementById(id);

  // Update status badge
  const status = $('report-status');
  if (status) status.textContent = 'Ready';

  // Show content, hide error
  const content = $('report-content');
  const error = $('report-error');
  if (content) content.style.display = 'block';
  if (error) error.style.display = 'none';

  // Health score
  const score = data.health_score;
  const healthValue = $('health-value');
  const healthGrade = $('health-grade');
  if (healthValue) healthValue.textContent = String(score.overall);
  if (healthGrade) healthGrade.textContent = `Grade ${score.grade}`;

  // Health circle color
  const circle = $('health-circle');
  if (circle) {
    if (score.overall >= 90) {
      circle.style.borderColor = 'var(--good)';
    } else if (score.overall >= 80) {
      circle.style.borderColor = '#84cc16';
    } else if (score.overall >= 70) {
      circle.style.borderColor = 'var(--ok)';
    } else if (score.overall >= 60) {
      circle.style.borderColor = '#f97316';
    } else {
      circle.style.borderColor = 'var(--bad)';
    }
  }

  // Breakdown
  const breakdown = score.breakdown || {};
  const breakdown5g = $('breakdown-5g');
  const breakdownStability = $('breakdown-stability');
  const breakdownTower = $('breakdown-tower');
  if (breakdown5g) breakdown5g.textContent = `${breakdown['5g_sinr'] || breakdown['5g_rsrp'] || '--'}%`;
  if (breakdownStability) breakdownStability.textContent = `${breakdown.stability || '--'}%`;
  if (breakdownTower) breakdownTower.textContent = `${breakdown.tower_stability || '--'}%`;

  // Stats - use avg from signal_summary (backend returns nested stats objects)
  const sinr5g = data.signal_summary?.['5g']?.sinr?.avg;
  const statSinr = $('stat-sinr');
  if (statSinr) statSinr.textContent = sinr5g !== undefined ? `${sinr5g} dB` : '--';

  // Disruptions - backend uses total_disruptions
  const disruptions = data.disruptions?.total_disruptions || 0;
  const statDisruptions = $('stat-disruptions');
  if (statDisruptions) {
    statDisruptions.textContent = String(disruptions);
    if (disruptions > 10) {
      statDisruptions.style.color = 'var(--bad)';
    } else if (disruptions > 5) {
      statDisruptions.style.color = 'var(--ok)';
    } else {
      statDisruptions.style.color = 'var(--text)';
    }
  }

  // Tower changes - backend uses tower_history.total_changes
  const statTowerChanges = $('stat-tower-changes');
  if (statTowerChanges) statTowerChanges.textContent = String(data.tower_history?.total_changes || 0);

  // Sample count from signal_summary
  const statSamples = $('stat-speedtests');
  if (statSamples) statSamples.textContent = String(data.signal_summary?.sample_count || 0);
}

export default ReportControls;
