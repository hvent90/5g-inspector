#!/usr/bin/env python3
"""Script to add diagnostic reports UI to index.html"""

import re

# Read the current file
with open('index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# CSS to add before </style>
css_to_add = '''
    /* Diagnostic Reports Section */
    .reports-section {
      margin-bottom: 6px;
    }

    .reports-controls {
      display: flex;
      gap: 8px;
      align-items: center;
      padding: 10px;
    }

    .report-duration-select {
      flex: 1;
      background: rgba(0,0,0,0.3);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 10px;
      color: var(--text);
      font-family: inherit;
      font-size: 11px;
    }

    .report-duration-select:focus {
      outline: none;
      border-color: var(--accent);
    }

    .report-btn {
      padding: 10px 20px;
      background: var(--accent);
      color: white;
      border: none;
      border-radius: 4px;
      font-family: inherit;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      cursor: pointer;
      transition: opacity 0.2s;
    }

    .report-btn:hover:not(:disabled) {
      opacity: 0.9;
    }

    .report-btn:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    .report-content {
      border-top: 1px solid var(--border);
      padding: 10px;
    }

    .health-score-container {
      display: flex;
      gap: 15px;
      align-items: center;
      margin-bottom: 15px;
    }

    .health-circle {
      width: 80px;
      height: 80px;
      border-radius: 50%;
      border: 4px solid var(--accent);
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      background: rgba(0,0,0,0.3);
    }

    .health-value {
      font-size: 28px;
      font-weight: 600;
    }

    .health-grade {
      font-size: 10px;
      color: var(--text-dim);
      text-transform: uppercase;
    }

    .health-breakdown {
      flex: 1;
    }

    .breakdown-title {
      font-size: 9px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-dim);
      margin-bottom: 8px;
    }

    .breakdown-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
    }

    .breakdown-item {
      background: rgba(0,0,0,0.3);
      border-radius: 4px;
      padding: 8px;
      text-align: center;
    }

    .breakdown-label {
      font-size: 8px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-dim);
      margin-bottom: 4px;
    }

    .breakdown-value {
      font-size: 14px;
      font-weight: 600;
    }

    .report-stats-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 8px;
      margin-bottom: 15px;
    }

    .report-stat {
      background: rgba(0,0,0,0.3);
      border-radius: 4px;
      padding: 10px;
      text-align: center;
    }

    .report-stat-value {
      font-size: 18px;
      font-weight: 600;
    }

    .report-stat-label {
      font-size: 8px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-dim);
      margin-top: 4px;
    }

    .report-export-section {
      border-top: 1px solid var(--border);
      padding-top: 12px;
    }

    .export-title {
      font-size: 9px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-dim);
      margin-bottom: 8px;
    }

    .export-buttons {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
    }

    .export-format-btn {
      padding: 10px;
      background: var(--surface);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 4px;
      font-family: inherit;
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      cursor: pointer;
      transition: border-color 0.2s, background 0.2s;
    }

    .export-format-btn:hover {
      border-color: var(--accent);
      background: rgba(226, 0, 116, 0.1);
    }

    .report-error {
      background: rgba(239, 68, 68, 0.1);
      border: 1px solid var(--bad);
      border-radius: 4px;
      padding: 10px;
      margin: 10px;
      color: var(--bad);
      font-size: 11px;
    }

    @media (max-width: 400px) {
      .report-stats-grid {
        grid-template-columns: repeat(2, 1fr);
      }
      .export-buttons {
        grid-template-columns: 1fr;
      }
    }
'''

# HTML section to add before <footer>
html_to_add = '''
    <!-- Diagnostic Reports Section -->
    <div class="section reports-section">
      <div class="section-header">
        <span class="section-title">Diagnostic Reports</span>
        <span class="section-badge" id="report-status">Ready</span>
      </div>

      <div class="reports-controls">
        <select id="report-duration" class="report-duration-select">
          <option value="1">Last 1 Hour</option>
          <option value="6">Last 6 Hours</option>
          <option value="24" selected>Last 24 Hours</option>
          <option value="72">Last 3 Days</option>
          <option value="168">Last 7 Days</option>
          <option value="720">Last 30 Days</option>
        </select>
        <button id="report-btn" class="report-btn" onclick="loadReportSummary()">Generate</button>
      </div>

      <div id="report-error" class="report-error" style="display: none;"></div>

      <div id="report-content" class="report-content" style="display: none;">
        <!-- Health Score -->
        <div class="health-score-container">
          <div id="health-circle" class="health-circle">
            <span id="health-value" class="health-value">--</span>
            <span id="health-grade" class="health-grade">--</span>
          </div>
          <div class="health-breakdown">
            <div class="breakdown-title">Health Breakdown</div>
            <div class="breakdown-grid">
              <div class="breakdown-item">
                <div class="breakdown-label">5G Signal</div>
                <div id="breakdown-5g" class="breakdown-value">--%</div>
              </div>
              <div class="breakdown-item">
                <div class="breakdown-label">Stability</div>
                <div id="breakdown-stability" class="breakdown-value">--%</div>
              </div>
              <div class="breakdown-item">
                <div class="breakdown-label">Tower</div>
                <div id="breakdown-tower" class="breakdown-value">--%</div>
              </div>
            </div>
          </div>
        </div>

        <!-- Quick Stats -->
        <div class="report-stats-grid">
          <div class="report-stat">
            <div id="stat-sinr" class="report-stat-value">--</div>
            <div class="report-stat-label">Avg SINR</div>
          </div>
          <div class="report-stat">
            <div id="stat-disruptions" class="report-stat-value">--</div>
            <div class="report-stat-label">Disruptions</div>
          </div>
          <div class="report-stat">
            <div id="stat-tower-changes" class="report-stat-value">--</div>
            <div class="report-stat-label">Tower Changes</div>
          </div>
          <div class="report-stat">
            <div id="stat-speedtests" class="report-stat-value">--</div>
            <div class="report-stat-label">Speed Tests</div>
          </div>
        </div>

        <!-- Export Options -->
        <div class="report-export-section">
          <div class="export-title">Export Report</div>
          <div class="export-buttons">
            <button class="export-format-btn" onclick="downloadReport('json')">JSON</button>
            <button class="export-format-btn" onclick="downloadReport('csv')">CSV</button>
            <button class="export-format-btn" onclick="downloadReport('pdf')">PDF</button>
          </div>
        </div>
      </div>
    </div>

'''

# Script to add before </script>
script_to_add = '''
    // Diagnostic Reports Functions
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

'''

# Step 1: Add CSS before the last </style>
# Find the pattern for last @media before </style>
css_insert_pattern = r'(@media \(max-width: 400px\) \{\s*\.form-row \{\s*grid-template-columns: 1fr;\s*\}\s*\})\s*</style>'
css_replacement = r'\1' + css_to_add + '  </style>'
content = re.sub(css_insert_pattern, css_replacement, content, flags=re.DOTALL)

# Step 2: Add HTML section before <footer>
footer_pattern = r'(</div>\s*\n\s*)\n(\s*<footer>)'
footer_replacement = r'\1\n' + html_to_add + r'\2'
content = re.sub(footer_pattern, footer_replacement, content)

# Step 3: Add script functions before the final loadSupportInteractions() call
script_pattern = r'(\s*// Load support interactions on page load\s*\n\s*loadSupportInteractions\(\);)'
script_replacement = script_to_add + r'\1'
content = re.sub(script_pattern, script_replacement, content)

# Write the updated file
with open('index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("Successfully updated index.html with diagnostic reports UI!")
