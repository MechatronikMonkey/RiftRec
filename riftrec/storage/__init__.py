"""Storage-Schicht: der SessionSink-Vertrag und seine SQLite-Umsetzung."""

from .base import SessionSink
from .sqlite_sink import SqliteSink

__all__ = ["SessionSink", "SqliteSink"]
