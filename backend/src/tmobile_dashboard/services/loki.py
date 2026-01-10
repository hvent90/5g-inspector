"""Loki log push service for event logging.

Pushes discrete events (like speedtest results) to Loki for time-series
visualization in Grafana. Unlike Prometheus gauges which are scraped continuously,
Loki events create one data point per push - perfect for infrequent events.
"""

import json
import time
from typing import Any

import httpx
import structlog

log = structlog.get_logger()


class LokiClient:
    """Client for pushing logs to Loki's HTTP API."""

    def __init__(self, url: str = "http://localhost:3100", timeout: float = 5.0):
        self._url = url.rstrip("/")
        self._push_url = f"{self._url}/loki/api/v1/push"
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def push_event(
        self,
        event_type: str,
        data: dict[str, Any],
        labels: dict[str, str] | None = None,
    ) -> bool:
        """Push a single event to Loki.

        Args:
            event_type: Event type label (e.g., "speedtest")
            data: JSON-serializable event data
            labels: Additional labels for the stream (low cardinality only)

        Returns:
            True if push succeeded, False otherwise
        """
        # Build labels - keep cardinality low
        stream_labels = {
            "job": "tmobile-dashboard",
            "event_type": event_type,
        }
        if labels:
            stream_labels.update(labels)

        # Current timestamp in nanoseconds (Loki requirement)
        timestamp_ns = str(int(time.time() * 1_000_000_000))

        # Loki push format: streams with values as [timestamp_ns, log_line]
        payload = {
            "streams": [
                {
                    "stream": stream_labels,
                    "values": [[timestamp_ns, json.dumps(data)]],
                }
            ]
        }

        try:
            client = await self._get_client()
            response = await client.post(
                self._push_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if response.status_code == 204:
                log.debug("loki_push_success", event_type=event_type)
                return True
            else:
                log.warning(
                    "loki_push_failed",
                    status=response.status_code,
                    body=response.text[:200],
                )
                return False
        except httpx.TimeoutException:
            log.warning("loki_push_timeout", event_type=event_type)
            return False
        except Exception as e:
            log.error("loki_push_error", error=str(e))
            return False

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# Global instance
_loki_client: LokiClient | None = None


def get_loki_client() -> LokiClient:
    """Get the global Loki client instance."""
    global _loki_client
    if _loki_client is None:
        from ..config import get_settings

        settings = get_settings()
        _loki_client = LokiClient(
            url=settings.loki.url,
            timeout=settings.loki.push_timeout_seconds,
        )
    return _loki_client


async def close_loki_client() -> None:
    """Close the global Loki client."""
    global _loki_client
    if _loki_client is not None:
        await _loki_client.close()
        _loki_client = None
