"""Services module for T-Mobile Dashboard."""

from .gateway import GatewayService, OutageEvent
from .lifespan import lifespan, AppState, get_state
from .congestion import CongestionService, get_congestion_service
from .speedtest import SpeedtestService
from .disruption import DisruptionService, get_disruption_service
from .alerts import AlertService, get_alert_service
from .scheduler import SchedulerService, get_scheduler_service
from .support import SupportService, get_support_service
from .service_terms import ServiceTermsService, get_service_terms_service
from .network_quality import NetworkQualityService, get_network_quality_service
from .diagnostics import DiagnosticsService, get_diagnostics_service

__all__ = [
    "GatewayService",
    "OutageEvent",
    "lifespan",
    "AppState",
    "get_state",
    "CongestionService",
    "get_congestion_service",
    "SpeedtestService",
    "DisruptionService",
    "get_disruption_service",
    "AlertService",
    "get_alert_service",
    "SchedulerService",
    "get_scheduler_service",
    "SupportService",
    "get_support_service",
    "ServiceTermsService",
    "get_service_terms_service",
    "NetworkQualityService",
    "get_network_quality_service",
    "DiagnosticsService",
    "get_diagnostics_service",
]
