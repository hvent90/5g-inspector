/**
 * App - React entry point for Base UI components
 *
 * Mounts React components in designated containers within the vanilla TS app.
 * Uses React 19's createRoot for concurrent features.
 */

import { StrictMode } from 'react';
import { ToastProvider } from './Toast';
import { ReportControls } from './ReportControls';
import { SpeedTestControls } from './SpeedTestControls';

interface AppProps {
  component?: 'report' | 'speedtest';
}

export function App({ component = 'report' }: AppProps) {
  return (
    <StrictMode>
      <ToastProvider>
        {component === 'report' && <ReportControls />}
        {component === 'speedtest' && <SpeedTestControls />}
      </ToastProvider>
    </StrictMode>
  );
}

export default App;
