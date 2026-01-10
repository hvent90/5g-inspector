"""Backwards compatibility shim for database module.

This module is deprecated. Import from tmobile_dashboard.db instead.
"""

from .db import DatabaseConnection, get_db, SignalRepository

# Backwards compatibility alias
Database = DatabaseConnection

__all__ = ["Database", "DatabaseConnection", "get_db", "SignalRepository"]
