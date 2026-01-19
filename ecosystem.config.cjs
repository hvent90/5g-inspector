const path = require('path');
const logsDir = path.join(__dirname, 'logs');

module.exports = {
  apps: [
    {
      name: 'api',
      cwd: './apps/api',
      script: 'bun',
      args: 'run src/main.ts',
      autorestart: true,
      watch: false,
      // Logging for SRE diagnostics
      output: path.join(logsDir, 'api-out.log'),
      error: path.join(logsDir, 'api-error.log'),
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      merge_logs: true,
      env: {
        NODE_ENV: 'production',
      },
    },
    {
      name: 'web',
      cwd: './apps/web',
      script: 'bun',
      args: 'run dev',
      autorestart: true,
      watch: false,
      // Logging for SRE diagnostics
      output: path.join(logsDir, 'web-out.log'),
      error: path.join(logsDir, 'web-error.log'),
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      merge_logs: true,
    },
  ],
};
