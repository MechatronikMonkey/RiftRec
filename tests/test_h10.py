"""Milestone-1 tests without hardware: 0x2A37 parser + H10Source via a fake transport."""

from __future__ import annotations

import asyncio

from riftrec.clock import SessionClock
from riftrec.hal.ble import NotifyCallback
from riftrec.model import HrSample, RrInterval
from riftrec.sources.h10 import HR_MEASUREMENT_UUID, H10Source, parse_hr_measurement


def test_parse_uint8_no_rr() -> None:
    # flags=0x00 (uint8 HR, no RR), HR=75
    hr, rr = parse_hr_measurement(bytes([0x00, 75]))
    assert hr == 75
    assert rr == []


def test_parse_uint8_with_rr() -> None:
    # flags=0x10 (RR present), HR=80, RR=1024/1024s -> 1000 ms, then 512 -> 500 ms
    payload = bytes([0x10, 80, 0x00, 0x04, 0x00, 0x02])
    hr, rr = parse_hr_measurement(payload)
    assert hr == 80
    assert rr == [1000.0, 500.0]


def test_parse_uint16_hr() -> None:
    # flags=0x01 (uint16 HR), HR=300 (0x012C little-endian)
    hr, rr = parse_hr_measurement(bytes([0x01, 0x2C, 0x01]))
    assert hr == 300
    assert rr == []


def test_parse_energy_then_rr_skipped() -> None:
    # flags=0x18 (Energy + RR), HR=60, Energy 2 bytes skipped, RR=256 -> 250 ms
    payload = bytes([0x18, 60, 0xFF, 0xFF, 0x00, 0x01])
    hr, rr = parse_hr_measurement(payload)
    assert hr == 60
    assert rr == [250.0]


class _FakeTransport:
    """Plays a script of payloads into the notify callback."""

    def __init__(self, payloads: list[bytes]) -> None:
        self._payloads = payloads
        self._cb: NotifyCallback | None = None
        self.connected = False
        self.disconnected = False

    @property
    def is_connected(self) -> bool:
        return self.connected

    @property
    def address(self):  # pragma: no cover
        return "FA:KE:00:00:00:00"

    @property
    def name(self):  # pragma: no cover
        return "Polar H10 FAKE"

    async def connect(self, device=None) -> None:
        self.connected = True

    async def subscribe(self, char_uuid: str, callback: NotifyCallback) -> None:
        assert char_uuid == HR_MEASUREMENT_UUID
        self._cb = callback
        for p in self._payloads:
            callback(p)

    async def write(self, char_uuid, data, response=True):  # pragma: no cover
        pass

    async def disconnect(self) -> None:
        self.disconnected = True


def test_h10source_emits_records() -> None:
    payloads = [
        bytes([0x10, 80, 0x00, 0x04]),  # HR 80, RR 1000 ms
        bytes([0x00, 82]),              # HR 82, no RR
    ]
    transport = _FakeTransport(payloads)
    source = H10Source(transport=transport)
    clock = SessionClock()
    emitted: list = []

    async def drive() -> None:
        task = asyncio.create_task(source.run(emitted.append, clock))
        await asyncio.sleep(0.05)  # let subscribe + callbacks run
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())

    hr = [r for r in emitted if isinstance(r, HrSample)]
    rr = [r for r in emitted if isinstance(r, RrInterval)]
    assert [h.hr_bpm for h in hr] == [80, 82]
    assert [round(r.rr_ms) for r in rr] == [1000]
    assert transport.connected and transport.disconnected


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK - {name}")
    print("OK - all H10 tests passed")
