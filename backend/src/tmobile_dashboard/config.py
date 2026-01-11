"""Configuration module for T-Mobile Dashboard using Pydantic Settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    """Gateway connection settings."""

    model_config = SettingsConfigDict(env_prefix="GATEWAY_")

    host: str = "192.168.12.1"
    port: int = 80
    poll_interval_ms: int = 200
    timeout_seconds: float = 2.0
    # Circuit breaker settings
    failure_threshold: int = 3
    recovery_timeout_seconds: int = 30
    # Signal quality drop detection (Priority 4)
    sinr_drop_threshold_db: float = Field(
        default=5.0,
        description="SINR drop threshold in dB to trigger event detection",
    )


class DatabaseSettings(BaseSettings):
    """Database settings."""

    model_config = SettingsConfigDict(env_prefix="DB_")

    path: Path = Path("signal_history.db")
    batch_interval_seconds: int = 5
    retention_days: int = 30
    # WAL mode for better concurrent access
    wal_mode: bool = True
    # Connection pool size for async operations
    pool_size: int = 5


class ServerSettings(BaseSettings):
    """HTTP server settings."""

    model_config = SettingsConfigDict(env_prefix="SERVER_")

    host: str = "0.0.0.0"
    port: int = 8080
    workers: int = 1  # For long-running, single worker is often better


class DisruptionSettings(BaseSettings):
    """Disruption detection thresholds."""

    model_config = SettingsConfigDict(env_prefix="DISRUPTION_")

    # Signal drop detection (dB drop)
    sinr_drop_5g: float = 10.0
    sinr_drop_4g: float = 8.0
    rsrp_drop_5g: float = 10.0
    rsrp_drop_4g: float = 10.0
    # Critical thresholds (absolute values)
    sinr_critical_5g: float = 0.0
    sinr_critical_4g: float = 0.0
    rsrp_critical_5g: float = -110.0
    rsrp_critical_4g: float = -115.0
    # Detection timing
    detection_window_seconds: int = 30
    cooldown_period_seconds: int = 60


class AlertSettings(BaseSettings):
    """Alert configuration."""

    model_config = SettingsConfigDict(env_prefix="ALERT_")

    enabled: bool = True
    history_file: Path = Path("alert_history.json")
    max_history_size: int = 1000


class LoggingSettings(BaseSettings):
    """Logging configuration."""

    model_config = SettingsConfigDict(env_prefix="LOG_")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "console"] = "json"
    # Include request correlation IDs
    correlation_id: bool = True


class LokiSettings(BaseSettings):
    """Loki log aggregation settings for event logging."""

    model_config = SettingsConfigDict(env_prefix="LOKI_")

    url: str = "http://localhost:3100"
    enabled: bool = True
    push_timeout_seconds: float = 5.0


class PingMonitorSettings(BaseSettings):
    """Continuous ping monitor settings for short outage detection."""

    model_config = SettingsConfigDict(env_prefix="PING_MONITOR_")

    enabled: bool = True
    interval_seconds: int = Field(
        default=30,
        description="Interval between ping cycles in seconds",
    )
    ping_timeout_seconds: int = Field(
        default=2,
        description="Timeout for each ping attempt",
    )
    targets: list[str] = Field(
        default=["8.8.8.8", "1.1.1.1", "208.54.0.1"],
        description="Ping targets for continuous monitoring",
    )


class SpeedtestSettings(BaseSettings):
    """Speedtest configuration."""

    model_config = SettingsConfigDict(env_prefix="SPEEDTEST_")

    # Tool preference order - first available tool wins
    # fast-cli (Netflix CDN) is preferred for real-world representative speeds
    # since ISPs may optimize traffic to speedtest servers
    preferred_tools: list[str] = Field(
        default=["fast-cli", "speedtest-cli", "ookla-speedtest"],
        description="Order of preference for speedtest tools",
    )

    # Ookla-specific settings
    ookla_server_id: int | None = Field(
        default=None,
        description="Specific Ookla server ID to target (None = auto-select)",
    )

    # Timeout for all tools
    timeout_seconds: int = Field(
        default=120,
        description="Timeout for speedtest execution",
    )


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # Sub-configurations
    gateway: GatewaySettings = Field(default_factory=GatewaySettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    disruption: DisruptionSettings = Field(default_factory=DisruptionSettings)
    alert: AlertSettings = Field(default_factory=AlertSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    speedtest: SpeedtestSettings = Field(default_factory=SpeedtestSettings)
    loki: LokiSettings = Field(default_factory=LokiSettings)
    ping_monitor: PingMonitorSettings = Field(default_factory=PingMonitorSettings)

    # Application settings
    debug: bool = False
    app_name: str = "T-Mobile Dashboard"
    version: str = "2.0.0"

    # Data paths (relative to app root)
    data_dir: Path = Path(".")
    speedtest_history_file: Path = Path("speedtest_history.json")
    support_interactions_file: Path = Path("support_interactions.json")
    scheduler_config_file: Path = Path("scheduler_config.json")
    service_terms_file: Path = Path("service_terms.json")


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance.

    Settings are loaded once and cached. To reload, call get_settings.cache_clear().
    """
    return Settings()


# Backwards compatibility alias
def get_config() -> Settings:
    """Alias for get_settings() for backwards compatibility."""
    return get_settings()
