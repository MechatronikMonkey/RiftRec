"""BleakTransport - BleTransport implementation over bleak (today's Windows path).

Wraps scan/connect/notify/write. The reactive-pairing issue and the PMD
reconnect bug affect only ECG/ACC (see README); the standard HR service (0x2A37)
used here connects reliably - including on reconnect.
"""

from __future__ import annotations

from typing import Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from .ble import NotifyCallback


class BleakTransport:
    def __init__(self) -> None:
        self._client: Optional[BleakClient] = None
        self._device: Optional[BLEDevice] = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def address(self) -> Optional[str]:
        return self._device.address if self._device else None

    @property
    def name(self) -> Optional[str]:
        return self._device.name if self._device else None

    async def connect(self, device: Optional[str] = None) -> None:
        if device is None:
            found = await BleakScanner.find_device_by_filter(
                lambda d, adv: bool(d.name) and "polar" in d.name.lower()
            )
        else:
            needle = device.lower()
            found = await BleakScanner.find_device_by_filter(
                lambda d, adv: needle in (d.address or "").lower()
                or (bool(d.name) and needle in d.name.lower())
            )
        if found is None:
            raise RuntimeError(
                "No matching BLE device found. "
                "Is the H10 worn and are the electrodes moistened?"
            )
        self._device = found
        self._client = BleakClient(found)
        await self._client.connect()

    async def subscribe(self, char_uuid: str, callback: NotifyCallback) -> None:
        assert self._client is not None, "call connect() first"
        await self._client.start_notify(
            char_uuid, lambda _sender, data: callback(bytes(data))
        )

    async def write(self, char_uuid: str, data: bytes, response: bool = True) -> None:
        assert self._client is not None, "call connect() first"
        await self._client.write_gatt_char(char_uuid, data, response=response)

    async def disconnect(self) -> None:
        if self._client is not None and self._client.is_connected:
            await self._client.disconnect()
        self._client = None
