/**
 * App - React entry point for Base UI components
 *
 * Mounts React components in designated containers within the vanilla TS app.
 * Uses React 19's createRoot for concurrent features.
 */

import { StrictMode } from 'react';
import { ToastProvider } from './Toast';
import { GrafanaLinks } from './GrafanaLinks';
import { ReportControls } from './ReportControls';
import { SignalMonitor } from './SignalMonitor';
import { SpeedChart } from './SpeedChart';
import { SpeedTestControls } from './SpeedTestControls';

interface AppProps {
  component?: 'report' | 'speedtest' | 'grafana' | 'signal' | 'speedchart';
}

export function App({ component = 'report' }: AppProps) {
  return (
    <StrictMode>
      <ToastProvider>
        {component === 'report' && <ReportControls />}
        {component === 'speedtest' && <SpeedTestControls />}
        {component === 'grafana' && <GrafanaLinks />}
        {component === 'signal' && <SignalMonitor />}
        {component === 'speedchart' && <SpeedChart />}
      </ToastProvider>
    </StrictMode>
  );
}

export default App;
