"""Repository classes for data access."""

import json
import time
from collections import deque

import structlog

from ..models import DisruptionEvent, SpeedtestResult, NetworkQualityResult
from .connection import DatabaseConnection, get_db

log = structlog.get_logger()


class SignalRepository:
    """Repository for signal data with buffered batch inserts."""

    def __init__(self, db: DatabaseConnection | None = None, batch_size: int = 100):
        self.db = db or get_db()
        self.batch_size = batch_size
        self._buffer: deque[dict] = deque(maxlen=1000)
        self._last_flush = time.time()

    def buffer_record(self, record: dict) -> None:
        """Add a record to the buffer for batch insert."""
        self._buffer.append(record)

    async def flush_buffer(self) -> int:
        """Flush buffered records to database. Returns count of inserted rows."""
        if not self._buffer:
            return 0

        records = list(self._buffer)
        self._buffer.clear()

        async with self.db.connection() as db:
            await db.executemany(
                """
                INSERT INTO signal_history (
                    timestamp, timestamp_unix,
                    nr_sinr, nr_rsrp, nr_rsrq, nr_rssi, nr_bands, nr_gnb_id, nr_cid,
                    lte_sinr, lte_rsrp, lte_rsrq, lte_rssi, lte_bands, lte_enb_id, lte_cid,
                    registration_status, device_uptime
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r.get("timestamp"), r.get("timestamp_unix"),
                        r.get("nr_sinr"), r.get("nr_rsrp"), r.get("nr_rsrq"), r.get("nr_rssi"),
                        r.get("nr_bands"), r.get("nr_gnb_id"), r.get("nr_cid"),
                        r.get("lte_sinr"), r.get("lte_rsrp"), r.get("lte_rsrq"), r.get("lte_rssi"),
                        r.get("lte_bands"), r.get("lte_enb_id"), r.get("lte_cid"),
                        r.get("registration_status"), r.get("device_uptime"),
                    )
                    for r in records
                ],
            )
            await db.commit()

        self._last_flush = time.time()
        log.debug("signal_buffer_flushed", count=len(records))
        return len(records)

    async def query_history(
        self,
        duration_minutes: int = 60,
        resolution: str = "auto",
        limit: int | None = None,
    ) -> list[dict]:
        """Query historical signal data.

        Args:
            duration_minutes: How far back to query
            resolution: 'auto', 'full', or number of seconds to bucket
            limit: Maximum number of rows to return
        """
        cutoff = time.time() - (duration_minutes * 60)

        async with self.db.connection() as db:
            if resolution == "full" or duration_minutes <= 5:
                # Return all data points
                query = """
                    SELECT * FROM signal_history
                    WHERE timestamp_unix >= ?
                    ORDER BY timestamp_unix ASC
                """
                params: tuple = (cutoff,)
                if limit:
                    query += " LIMIT ?"
                    params = (cutoff, limit)

                cursor = await db.execute(query, params)
            else:
                # Auto-downsample for longer durations
                if resolution == "auto":
                    if duration_minutes <= 60:
                        bucket_seconds = 5
                    elif duration_minutes <= 360:
                        bucket_seconds = 30
                    elif duration_minutes <= 1440:
                        bucket_seconds = 60
                    else:
                        bucket_seconds = 300
                else:
                    bucket_seconds = int(resolution)

                query = """
                    SELECT
                        MIN(id) as id,
                        MIN(timestamp) as timestamp,
                        (CAST(timestamp_unix / ? AS INTEGER) * ?) as timestamp_unix,
                        AVG(nr_sinr) as nr_sinr,
                        AVG(nr_rsrp) as nr_rsrp,
                        AVG(nr_rsrq) as nr_rsrq,
                        AVG(nr_rssi) as nr_rssi,
                        MAX(nr_bands) as nr_bands,
                        MAX(nr_gnb_id) as nr_gnb_id,
                        MAX(nr_cid) as nr_cid,
                        AVG(lte_sinr) as lte_sinr,
                        AVG(lte_rsrp) as lte_rsrp,
                        AVG(lte_rsrq) as lte_rsrq,
                        AVG(lte_rssi) as lte_rssi,
                        MAX(lte_bands) as lte_bands,
                        MAX(lte_enb_id) as lte_enb_id,
                        MAX(lte_cid) as lte_cid,
                        MAX(registration_status) as registration_status,
                        MAX(device_uptime) as device_uptime
                    FROM signal_history
                    WHERE timestamp_unix >= ?
                    GROUP BY CAST(timestamp_unix / ? AS INTEGER)
                    ORDER BY timestamp_unix ASC
                """
                params = (bucket_seconds, bucket_seconds, cutoff, bucket_seconds)
                if limit:
                    query += " LIMIT ?"
                    params = (*params, limit)

                cursor = await db.execute(query, params)

            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_latest(self) -> dict | None:
        """Get the most recent signal record."""
        async with self.db.connection() as db:
            cursor = await db.execute(
                "SELECT * FROM signal_history ORDER BY timestamp_unix DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_tower_history(self, duration_minutes: int = 60) -> list[dict]:
        """Get tower/cell changes over time."""
        cutoff = time.time() - (duration_minutes * 60)

        async with self.db.connection() as db:
            cursor = await db.execute(
                """
                SELECT
                    timestamp, timestamp_unix,
                    nr_gnb_id, nr_cid, nr_bands,
                    lte_enb_id, lte_cid, lte_bands
                FROM signal_history
                WHERE timestamp_unix >= ?
                ORDER BY timestamp_unix ASC
                """,
                (cutoff,),
            )
            rows = await cursor.fetchall()

        # Find tower changes
        changes = []
        prev_nr_gnb = prev_lte_enb = None

        for row in rows:
            row_dict = dict(row)
            nr_gnb = row_dict.get("nr_gnb_id")
            lte_enb = row_dict.get("lte_enb_id")

            if nr_gnb != prev_nr_gnb or lte_enb != prev_lte_enb:
                changes.append({
                    "timestamp": row_dict["timestamp"],
                    "timestamp_unix": row_dict["timestamp_unix"],
                    "nr_gnb_id": nr_gnb,
                    "nr_cid": row_dict.get("nr_cid"),
                    "lte_enb_id": lte_enb,
                    "lte_cid": row_dict.get("lte_cid"),
                    "change_type": "5g" if nr_gnb != prev_nr_gnb else "4g",
                })
                prev_nr_gnb = nr_gnb
                prev_lte_enb = lte_enb

        return changes


class DisruptionRepository:
    """Repository for disruption events."""

    def __init__(self, db: DatabaseConnection | None = None):
        self.db = db or get_db()

    async def insert(self, event: DisruptionEvent) -> int:
        """Insert a disruption event. Returns the row ID."""
        async with self.db.connection() as db:
            cursor = await db.execute(
                """
                INSERT INTO disruption_events (
                    timestamp, timestamp_unix, event_type, severity, description,
                    before_state, after_state, duration_seconds, resolved, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.timestamp.isoformat(),
                    event.timestamp_unix,
                    event.event_type,
                    event.severity.value,
                    event.description,
                    json.dumps(event.before_state),
                    json.dumps(event.after_state),
                    event.duration_seconds,
                    1 if event.resolved else 0,
                    event.resolved_at.isoformat() if event.resolved_at else None,
                ),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def resolve(
        self,
        event_id: int,
        duration_seconds: float,
        resolved_at: str,
        after_state: dict | None = None,
    ) -> bool:
        """Mark a disruption event as resolved. Returns True if updated."""
        async with self.db.connection() as db:
            if after_state is not None:
                cursor = await db.execute(
                    """
                    UPDATE disruption_events
                    SET resolved = 1, duration_seconds = ?, resolved_at = ?, after_state = ?
                    WHERE id = ?
                    """,
                    (duration_seconds, resolved_at, json.dumps(after_state), event_id),
                )
            else:
                cursor = await db.execute(
                    """
                    UPDATE disruption_events
                    SET resolved = 1, duration_seconds = ?, resolved_at = ?
                    WHERE id = ?
                    """,
                    (duration_seconds, resolved_at, event_id),
                )
            await db.commit()
            return cursor.rowcount > 0

    async def query(self, duration_hours: int = 24) -> list[dict]:
        """Query disruption events."""
        cutoff = time.time() - (duration_hours * 60 * 60)

        async with self.db.connection() as db:
            cursor = await db.execute(
                """
                SELECT * FROM disruption_events
                WHERE timestamp_unix >= ?
                ORDER BY timestamp_unix DESC
                """,
                (cutoff,),
            )
            rows = await cursor.fetchall()

        result = []
        for row in rows:
            row_dict = dict(row)
            # Parse JSON fields
            if row_dict.get("before_state"):
                row_dict["before_state"] = json.loads(row_dict["before_state"])
            if row_dict.get("after_state"):
                row_dict["after_state"] = json.loads(row_dict["after_state"])
            result.append(row_dict)

        return result

    async def get_stats(self, duration_hours: int = 24) -> dict:
        """Get disruption statistics."""
        cutoff = time.time() - (duration_hours * 60 * 60)

        async with self.db.connection() as db:
            # Total count
            cursor = await db.execute(
                "SELECT COUNT(*) FROM disruption_events WHERE timestamp_unix >= ?",
                (cutoff,),
            )
            total = (await cursor.fetchone())[0]

            # By type
            cursor = await db.execute(
                """
                SELECT event_type, COUNT(*) as count
                FROM disruption_events
                WHERE timestamp_unix >= ?
                GROUP BY event_type
                """,
                (cutoff,),
            )
            by_type = {row["event_type"]: row["count"] for row in await cursor.fetchall()}

            # By severity
            cursor = await db.execute(
                """
                SELECT severity, COUNT(*) as count
                FROM disruption_events
                WHERE timestamp_unix >= ?
                GROUP BY severity
                """,
                (cutoff,),
            )
            by_severity = {row["severity"]: row["count"] for row in await cursor.fetchall()}

            # Average duration
            cursor = await db.execute(
                """
                SELECT AVG(duration_seconds)
                FROM disruption_events
                WHERE timestamp_unix >= ? AND duration_seconds IS NOT NULL
                """,
                (cutoff,),
            )
            avg_row = await cursor.fetchone()
            avg_duration = avg_row[0] if avg_row and avg_row[0] else None

        return {
            "period_hours": duration_hours,
            "total_events": total,
            "events_by_type": by_type,
            "events_by_severity": by_severity,
            "avg_duration_seconds": avg_duration,
        }


class SpeedtestRepository:
    """Repository for speedtest results."""

    def __init__(self, db: DatabaseConnection | None = None):
        self.db = db or get_db()

    async def insert(self, result: SpeedtestResult) -> int:
        """Insert a speedtest result. Returns the row ID."""
        async with self.db.connection() as db:
            cursor = await db.execute(
                """
                INSERT INTO speedtest_results (
                    timestamp, timestamp_unix, download_mbps, upload_mbps, ping_ms,
                    jitter_ms, packet_loss_percent, server_name, server_location,
                    server_host, server_id, client_ip, isp, tool, result_url,
                    signal_snapshot, status, error_message, triggered_by,
                    network_context, pre_test_latency_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.timestamp.isoformat(),
                    result.timestamp_unix,
                    result.download_mbps,
                    result.upload_mbps,
                    result.ping_ms,
                    result.jitter_ms,
                    result.packet_loss_percent,
                    result.server_name,
                    result.server_location,
                    result.server_host,
                    result.server_id,
                    result.client_ip,
                    result.isp,
                    result.tool,
                    result.result_url,
                    result.signal_at_test.model_dump_json() if result.signal_at_test else None,
                    result.status,
                    result.error_message,
                    result.triggered_by,
                    result.network_context.value,
                    result.pre_test_latency_ms,
                ),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def query(self, limit: int = 100) -> list[dict]:
        """Query speedtest results."""
        async with self.db.connection() as db:
            cursor = await db.execute(
                """
                SELECT * FROM speedtest_results
                ORDER BY timestamp_unix DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()

        result = []
        for row in rows:
            row_dict = dict(row)
            if row_dict.get("signal_snapshot"):
                row_dict["signal_snapshot"] = json.loads(row_dict["signal_snapshot"])
            result.append(row_dict)

        return result

    async def get_latest(self) -> dict | None:
        """Get the most recent speedtest result."""
        async with self.db.connection() as db:
            cursor = await db.execute(
                "SELECT * FROM speedtest_results ORDER BY timestamp_unix DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            if not row:
                return None

            row_dict = dict(row)
            if row_dict.get("signal_snapshot"):
                row_dict["signal_snapshot"] = json.loads(row_dict["signal_snapshot"])
            return row_dict


class NetworkQualityRepository:
    """Repository for network quality measurements."""

    def __init__(self, db: DatabaseConnection | None = None):
        self.db = db or get_db()

    async def insert(self, result: NetworkQualityResult) -> int:
        """Insert a network quality result. Returns the row ID."""
        async with self.db.connection() as db:
            cursor = await db.execute(
                """
                INSERT INTO network_quality (
                    timestamp, timestamp_unix, ping_ms, jitter_ms, packet_loss_percent,
                    target_host, packet_count, signal_snapshot
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.timestamp.isoformat(),
                    result.timestamp_unix,
                    result.ping_ms,
                    result.jitter_ms,
                    result.packet_loss_percent,
                    result.target_host,
                    result.packet_count,
                    result.signal_at_test.model_dump_json() if result.signal_at_test else None,
                ),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def query(self, duration_minutes: int = 60) -> list[dict]:
        """Query network quality results."""
        cutoff = time.time() - (duration_minutes * 60)

        async with self.db.connection() as db:
            cursor = await db.execute(
                """
                SELECT * FROM network_quality
                WHERE timestamp_unix >= ?
                ORDER BY timestamp_unix DESC
                """,
                (cutoff,),
            )
            rows = await cursor.fetchall()

        result = []
        for row in rows:
            row_dict = dict(row)
            if row_dict.get("signal_snapshot"):
                row_dict["signal_snapshot"] = json.loads(row_dict["signal_snapshot"])
            result.append(row_dict)

        return result

    async def get_latest(self) -> dict | None:
        """Get the most recent network quality result."""
        async with self.db.connection() as db:
            cursor = await db.execute(
                "SELECT * FROM network_quality ORDER BY timestamp_unix DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            if not row:
                return None

            row_dict = dict(row)
            if row_dict.get("signal_snapshot"):
                row_dict["signal_snapshot"] = json.loads(row_dict["signal_snapshot"])
            return row_dict
