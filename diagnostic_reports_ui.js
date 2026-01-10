// Diagnostic Reports UI Functions for T-Mobile Dashboard
// This module provides UI functions for generating and exporting diagnostic reports

async function loadReportSummary() {
  const btn = document.getElementById('report-btn');
  const status = document.getElementById('report-status');
  const content = document.getElementById('report-content');
  const error = document.getElementById('report-error');
  const duration = document.getElementById('report-duration').value;

  btn.disabled = true;
  btn.textContent = 'Generating...';
  status.textContent = 'Loading';
  error.style.display = 'none';

  try {
    const response = await fetch(`/api/report/summary?duration=${duration}`);
    const data = await response.json();

    if (data.error) {
      throw new Error(data.error);
    }

    // Update health score
    const score = data.health_score;
    document.getElementById('health-value').textContent = score.overall;
    document.getElementById('health-grade').textContent = `Grade ${score.grade}`;

    // Update health circle color based on grade
    const circle = document.getElementById('health-circle');
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

    // Update breakdown
    const breakdown = score.breakdown || {};
    document.getElementById('breakdown-5g').textContent =
      (breakdown['5g_sinr'] || breakdown['5g_rsrp'] || '--') + '%';
    document.getElementById('breakdown-stability').textContent =
      (breakdown.stability || '--') + '%';
    document.getElementById('breakdown-tower').textContent =
      (breakdown.tower_stability || '--') + '%';

    // Update stats
    const sinr5g = data.signal_summary?.['5g']?.sinr;
    document.getElementById('stat-sinr').textContent =
      sinr5g !== undefined ? sinr5g + ' dB' : '--';

    const disruptions = data.disruptions?.total || 0;
    const disruptionEl = document.getElementById('stat-disruptions');
    disruptionEl.textContent = disruptions;
    if (disruptions > 10) {
      disruptionEl.style.color = 'var(--bad)';
    } else if (disruptions > 5) {
      disruptionEl.style.color = 'var(--ok)';
    } else {
      disruptionEl.style.color = 'var(--text)';
    }

    document.getElementById('stat-tower-changes').textContent = data.tower_changes || 0;
    document.getElementById('stat-speedtests').textContent = data.speedtest_count || 0;

    content.style.display = 'block';
    status.textContent = 'Ready';

  } catch (err) {
    error.textContent = err.message || 'Failed to generate report';
    error.style.display = 'block';
    content.style.display = 'none';
    status.textContent = 'Error';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Generate';
  }
}

function downloadReport(format) {
  const duration = document.getElementById('report-duration').value;
  const url = `/api/report?format=${format}&duration=${duration}`;

  // Create invisible link and click it to trigger download
  const a = document.createElement('a');
  a.href = url;
  a.download = `tmobile_diagnostic_report.${format}`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

// Auto-load report summary on page load if elements exist
document.addEventListener('DOMContentLoaded', function() {
  // Check if diagnostic report elements exist
  if (document.getElementById('report-btn')) {
    console.log('[REPORTS] Diagnostic reports UI loaded');
  }
});
