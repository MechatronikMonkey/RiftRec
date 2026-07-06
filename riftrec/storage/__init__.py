"""Storage layer: the SessionSink contract and its SQLite implementation."""

from .base import SessionSink
from .sqlite_sink import SqliteSink

__all__ = ["SessionSink", "SqliteSink"]
