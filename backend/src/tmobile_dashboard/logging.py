"""Structured logging configuration for T-Mobile Dashboard.

Configures structlog with:
- JSON or console output based on settings
- Request correlation ID binding
- Context variables for automatic ID propagation
"""

import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.typing import EventDict, WrappedLogger

from .config import get_settings

# Context variable for request correlation ID
correlation_id_ctx: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    """Get the current correlation ID from context."""
    return correlation_id_ctx.get()


def set_correlation_id(cid: str | None = None) -> str:
    """Set a correlation ID in context. Generates one if not provided."""
    if cid is None:
        cid = str(uuid.uuid4())[:8]  # Short ID for readability
    correlation_id_ctx.set(cid)
    return cid


def add_correlation_id(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Processor that adds correlation ID to log events."""
    cid = correlation_id_ctx.get()
    if cid is not None:
        event_dict["correlation_id"] = cid
    return event_dict


def add_app_context(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Processor that adds application context to log events."""
    settings = get_settings()
    event_dict["app"] = settings.app_name
    event_dict["version"] = settings.version
    return event_dict


def configure_logging() -> None:
    """Configure structlog for the application.

    Sets up structured logging based on settings:
    - JSON format for production (machine-readable)
    - Console format for development (human-readable)
    """
    settings = get_settings()

    # Shared processors for both formats
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        add_correlation_id,
    ]

    if settings.logging.format == "json":
        # JSON format for production
        processors: list[Any] = [
            *shared_processors,
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Console format for development
        processors = [
            *shared_processors,
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.logging.level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Also configure standard library logging to route through structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.getLevelName(settings.logging.level),
    )

    # Quiet noisy loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """Get a structured logger instance.

    Args:
        name: Optional logger name for context

    Returns:
        Configured structlog BoundLogger
    """
    logger = structlog.get_logger()
    if name:
        logger = logger.bind(logger_name=name)
    return logger
