/**
 * Database migration script for PostgreSQL
 *
 * Run with: bun run src/db/migrate.ts
 */

import { Effect, Console } from "effect"
import { SqlClient } from "@effect/sql"
import { PgClientLive } from "./config"

const createTables = `
-- Signal history table
CREATE TABLE IF NOT EXISTS signal_history (
  id SERIAL PRIMARY KEY,
  timestamp TEXT NOT NULL,
  timestamp_unix DOUBLE PRECISION NOT NULL,
  nr_sinr DOUBLE PRECISION,
  nr_rsrp DOUBLE PRECISION,
  nr_rsrq DOUBLE PRECISION,
  nr_rssi DOUBLE PRECISION,
  nr_bands TEXT,
  nr_gnb_id INTEGER,
  nr_cid INTEGER,
  lte_sinr DOUBLE PRECISION,
  lte_rsrp DOUBLE PRECISION,
  lte_rsrq DOUBLE PRECISION,
  lte_rssi DOUBLE PRECISION,
  lte_bands TEXT,
  lte_enb_id INTEGER,
  lte_cid INTEGER,
  registration_status TEXT,
  device_uptime INTEGER
);

-- Index for time-range queries (optimized for Grafana)
CREATE INDEX IF NOT EXISTS idx_signal_timestamp ON signal_history(timestamp_unix DESC);

-- Speedtest results table
CREATE TABLE IF NOT EXISTS speedtest_results (
  id SERIAL PRIMARY KEY,
  timestamp TEXT NOT NULL,
  timestamp_unix DOUBLE PRECISION NOT NULL,
  download_mbps DOUBLE PRECISION NOT NULL,
  upload_mbps DOUBLE PRECISION NOT NULL,
  ping_ms DOUBLE PRECISION NOT NULL,
  jitter_ms DOUBLE PRECISION,
  packet_loss_percent DOUBLE PRECISION,
  server_name TEXT,
  server_location TEXT,
  server_host TEXT,
  server_id INTEGER,
  client_ip TEXT,
  isp TEXT,
  tool TEXT NOT NULL,
  result_url TEXT,
  signal_snapshot TEXT,
  status TEXT NOT NULL,
  error_message TEXT,
  triggered_by TEXT NOT NULL,
  network_context TEXT NOT NULL DEFAULT 'unknown',
  pre_test_latency_ms DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_speedtest_timestamp ON speedtest_results(timestamp_unix DESC);

-- Disruption events table
CREATE TABLE IF NOT EXISTS disruption_events (
  id SERIAL PRIMARY KEY,
  timestamp TEXT NOT NULL,
  timestamp_unix DOUBLE PRECISION NOT NULL,
  event_type TEXT NOT NULL,
  severity TEXT NOT NULL,
  description TEXT NOT NULL,
  before_state TEXT,
  after_state TEXT,
  duration_seconds DOUBLE PRECISION,
  resolved INTEGER NOT NULL DEFAULT 0,
  resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_disruption_timestamp ON disruption_events(timestamp_unix DESC);
CREATE INDEX IF NOT EXISTS idx_disruption_type ON disruption_events(event_type);
CREATE INDEX IF NOT EXISTS idx_disruption_severity ON disruption_events(severity);

-- Network quality results table
CREATE TABLE IF NOT EXISTS network_quality_results (
  id SERIAL PRIMARY KEY,
  timestamp TEXT NOT NULL,
  timestamp_unix DOUBLE PRECISION NOT NULL,
  target_host TEXT NOT NULL,
  target_name TEXT,
  ping_ms DOUBLE PRECISION,
  jitter_ms DOUBLE PRECISION NOT NULL,
  packet_loss_percent DOUBLE PRECISION NOT NULL,
  status TEXT NOT NULL,
  error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_nq_timestamp ON network_quality_results(timestamp_unix DESC);
`

const migrate = Effect.gen(function* () {
  const sql = yield* SqlClient.SqlClient

  yield* Console.log("Running PostgreSQL migrations...")

  yield* sql.unsafe(createTables)

  yield* Console.log("Migrations completed successfully!")
})

// Run the migration
migrate.pipe(Effect.provide(PgClientLive), Effect.runPromise).catch(console.error)
