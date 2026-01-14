/**
 * GrafanaLinks - Taskbar quick links to Grafana dashboards
 *
 * Compact buttons for the Win95-style taskbar providing quick access to:
 * - Signal Dashboard (netpulse-signal-sqlite)
 * - Speedtest Dashboard (netpulse-speedtest-sqlite)
 * - Disruptions Dashboard (netpulse-disruptions-sqlite)
 */

const GRAFANA_BASE_URL = 'http://localhost:3002';

const dashboards = [
  {
    id: 'netpulse-signal-sqlite',
    name: 'Signal',
    icon: '\u{1F4F6}',
  },
  {
    id: 'netpulse-speedtest-sqlite',
    name: 'Speed',
    icon: '\u{1F680}',
  },
  {
    id: 'netpulse-disruptions-sqlite',
    name: 'Drops',
    icon: '\u{26A0}',
  },
] as const;

function GrafanaButton({
  dashboard,
}: {
  dashboard: (typeof dashboards)[number];
}) {
  const url = `${GRAFANA_BASE_URL}/d/${dashboard.id}`;

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="grafana-link-btn"
      title={`Open ${dashboard.name} in Grafana`}
    >
      <span className="grafana-link-icon">{dashboard.icon}</span>
      <span className="grafana-link-text">{dashboard.name}</span>
    </a>
  );
}

export function GrafanaLinks() {
  return (
    <div className="grafana-links">
      {dashboards.map((dashboard) => (
        <GrafanaButton key={dashboard.id} dashboard={dashboard} />
      ))}
      <a
        href={GRAFANA_BASE_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="grafana-link-btn"
        title="Open Grafana Home"
      >
        <span className="grafana-link-icon">{'\u{1F4CA}'}</span>
        <span className="grafana-link-text">All</span>
      </a>
    </div>
  );
}

export default GrafanaLinks;
