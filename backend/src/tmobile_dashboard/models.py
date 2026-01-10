"""Data models for T-Mobile Dashboard using Pydantic."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, computed_field


class SignalQuality(str, Enum):
    """Signal quality levels based on SINR."""

    EXCELLENT = "excellent"  # SINR >= 20
    GOOD = "good"  # SINR >= 10
    FAIR = "fair"  # SINR >= 0
    POOR = "poor"  # SINR >= -5
    CRITICAL = "critical"  # SINR < -5


class ConnectionMode(str, Enum):
    """Connection mode types."""

    SA = "SA"  # 5G Standalone
    NSA = "NSA"  # 5G Non-Standalone
    LTE = "LTE"  # 4G only
    NO_SIGNAL = "No Signal"


class DisruptionSeverity(str, Enum):
    """Disruption event severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, Enum):
    """Alert types."""

    SIGNAL_DROP = "signal_drop"
    SIGNAL_CRITICAL = "signal_critical"
    TOWER_CHANGE = "tower_change"
    SPEED_LOW = "speed_low"
    PACKET_LOSS = "packet_loss"
    HIGH_JITTER = "high_jitter"


class NetworkContext(str, Enum):
    """Network usage context at time of speed test.

    Used to distinguish clean baseline measurements from tests
    affected by local network usage (streaming, downloads, etc.).
    """

    BASELINE = "baseline"  # Known idle time (e.g., 2-5am), minimal LAN usage
    IDLE = "idle"  # Low latency detected, likely idle network
    LIGHT = "light"  # Moderate latency, some network activity
    BUSY = "busy"  # High latency spike, heavy local usage detected
    UNKNOWN = "unknown"  # Unable to determine context


# ============================================
# Signal Models
# ============================================


class SignalMetrics(BaseModel):
    """Signal metrics for 5G or 4G."""

    sinr: float | None = None
    rsrp: float | None = None
    rsrq: float | None = None
    rssi: float | None = None
    bands: list[str] = Field(default_factory=list)
    tower_id: int | None = Field(None, description="gNB ID for 5G, eNB ID for 4G")
    cell_id: int | None = None

    @computed_field
    @property
    def quality(self) -> SignalQuality:
        """Calculate signal quality from SINR."""
        if self.sinr is None:
            return SignalQuality.POOR
        if self.sinr >= 20:
            return SignalQuality.EXCELLENT
        if self.sinr >= 10:
            return SignalQuality.GOOD
        if self.sinr >= 0:
            return SignalQuality.FAIR
        if self.sinr >= -5:
            return SignalQuality.POOR
        return SignalQuality.CRITICAL


class SignalData(BaseModel):
    """Complete signal data snapshot from gateway."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    timestamp_unix: float = Field(default_factory=lambda: datetime.utcnow().timestamp())

    # 5G NR metrics
    nr: SignalMetrics = Field(default_factory=SignalMetrics)

    # 4G LTE metrics
    lte: SignalMetrics = Field(default_factory=SignalMetrics)

    # Device info
    registration_status: str | None = None
    connection_mode: ConnectionMode = ConnectionMode.NO_SIGNAL
    device_uptime: int | None = None

    # Raw data for debugging
    raw: dict[str, Any] | None = Field(None, exclude=True)


class SignalRecord(BaseModel):
    """Database record for signal history."""

    id: int | None = None
    timestamp: str
    timestamp_unix: float

    # 5G NR fields
    nr_sinr: float | None = None
    nr_rsrp: float | None = None
    nr_rsrq: float | None = None
    nr_rssi: float | None = None
    nr_bands: str | None = None
    nr_gnb_id: int | None = None
    nr_cid: int | None = None

    # 4G LTE fields
    lte_sinr: float | None = None
    lte_rsrp: float | None = None
    lte_rsrq: float | None = None
    lte_rssi: float | None = None
    lte_bands: str | None = None
    lte_enb_id: int | None = None
    lte_cid: int | None = None

    # Device info
    registration_status: str | None = None
    device_uptime: int | None = None


# ============================================
# Speedtest Models
# ============================================


class SpeedtestResult(BaseModel):
    """Speed test result with signal correlation."""

    id: str = Field(default_factory=lambda: str(int(datetime.utcnow().timestamp() * 1000)))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    timestamp_unix: float = Field(default_factory=lambda: datetime.utcnow().timestamp())

    # Speed metrics
    download_mbps: float
    upload_mbps: float
    ping_ms: float
    jitter_ms: float | None = None
    packet_loss_percent: float | None = None

    # Server info
    server_name: str | None = None
    server_location: str | None = None
    server_host: str | None = None  # e.g., "speedtest.nyc.example.com:8080"
    server_id: int | None = None  # Ookla server ID

    # Client/ISP info detected by test
    client_ip: str | None = None
    isp: str | None = None

    # Tool used for test
    tool: str = "unknown"  # "ookla-speedtest", "speedtest-cli", "fast-cli"
    result_url: str | None = None  # Ookla shareable result URL

    # Signal snapshot at time of test
    signal_at_test: SignalData | None = None

    # Network context - helps distinguish clean baselines from tests affected by local usage
    network_context: NetworkContext = NetworkContext.UNKNOWN
    pre_test_latency_ms: float | None = None  # Latency probe before test started

    # Test metadata
    status: str = "success"
    error_message: str | None = None
    triggered_by: str = "manual"  # manual, scheduled, api


class SpeedtestStatus(BaseModel):
    """Current speedtest execution status."""

    running: bool = False
    last_result: SpeedtestResult | None = None
    last_run_time: datetime | None = None
    next_scheduled: datetime | None = None


# ============================================
# Disruption Models
# ============================================


class DisruptionEvent(BaseModel):
    """A signal disruption event."""

    id: str = Field(default_factory=lambda: str(int(datetime.utcnow().timestamp() * 1000)))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    timestamp_unix: float = Field(default_factory=lambda: datetime.utcnow().timestamp())

    event_type: str  # signal_drop_5g, tower_change, etc.
    severity: DisruptionSeverity
    description: str

    # Before/after state for comparison
    before_state: dict[str, Any] = Field(default_factory=dict)
    after_state: dict[str, Any] = Field(default_factory=dict)

    # Calculated metrics
    duration_seconds: float | None = None
    resolved: bool = False
    resolved_at: datetime | None = None


class DisruptionStats(BaseModel):
    """Aggregated disruption statistics."""

    period_hours: int
    total_events: int
    events_by_type: dict[str, int] = Field(default_factory=dict)
    events_by_severity: dict[str, int] = Field(default_factory=dict)
    avg_duration_seconds: float | None = None


# ============================================
# Support Interaction Models
# ============================================


class SupportInteraction(BaseModel):
    """Customer support interaction record."""

    id: str = Field(default_factory=lambda: str(int(datetime.utcnow().timestamp() * 1000)))
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Contact details
    contact_date: str | None = None
    contact_time: str | None = None
    contact_method: str = "phone"  # phone, chat, email, store

    # Agent info
    agent_name: str = ""
    agent_id: str = ""
    ticket_number: str = ""

    # Interaction details
    complaint_summary: str = ""
    response_received: str = ""
    resolution_offered: str = ""
    resolution_status: str = "unresolved"  # unresolved, partial, resolved

    # Metrics
    wait_time_minutes: int | None = None
    call_duration_minutes: int | None = None
    was_transferred: bool = False
    transfer_count: int = 0
    customer_satisfaction: int | None = Field(None, ge=1, le=5)

    notes: str = ""


class SupportSummary(BaseModel):
    """Summary of support interactions."""

    total_interactions: int
    unresolved_count: int
    avg_wait_time_minutes: float | None = None
    avg_call_duration_minutes: float | None = None
    total_transfers: int
    avg_satisfaction: float | None = None


# ============================================
# Alert Models
# ============================================


class Alert(BaseModel):
    """An active or historical alert."""

    id: str = Field(default_factory=lambda: str(int(datetime.utcnow().timestamp() * 1000)))
    created_at: datetime = Field(default_factory=datetime.utcnow)

    alert_type: AlertType
    severity: DisruptionSeverity
    title: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)

    acknowledged: bool = False
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None

    resolved: bool = False
    resolved_at: datetime | None = None


class AlertConfig(BaseModel):
    """Alert configuration settings."""

    enabled: bool = True

    # Thresholds
    signal_drop_threshold_db: float = 10.0
    speed_low_threshold_mbps: float = 10.0
    packet_loss_threshold_percent: float = 5.0
    jitter_threshold_ms: float = 50.0

    # Notification settings
    notify_on_warning: bool = True
    notify_on_critical: bool = True
    cooldown_minutes: int = 5


# ============================================
# Network Quality Models
# ============================================


class NetworkQualityResult(BaseModel):
    """Network quality measurement (ping, jitter, packet loss)."""

    id: str = Field(default_factory=lambda: str(int(datetime.utcnow().timestamp() * 1000)))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    timestamp_unix: float = Field(default_factory=lambda: datetime.utcnow().timestamp())

    # Measurements
    ping_ms: float
    jitter_ms: float
    packet_loss_percent: float

    # Test parameters
    target_host: str = "8.8.8.8"
    packet_count: int = 10

    # Context
    signal_at_test: SignalData | None = None
    speedtest_active: bool = False  # True if a speedtest was running during this measurement


# ============================================
# Health Models
# ============================================


class ComponentHealth(BaseModel):
    """Health status of a component."""

    name: str
    healthy: bool
    message: str = "OK"
    last_check: datetime = Field(default_factory=datetime.utcnow)


class HealthStatus(BaseModel):
    """Overall application health status."""

    status: str = "healthy"  # healthy, degraded, unhealthy
    uptime_seconds: float
    version: str

    components: list[ComponentHealth] = Field(default_factory=list)

    # Key metrics
    last_signal_poll: datetime | None = None
    signal_poll_success_rate: float | None = None
    db_connected: bool = True
    active_alerts: int = 0
