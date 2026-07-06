"""Signal-Quellen: async Producer, die zeitgestempelte Records emittieren."""

from .base import EmitFn, SignalSource
from .fake import FakeSource

__all__ = ["EmitFn", "SignalSource", "FakeSource"]
