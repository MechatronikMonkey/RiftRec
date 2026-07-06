"""H10Source - Polar H10 as a signal source over the BLE HAL.

Subscribes to the standard Heart Rate Measurement characteristic (0x2A37) and
parses its payload itself (flags + HR + RR list). This keeps the Polar semantics
above the HAL, and the transport stays swappable.

RR intervals arrive in units of 1/1024 s and are converted to ms. A
notification can carry 0..n RR intervals; each becomes its own RrInterval record
(the load-bearing HRV signal).

PMD/ECG/ACC is deliberately NOT included (reproducibly broken on Windows, see
README) - the seam for it is the HAL `write` method plus a later PMD substream.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from ..clock import SessionClock
from ..hal.ble import BleTransport
from ..model import HrSample, RrInterval
from .base import EmitFn

# Standard BLE Heart Rate Measurement characteristic.
HR_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

# Flags byte (first byte of the payload)
_FLAG_HR_16BIT = 0x01   # HR as uint16 instead of uint8
_FLAG_ENERGY = 0x08     # Energy Expended field present (skipped)
_FLAG_RR = 0x10         # RR interval list present


def parse_hr_measurement(data: bytes) -> tuple[int, list[float]]:
    """Split a 0x2A37 payload into (HR in bpm, list of RR intervals in ms).

    Layout per BLE spec: flags byte, then HR (uint8 or uint16 LE), optional
    Energy Expended (uint16, skipped), then 0..n RR values (uint16 LE, unit
    1/1024 s).
    """
    if not data:
        return 0, []
    flags = data[0]
    idx = 1
    if flags & _FLAG_HR_16BIT:
        hr = int.from_bytes(data[idx:idx + 2], "little")
        idx += 2
    else:
        hr = data[idx]
        idx += 1
    if flags & _FLAG_ENERGY:
        idx += 2
    rr_intervals: list[float] = []
    if flags & _FLAG_RR:
        while idx + 1 < len(data):
            raw = int.from_bytes(data[idx:idx + 2], "little")
            rr_intervals.append(raw / 1024.0 * 1000.0)
            idx += 2
    return hr, rr_intervals


class H10Source:
    name = "h10"

    def __init__(
        self,
        device: Optional[str] = None,
        transport: Optional[BleTransport] = None,
    ) -> None:
        self._device = device
        # Transport injectable -> testable without hardware; default = Bleak.
        if transport is None:
            from ..hal.ble_bleak import BleakTransport

            transport = BleakTransport()
        self._transport = transport

    async def run(self, emit: EmitFn, clock: SessionClock) -> None:
        await self._transport.connect(self._device)

        def on_notify(payload: bytes) -> None:
            # Runs in the BLE callback; timestamp = arrival time, emit is non-blocking.
            hr, rr_list = parse_hr_measurement(payload)
            mono, utc = clock.now()
            emit(HrSample(mono_ns=mono, utc=utc, hr_bpm=hr))
            for rr_ms in rr_list:
                emit(RrInterval(mono_ns=mono, utc=utc, rr_ms=rr_ms))

        await self._transport.subscribe(HR_MEASUREMENT_UUID, on_notify)
        try:
            # Runs until the runtime cancels the task.
            # (Auto-reconnect on dropout is hardening, EW-39 - seam here.)
            while True:
                await asyncio.sleep(3600)
        finally:
            await self._transport.disconnect()
