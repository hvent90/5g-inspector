"""FastAPI middleware for T-Mobile Dashboard.

Includes:
- Correlation ID middleware for request tracing
- Request logging middleware
- Metrics collection middleware
"""

import time
from typing import Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from .logging import set_correlation_id, get_correlation_id
from .metrics import get_metrics

log = structlog.get_logger()


class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """Middleware that adds correlation IDs to requests.

    Extracts correlation ID from X-Correlation-ID or X-Request-ID header,
    or generates a new one if not present. The ID is then:
    - Stored in context for logging
    - Added to response headers
    """

    HEADER_NAMES = ("x-correlation-id", "x-request-id")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Extract correlation ID from request headers
        correlation_id = None
        for header_name in self.HEADER_NAMES:
            correlation_id = request.headers.get(header_name)
            if correlation_id:
                break

        # Set correlation ID in context (generates new one if not present)
        correlation_id = set_correlation_id(correlation_id)

        # Process request
        response = await call_next(request)

        # Add correlation ID to response headers
        response.headers["X-Correlation-ID"] = correlation_id

        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware that logs request/response information and records metrics."""

    # Paths to exclude from logging (health checks, metrics)
    EXCLUDE_PATHS = {"/health", "/health/live", "/health/ready", "/metrics"}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.time()

        # Skip detailed logging for excluded paths but still process
        skip_logging = request.url.path in self.EXCLUDE_PATHS

        if not skip_logging:
            # Log request start
            log.debug(
                "request_started",
                method=request.method,
                path=request.url.path,
                query=str(request.query_params) if request.query_params else None,
                client_ip=request.client.host if request.client else None,
            )

        # Process request
        response = await call_next(request)

        # Calculate duration
        duration_ms = (time.time() - start_time) * 1000

        # Record metrics for all requests
        metrics = get_metrics()
        metrics.record_request(
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

        if not skip_logging:
            # Log request completion
            log.info(
                "request_completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
                correlation_id=get_correlation_id(),
            )

        return response
