"""Signal sources: async producers that emit timestamped records."""

from .base import EmitFn, SignalSource
from .fake import FakeSource

__all__ = ["EmitFn", "SignalSource", "FakeSource"]
