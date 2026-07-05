"""Spike: confirm the PC can see the Polar H10 over BLE.

Pure discovery only - no connection, no BleakHeart. The H10 only advertises
over BLE while its electrodes have skin contact (moistened strap, worn).
"""

import asyncio

from bleak import BleakScanner


async def main():
    print("Scanning for BLE devices (5s)...")
    devices = await BleakScanner.discover(timeout=5.0)

    if not devices:
        print("No BLE devices found at all. Check that Bluetooth is on and")
        print("no other app (Polar Flow, Polar Beat, ...) is already connected")
        print("to the H10.")
        return

    print(f"Found {len(devices)} device(s):\n")
    polar_hits = []
    for d in devices:
        name = d.name or "(no name)"
        print(f"  {d.address}  {name}")
        if d.name and "polar" in d.name.lower():
            polar_hits.append(d)

    print()
    if polar_hits:
        print("Polar device(s) found:")
        for d in polar_hits:
            print(f"  -> {d.name} @ {d.address}")
    else:
        print("No device with 'Polar' in its name found. Make sure the H10")
        print("electrodes have skin contact (moistened strap, worn).")


if __name__ == "__main__":
    asyncio.run(main())
