"""BleTransport-Protocol - die austauschbare BLE-Transport-Naht.

Liefert rohe Notification-Bytes an einen Callback; das Parsen ist Sache der
darüberliegenden Quelle. `write` wird erst für das PMD-Control-Point (ECG/ACC)
gebraucht, ist aber Teil des Vertrags, damit der spätere Dongle-Transport
dieselbe Schnittstelle erfüllt.
"""

from __future__ import annotations

from typing import Callable, Optional, Protocol

# Callback bekommt die rohen Bytes einer Characteristic-Notification.
NotifyCallback = Callable[[bytes], None]


class BleTransport(Protocol):
    @property
    def is_connected(self) -> bool: ...

    @property
    def address(self) -> Optional[str]: ...

    @property
    def name(self) -> Optional[str]: ...

    async def connect(self, device: Optional[str] = None) -> None:
        """Gerät finden und verbinden. `device` = Name/Adress-Teilstring,
        oder None für Auto-Scan (erstes Gerät mit 'polar' im Namen)."""
        ...

    async def subscribe(self, char_uuid: str, callback: NotifyCallback) -> None:
        """Notifications einer Characteristic an `callback` weiterreichen."""
        ...

    async def write(self, char_uuid: str, data: bytes, response: bool = True) -> None:
        """Bytes auf eine Characteristic schreiben (PMD-Control-Point)."""
        ...

    async def disconnect(self) -> None: ...
