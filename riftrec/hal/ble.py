"""BleTransport protocol - the swappable BLE transport seam.

Delivers raw notification bytes to a callback; parsing is the job of the source
above. `write` is only needed for the PMD control point (ECG/ACC) but is part of
the contract so the later dongle transport implements the same interface.
"""

from __future__ import annotations

from typing import Callable, Optional, Protocol

# The callback receives the raw bytes of a characteristic notification.
NotifyCallback = Callable[[bytes], None]


class BleTransport(Protocol):
    @property
    def is_connected(self) -> bool: ...

    @property
    def address(self) -> Optional[str]: ...

    @property
    def name(self) -> Optional[str]: ...

    async def connect(self, device: Optional[str] = None) -> None:
        """Find and connect a device. `device` = name/address substring, or
        None for auto-scan (first device with 'polar' in its name)."""
        ...

    async def subscribe(self, char_uuid: str, callback: NotifyCallback) -> None:
        """Forward notifications of a characteristic to `callback`."""
        ...

    async def write(self, char_uuid: str, data: bytes, response: bool = True) -> None:
        """Write bytes to a characteristic (PMD control point)."""
        ...

    async def disconnect(self) -> None: ...
