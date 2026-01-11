"""Database connection management with aiosqlite."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite
import structlog

from ..config import get_settings

log = structlog.get_logger()


class DatabaseConnection:
    """Async SQLite connection manager with WAL mode support."""

    def __init__(self, db_path: Path | None = None):
        settings = get_settings()
        self.db_path = db_path or settings.database.path
        self.wal_mode = settings.database.wal_mode
        self._initialized = False
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize database with schema and settings."""
        async with self._lock:
            if self._initialized:
                return

            async with aiosqlite.connect(self.db_path) as db:
                # Enable WAL mode for better concurrent access
                if self.wal_mode:
                    await db.execute("PRAGMA journal_mode=WAL")
                    await db.execute("PRAGMA synchronous=NORMAL")
                    log.info("database_wal_enabled", path=str(self.db_path))

                # Create schema
                await self._create_schema(db)
                await db.commit()

            self._initialized = True
            log.info("database_initialized", path=str(self.db_path))

    async def _create_schema(self, db: aiosqlite.Connection) -> None:
        """Create database tables and indexes."""
        # Signal history table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS signal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                timestamp_unix REAL NOT NULL,
                nr_sinr REAL, nr_rsrp REAL, nr_rsrq REAL, nr_rssi REAL,
                nr_bands TEXT, nr_gnb_id INTEGER, nr_cid INTEGER,
                lte_sinr REAL, lte_rsrp REAL, lte_rsrq REAL, lte_rssi REAL,
                lte_bands TEXT, lte_enb_id INTEGER, lte_cid INTEGER,
                registration_status TEXT, device_uptime INTEGER
            )
        """)

        # Disruption events table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS disruption_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                timestamp_unix REAL NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                description TEXT,
                before_state TEXT,
                after_state TEXT,
                duration_seconds REAL,
                resolved INTEGER DEFAULT 0,
                resolved_at TEXT
            )
        """)

        # Speedtest results table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS speedtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                timestamp_unix REAL NOT NULL,
                download_mbps REAL,
                upload_mbps REAL,
                ping_ms REAL,
                jitter_ms REAL,
                packet_loss_percent REAL,
                server_name TEXT,
                server_location TEXT,
                server_host TEXT,
                server_id INTEGER,
                client_ip TEXT,
                isp TEXT,
                tool TEXT DEFAULT 'unknown',
                result_url TEXT,
                signal_snapshot TEXT,
                status TEXT DEFAULT 'success',
                error_message TEXT,
                triggered_by TEXT DEFAULT 'manual',
                network_context TEXT DEFAULT 'unknown',
                pre_test_latency_ms REAL
            )
        """)

        # Migration: Add new columns if they don't exist (for existing databases)
        await self._migrate_speedtest_columns(db)

        # Network quality results table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS network_quality (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                timestamp_unix REAL NOT NULL,
                ping_ms REAL,
                jitter_ms REAL,
                packet_loss_percent REAL,
                target_host TEXT,
                packet_count INTEGER,
                signal_snapshot TEXT,
                speedtest_active INTEGER DEFAULT 0
            )
        """)

        # Migration: Add speedtest_active column to existing network_quality tables
        await self._migrate_network_quality_columns(db)

        # Gateway poll events table (every poll failure, NO retention policy per user choice)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS gateway_poll_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                timestamp_unix REAL NOT NULL,
                success INTEGER NOT NULL,
                error_type TEXT,
                error_message TEXT,
                circuit_state TEXT,
                response_time_ms REAL,
                signal_snapshot TEXT
            )
        """)

        # Continuous ping results table (for high-frequency 30s monitoring)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS continuous_ping (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                timestamp_unix REAL NOT NULL,
                target_host TEXT NOT NULL,
                success INTEGER NOT NULL,
                latency_ms REAL,
                error_type TEXT
            )
        """)

        # Hourly metrics table for congestion analysis
        await db.execute("""
            CREATE TABLE IF NOT EXISTS hourly_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                hour INTEGER NOT NULL,
                day_of_week INTEGER NOT NULL,
                is_weekend INTEGER NOT NULL,
                nr_sinr_avg REAL, nr_sinr_min REAL, nr_sinr_max REAL,
                nr_rsrp_avg REAL, nr_rsrp_min REAL, nr_rsrp_max REAL, nr_rsrq_avg REAL,
                lte_sinr_avg REAL, lte_sinr_min REAL, lte_sinr_max REAL,
                lte_rsrp_avg REAL, lte_rsrp_min REAL, lte_rsrp_max REAL, lte_rsrq_avg REAL,
                congestion_score REAL,
                sample_count INTEGER NOT NULL,
                UNIQUE(date, hour)
            )
        """)

        # Indexes for efficient queries
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_signal_timestamp_unix
            ON signal_history(timestamp_unix)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_disruption_timestamp_unix
            ON disruption_events(timestamp_unix)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_speedtest_timestamp_unix
            ON speedtest_results(timestamp_unix)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_network_quality_timestamp_unix
            ON network_quality(timestamp_unix)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_hourly_metrics_date
            ON hourly_metrics(date)
        """)

        # Index for filtering by network_context in Grafana
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_speedtest_network_context
            ON speedtest_results(network_context)
        """)

        # Index for filtering by tool in Grafana
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_speedtest_tool
            ON speedtest_results(tool)
        """)

        # Indexes for gateway poll events
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_gateway_poll_events_timestamp_unix
            ON gateway_poll_events(timestamp_unix)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_gateway_poll_events_success
            ON gateway_poll_events(success)
        """)

        # Indexes for continuous ping
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_continuous_ping_timestamp_unix
            ON continuous_ping(timestamp_unix)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_continuous_ping_target
            ON continuous_ping(target_host)
        """)

    async def _migrate_speedtest_columns(self, db: aiosqlite.Connection) -> None:
        """Add new columns to existing speedtest_results table."""
        cursor = await db.execute("PRAGMA table_info(speedtest_results)")
        columns = {row[1] for row in await cursor.fetchall()}

        # Define columns to add: (name, definition)
        new_columns = [
            ("network_context", "TEXT DEFAULT 'unknown'"),
            ("pre_test_latency_ms", "REAL"),
            ("server_host", "TEXT"),
            ("server_id", "INTEGER"),
            ("client_ip", "TEXT"),
            ("isp", "TEXT"),
            ("tool", "TEXT DEFAULT 'unknown'"),
            ("result_url", "TEXT"),
        ]

        for col_name, col_def in new_columns:
            if col_name not in columns:
                await db.execute(
                    f"ALTER TABLE speedtest_results ADD COLUMN {col_name} {col_def}"
                )
                log.info("database_migration", migration=f"added {col_name} column to speedtest_results")

    async def _migrate_network_quality_columns(self, db: aiosqlite.Connection) -> None:
        """Add new columns to existing network_quality table."""
        cursor = await db.execute("PRAGMA table_info(network_quality)")
        columns = {row[1] for row in await cursor.fetchall()}

        # Define columns to add: (name, definition)
        new_columns = [
            ("speedtest_active", "INTEGER DEFAULT 0"),
        ]

        for col_name, col_def in new_columns:
            if col_name not in columns:
                await db.execute(
                    f"ALTER TABLE network_quality ADD COLUMN {col_name} {col_def}"
                )
                log.info("database_migration", migration=f"added {col_name} column to network_quality")

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Get a database connection context manager."""
        if not self._initialized:
            await self.initialize()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    async def is_connected(self) -> bool:
        """Check if database is accessible."""
        try:
            async with self.connection() as db:
                await db.execute("SELECT 1")
            return True
        except Exception as e:
            log.error("database_connection_check_failed", error=str(e))
            return False

    async def get_stats(self) -> dict:
        """Get database statistics."""
        async with self.connection() as db:
            stats = {}

            # Table row counts
            for table in ["signal_history", "disruption_events", "speedtest_results", "network_quality", "hourly_metrics", "gateway_poll_events", "continuous_ping"]:
                cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")
                row = await cursor.fetchone()
                stats[f"{table}_count"] = row[0] if row else 0

            # Database file size
            if self.db_path.exists():
                stats["file_size_bytes"] = self.db_path.stat().st_size

            # Oldest/newest signal record
            cursor = await db.execute(
                "SELECT MIN(timestamp_unix), MAX(timestamp_unix) FROM signal_history"
            )
            row = await cursor.fetchone()
            if row and row[0]:
                stats["oldest_signal_unix"] = row[0]
                stats["newest_signal_unix"] = row[1]

            return stats

    async def cleanup_old_data(self, retention_days: int | None = None) -> int:
        """Remove data older than retention period. Returns number of rows deleted."""
        settings = get_settings()
        days = retention_days or settings.database.retention_days

        import time
        cutoff = time.time() - (days * 24 * 60 * 60)

        total_deleted = 0
        async with self.connection() as db:
            # Note: gateway_poll_events is NOT included per user choice (no retention policy)
            for table in ["signal_history", "disruption_events", "speedtest_results", "network_quality", "continuous_ping"]:
                cursor = await db.execute(
                    f"DELETE FROM {table} WHERE timestamp_unix < ?", (cutoff,)
                )
                total_deleted += cursor.rowcount

            await db.commit()

        if total_deleted > 0:
            log.info("database_cleanup", deleted_rows=total_deleted, retention_days=days)

        return total_deleted


# Global database instance
_db: DatabaseConnection | None = None


def get_db() -> DatabaseConnection:
    """Get the global database connection instance."""
    global _db
    if _db is None:
        _db = DatabaseConnection()
    return _db
