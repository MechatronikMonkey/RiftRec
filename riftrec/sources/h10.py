"""H10Source - Polar H10 als Signalquelle über die BLE-HAL.

Abonniert den Standard-Heart-Rate-Measurement-Characteristic (0x2A37) und
parst dessen Payload selbst (Flags + HR + RR-Liste). Damit bleibt die
Polar-Semantik über der HAL, und der Transport bleibt austauschbar.

RR-Intervalle kommen in Einheiten von 1/1024 s und werden in ms umgerechnet.
Eine Notification kann 0..n RR-Intervalle tragen; jedes wird ein eigener
RrInterval-Record (tragendes HRV-Signal).

PMD/ECG/ACC ist bewusst NICHT enthalten (auf Windows reproduzierbar defekt,
siehe README) - der Seam dafür ist die HAL-`write`-Methode plus ein späterer
PMD-Substream.
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

# Flags-Byte (erstes Byte der Payload)
_FLAG_HR_16BIT = 0x01   # HR als uint16 statt uint8
_FLAG_ENERGY = 0x08     # Energy-Expended-Feld vorhanden (übersprungen)
_FLAG_RR = 0x10         # RR-Intervall-Liste vorhanden


def parse_hr_measurement(data: bytes) -> tuple[int, list[float]]:
    """Zerlegt eine 0x2A37-Payload in (HR in bpm, Liste RR-Intervalle in ms).

    Layout nach BLE-Spec: Flags-Byte, dann HR (uint8 oder uint16 LE), optional
    Energy Expended (uint16, übersprungen), dann 0..n RR-Werte (uint16 LE,
    Einheit 1/1024 s).
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
        # Transport injizierbar -> hardwarelos testbar; Standard = Bleak.
        if transport is None:
            from ..hal.ble_bleak import BleakTransport

            transport = BleakTransport()
        self._transport = transport

    async def run(self, emit: EmitFn, clock: SessionClock) -> None:
        await self._transport.connect(self._device)

        def on_notify(payload: bytes) -> None:
            # Läuft im BLE-Callback; Zeitstempel = Ankunftszeit, emit ist nicht-blockierend.
            hr, rr_list = parse_hr_measurement(payload)
            mono, utc = clock.now()
            emit(HrSample(mono_ns=mono, utc=utc, hr_bpm=hr))
            for rr_ms in rr_list:
                emit(RrInterval(mono_ns=mono, utc=utc, rr_ms=rr_ms))

        await self._transport.subscribe(HR_MEASUREMENT_UUID, on_notify)
        try:
            # Läuft bis zum Task-Cancel durch die Runtime.
            # (Auto-Reconnect bei Dropout ist Härtung, EW-39 - Seam hier.)
            while True:
                await asyncio.sleep(3600)
        finally:
            await self._transport.disconnect()
