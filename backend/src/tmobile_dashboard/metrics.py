"""Prometheus metrics for T-Mobile Dashboard.

Exposes application metrics in Prometheus format:
- HTTP request metrics (count, duration, status)
- Gateway polling metrics
- Signal quality metrics
- Speed test metrics
- Database metrics
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricsCollector:
    """Collects and exposes application metrics.

    Uses simple counters and gauges without external dependencies.
    Metrics are exposed in Prometheus text format.
    """

    # Request counters: {(method, path, status): count}
    request_count: dict[tuple[str, str, int], int] = field(default_factory=lambda: defaultdict(int))

    # Request duration: {(method, path): [total_ms, count]}
    request_duration: dict[tuple[str, str], list[float]] = field(
        default_factory=lambda: defaultdict(lambda: [0.0, 0])
    )

    # Gauge values
    gauges: dict[str, float] = field(default_factory=dict)

    # Counter values
    counters: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Start time for uptime calculation
    start_time: float = field(default_factory=time.time)

    def record_request(
        self, method: str, path: str, status_code: int, duration_ms: float
    ) -> None:
        """Record an HTTP request."""
        # Normalize path to avoid high cardinality
        normalized_path = self._normalize_path(path)

        self.request_count[(method, normalized_path, status_code)] += 1
        duration_data = self.request_duration[(method, normalized_path)]
        duration_data[0] += duration_ms
        duration_data[1] += 1

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Set a gauge value."""
        key = self._make_key(name, labels)
        self.gauges[key] = value

    def inc_counter(self, name: str, value: int = 1, labels: dict[str, str] | None = None) -> None:
        """Increment a counter."""
        key = self._make_key(name, labels)
        self.counters[key] += value

    def _make_key(self, name: str, labels: dict[str, str] | None) -> str:
        """Create a unique key for a metric with labels."""
        if not labels:
            return name
        label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def _normalize_path(self, path: str) -> str:
        """Normalize path to reduce cardinality.

        Replaces IDs and dynamic segments with placeholders.
        """
        parts = path.split("/")
        normalized = []
        for part in parts:
            # Replace numeric IDs
            if part.isdigit():
                normalized.append(":id")
            # Replace UUIDs or long alphanumeric strings
            elif len(part) > 20 and part.replace("-", "").isalnum():
                normalized.append(":id")
            else:
                normalized.append(part)
        return "/".join(normalized)

    def get_uptime_seconds(self) -> float:
        """Get application uptime in seconds."""
        return time.time() - self.start_time

    def to_prometheus_format(self, app_state: Any | None = None) -> str:
        """Export metrics in Prometheus text format."""
        lines = []

        # Add uptime gauge
        lines.append("# HELP tmobile_dashboard_uptime_seconds Application uptime in seconds")
        lines.append("# TYPE tmobile_dashboard_uptime_seconds gauge")
        lines.append(f"tmobile_dashboard_uptime_seconds {self.get_uptime_seconds():.2f}")
        lines.append("")

        # Add request count metrics
        if self.request_count:
            lines.append("# HELP http_requests_total Total HTTP requests")
            lines.append("# TYPE http_requests_total counter")
            for (method, path, status), count in sorted(self.request_count.items()):
                lines.append(
                    f'http_requests_total{{method="{method}",path="{path}",status="{status}"}} {count}'
                )
            lines.append("")

        # Add request duration metrics
        if self.request_duration:
            lines.append("# HELP http_request_duration_ms_sum Total HTTP request duration in milliseconds")
            lines.append("# TYPE http_request_duration_ms_sum counter")
            for (method, path), (total_ms, count) in sorted(self.request_duration.items()):
                lines.append(
                    f'http_request_duration_ms_sum{{method="{method}",path="{path}"}} {total_ms:.2f}'
                )

            lines.append("")
            lines.append("# HELP http_request_duration_ms_count Number of HTTP requests for duration")
            lines.append("# TYPE http_request_duration_ms_count counter")
            for (method, path), (total_ms, count) in sorted(self.request_duration.items()):
                lines.append(
                    f'http_request_duration_ms_count{{method="{method}",path="{path}"}} {count}'
                )
            lines.append("")

        # Add gateway metrics from app state if available
        if app_state is not None:
            gateway_stats = app_state.gateway.get_stats()

            lines.append("# HELP tmobile_gateway_poll_success_total Successful gateway polls")
            lines.append("# TYPE tmobile_gateway_poll_success_total counter")
            lines.append(f"tmobile_gateway_poll_success_total {gateway_stats.get('success_count', 0)}")
            lines.append("")

            lines.append("# HELP tmobile_gateway_poll_error_total Failed gateway polls")
            lines.append("# TYPE tmobile_gateway_poll_error_total counter")
            lines.append(f"tmobile_gateway_poll_error_total {gateway_stats.get('error_count', 0)}")
            lines.append("")

            lines.append("# HELP tmobile_gateway_circuit_breaker_state Circuit breaker state (0=closed, 1=open, 2=half-open)")
            lines.append("# TYPE tmobile_gateway_circuit_breaker_state gauge")
            circuit_state = gateway_stats.get("circuit_state", "closed")
            state_value = {"closed": 0, "open": 1, "half_open": 2}.get(circuit_state, 0)
            lines.append(f"tmobile_gateway_circuit_breaker_state {state_value}")
            lines.append("")

            # Speed test metrics from last result
            # Only emit actual values if result is fresh (within 2 minutes)
            # This prevents Prometheus from creating continuous data points from stale results
            if app_state.speedtest._last_result is not None:
                result = app_state.speedtest._last_result
                result_age_seconds = time.time() - result.timestamp_unix
                is_fresh = result_age_seconds < 120  # 2 minutes freshness window

                if result.status == "success":
                    lines.append("# HELP tmobile_speedtest_download_mbps Last speed test download in Mbps")
                    lines.append("# TYPE tmobile_speedtest_download_mbps gauge")
                    if is_fresh:
                        lines.append(f"tmobile_speedtest_download_mbps {result.download_mbps}")
                    else:
                        lines.append("tmobile_speedtest_download_mbps NaN")
                    lines.append("")

                    lines.append("# HELP tmobile_speedtest_upload_mbps Last speed test upload in Mbps")
                    lines.append("# TYPE tmobile_speedtest_upload_mbps gauge")
                    if is_fresh:
                        lines.append(f"tmobile_speedtest_upload_mbps {result.upload_mbps}")
                    else:
                        lines.append("tmobile_speedtest_upload_mbps NaN")
                    lines.append("")

                    lines.append("# HELP tmobile_speedtest_ping_ms Last speed test ping in milliseconds")
                    lines.append("# TYPE tmobile_speedtest_ping_ms gauge")
                    if is_fresh:
                        lines.append(f"tmobile_speedtest_ping_ms {result.ping_ms}")
                    else:
                        lines.append("tmobile_speedtest_ping_ms NaN")
                    lines.append("")

                    if result.jitter_ms is not None:
                        lines.append("# HELP tmobile_speedtest_jitter_ms Last speed test jitter in milliseconds")
                        lines.append("# TYPE tmobile_speedtest_jitter_ms gauge")
                        if is_fresh:
                            lines.append(f"tmobile_speedtest_jitter_ms {result.jitter_ms}")
                        else:
                            lines.append("tmobile_speedtest_jitter_ms NaN")
                        lines.append("")

                    # Always emit timestamp so we know when last test occurred
                    lines.append("# HELP tmobile_speedtest_last_success_timestamp Unix timestamp of last successful speed test")
                    lines.append("# TYPE tmobile_speedtest_last_success_timestamp gauge")
                    lines.append(f"tmobile_speedtest_last_success_timestamp {result.timestamp_unix}")
                    lines.append("")

                    # Network context as a labeled metric (only when fresh)
                    context = result.network_context.value if result.network_context else "unknown"
                    lines.append("# HELP tmobile_speedtest_network_context Network context of last test (1=active for that context)")
                    lines.append("# TYPE tmobile_speedtest_network_context gauge")
                    for ctx in ["baseline", "idle", "light", "busy", "unknown"]:
                        if is_fresh:
                            value = 1 if ctx == context else 0
                        else:
                            value = 0  # No active context when stale
                        lines.append(f'tmobile_speedtest_network_context{{context="{ctx}"}} {value}')
                    lines.append("")

            # Scheduler stats (import inline to avoid circular import)
            try:
                from .services.scheduler import get_scheduler_service
                scheduler = get_scheduler_service()
                stats = scheduler.get_stats()

                lines.append("# HELP tmobile_scheduler_tests_total Total scheduled speed tests completed")
                lines.append("# TYPE tmobile_scheduler_tests_total counter")
                lines.append(f"tmobile_scheduler_tests_total {stats.get('tests_completed', 0)}")
                lines.append("")

                lines.append("# HELP tmobile_scheduler_tests_failed_total Total scheduled speed tests failed")
                lines.append("# TYPE tmobile_scheduler_tests_failed_total counter")
                lines.append(f"tmobile_scheduler_tests_failed_total {stats.get('tests_failed', 0)}")
                lines.append("")

                lines.append("# HELP tmobile_scheduler_tests_below_threshold_total Tests below speed threshold")
                lines.append("# TYPE tmobile_scheduler_tests_below_threshold_total counter")
                lines.append(f"tmobile_scheduler_tests_below_threshold_total {stats.get('tests_below_threshold', 0)}")
                lines.append("")

                lines.append("# HELP tmobile_scheduler_avg_download_mbps Average download speed from scheduler")
                lines.append("# TYPE tmobile_scheduler_avg_download_mbps gauge")
                lines.append(f"tmobile_scheduler_avg_download_mbps {stats.get('avg_download_mbps', 0)}")
                lines.append("")

                lines.append("# HELP tmobile_scheduler_avg_upload_mbps Average upload speed from scheduler")
                lines.append("# TYPE tmobile_scheduler_avg_upload_mbps gauge")
                lines.append(f"tmobile_scheduler_avg_upload_mbps {stats.get('avg_upload_mbps', 0)}")
                lines.append("")

                lines.append("# HELP tmobile_scheduler_running Scheduler running state (1=running, 0=stopped)")
                lines.append("# TYPE tmobile_scheduler_running gauge")
                lines.append(f"tmobile_scheduler_running {1 if stats.get('is_running') else 0}")
                lines.append("")

                # Next test countdown
                next_test_in = stats.get("next_test_in_seconds")
                if next_test_in is not None:
                    lines.append("# HELP tmobile_scheduler_next_test_seconds Seconds until next scheduled test")
                    lines.append("# TYPE tmobile_scheduler_next_test_seconds gauge")
                    lines.append(f"tmobile_scheduler_next_test_seconds {next_test_in:.0f}")
                    lines.append("")
            except Exception:
                pass  # Scheduler not available

            # Signal metrics if data is available
            if app_state.gateway.current_data:
                data = app_state.gateway.current_data
                lines.append("# HELP tmobile_signal_nr_sinr 5G NR SINR in dB")
                lines.append("# TYPE tmobile_signal_nr_sinr gauge")
                if data.nr.sinr is not None:
                    lines.append(f"tmobile_signal_nr_sinr {data.nr.sinr}")

                lines.append("")
                lines.append("# HELP tmobile_signal_nr_rsrp 5G NR RSRP in dBm")
                lines.append("# TYPE tmobile_signal_nr_rsrp gauge")
                if data.nr.rsrp is not None:
                    lines.append(f"tmobile_signal_nr_rsrp {data.nr.rsrp}")

                lines.append("")
                lines.append("# HELP tmobile_signal_lte_sinr LTE SINR in dB")
                lines.append("# TYPE tmobile_signal_lte_sinr gauge")
                if data.lte.sinr is not None:
                    lines.append(f"tmobile_signal_lte_sinr {data.lte.sinr}")

                lines.append("")
                lines.append("# HELP tmobile_signal_lte_rsrp LTE RSRP in dBm")
                lines.append("# TYPE tmobile_signal_lte_rsrp gauge")
                if data.lte.rsrp is not None:
                    lines.append(f"tmobile_signal_lte_rsrp {data.lte.rsrp}")

                lines.append("")

        # Add custom gauges
        for key, value in sorted(self.gauges.items()):
            lines.append(f"{key} {value}")

        # Add custom counters
        for key, value in sorted(self.counters.items()):
            lines.append(f"{key} {value}")

        return "\n".join(lines)


# Global metrics collector instance
_metrics: MetricsCollector | None = None


def get_metrics() -> MetricsCollector:
    """Get the global metrics collector instance."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics


def reset_metrics() -> None:
    """Reset metrics collector (for testing)."""
    global _metrics
    _metrics = None
