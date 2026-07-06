"""Scan for Polar BLE devices to populate the settings dropdown."""

from __future__ import annotations

import asyncio


async def _scan(timeout: float) -> list[tuple[str, str]]:
    from bleak import BleakScanner

    devices = await BleakScanner.discover(timeout=timeout)
    return [
        (d.name, d.address)
        for d in devices
        if d.name and "polar" in d.name.lower()
    ]


def scan_polar_devices(timeout: float = 6.0) -> list[tuple[str, str]]:
    """Blocking scan; returns [(name, address), ...] for Polar devices."""
    return asyncio.run(_scan(timeout))
