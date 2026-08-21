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
from ..model import DeviceInfo, HrRaw, HrSample, RrInterval
from .base import EmitFn

# Standard BLE Heart Rate Measurement characteristic.
HR_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

# Device Information Service + Battery Service. Open standard services, no
# pairing required - the same category as the HR service, unlike PMD.
_DIS_CHARS = {
    "manufacturer": "00002a29-0000-1000-8000-00805f9b34fb",
    "model": "00002a24-0000-1000-8000-00805f9b34fb",
    "serial": "00002a25-0000-1000-8000-00805f9b34fb",
    "hardware_rev": "00002a27-0000-1000-8000-00805f9b34fb",
    "firmware_rev": "00002a26-0000-1000-8000-00805f9b34fb",
    "software_rev": "00002a28-0000-1000-8000-00805f9b34fb",
}
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"


async def read_device_info(transport: BleTransport, utc: str) -> DeviceInfo:
    """Read identity and battery level of the connected sensor.

    Every read is guarded individually: a sensor that does not expose a
    characteristic, or a transport that fails on it, must never prevent a
    recording from starting. Missing values stay None.
    """
    info = DeviceInfo(
        source="h10",
        utc=utc,
        address=getattr(transport, "address", None),
        name=getattr(transport, "name", None),
    )
    for field, uuid in _DIS_CHARS.items():
        try:
            raw = await transport.read(uuid)
        except Exception:
            continue
        try:
            setattr(info, field, raw.decode("utf-8").strip("\x00").strip())
        except UnicodeDecodeError:
            setattr(info, field, raw.hex())
    try:
        battery = await transport.read(BATTERY_LEVEL_UUID)
        if battery:
            info.battery_pct = battery[0]
    except Exception:
        pass
    return info

# Flags byte (first byte of the payload)
_FLAG_HR_16BIT = 0x01           # HR as uint16 instead of uint8
_FLAG_CONTACT_DETECTED = 0x02   # skin contact detected (only valid with _SUPPORTED)
_FLAG_CONTACT_SUPPORTED = 0x04  # device reports contact status at all
_FLAG_ENERGY = 0x08             # Energy Expended field present (skipped, kept in HrRaw)
_FLAG_RR = 0x10                 # RR interval list present


def parse_hr_measurement(data: bytes) -> tuple[int, list[float], Optional[bool]]:
    """Split a 0x2A37 payload into (HR in bpm, RR intervals in ms, contact).

    Layout per BLE spec: flags byte, then HR (uint8 or uint16 LE), optional
    Energy Expended (uint16, skipped - the value survives in the HrRaw payload),
    then 0..n RR values (uint16 LE, unit 1/1024 s).

    `contact` is True/False only when the device advertises support for the
    sensor contact feature, otherwise None. Per BLE spec the "detected" bit is
    meaningless unless the "supported" bit is set, so the two must be read
    together - a naive check of the detected bit alone would report "no contact"
    for every device that simply does not implement the feature (EW-86).
    """
    if not data:
        return 0, [], None
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
    contact: Optional[bool] = None
    if flags & _FLAG_CONTACT_SUPPORTED:
        contact = bool(flags & _FLAG_CONTACT_DETECTED)
    return hr, rr_intervals, contact


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

        # Which strap produced this recording, on which firmware, at what
        # battery level - recorded before the first sample arrives.
        _, utc = clock.now()
        emit(await read_device_info(self._transport, utc))

        def on_notify(payload: bytes) -> None:
            # Runs in the BLE callback; timestamp = arrival time, emit is non-blocking.
            hr, rr_list, contact = parse_hr_measurement(payload)
            mono, utc = clock.now()
            # Raw first: if parsing ever proves wrong, the payload is still there.
            emit(HrRaw(mono_ns=mono, utc=utc, payload=bytes(payload)))
            emit(HrSample(mono_ns=mono, utc=utc, hr_bpm=hr, contact=contact))
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
