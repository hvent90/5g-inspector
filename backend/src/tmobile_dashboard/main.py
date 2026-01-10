"""Main entry point for T-Mobile Dashboard."""

import uvicorn

from .config import get_settings


def main() -> None:
    """Run the dashboard server."""
    settings = get_settings()

    uvicorn.run(
        "tmobile_dashboard.api:app",
        host=settings.server.host,
        port=settings.server.port,
        workers=settings.server.workers,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info",
    )


if __name__ == "__main__":
    main()
