"""Spike: read a Polar H10's identity and firmware version over BLE.

No pairing needed - the Device Information Service (0x180A) and the Battery
Service (0x180F) are open standard services, the same category as the HR
service that works reliably here. This is deliberately *not* the PMD path.

Two uses:

1. **Now:** find out which firmware a strap runs. Polar changed a lot in the
   4.x line - 4.0.4 introduced BLE secure pairing and access control for
   recorded files, and there are open reports in Polar's own SDK tracker of
   PMD notifications failing on recent firmware (issues #778, #829). Knowing
   the version is the first step before blaming the Windows BLE stack.

2. **Later:** inventory for the pilot. Run this once per strap at onboarding
   and keep the output - serial number plus firmware version per device.
   Mixed firmware across a measurement series is a variable nobody wants to
   discover afterwards.

Also lists every GATT service the device exposes, which answers in passing
whether the PMD service is advertised at all.

Usage:
    python h10_device_info.py            # scan and pick the first Polar
    python h10_device_info.py <address>  # target a known device
"""

from __future__ import annotations

import asyncio
import sys

from bleak import BleakClient, BleakScanner

# Device Information Service characteristics (Bluetooth SIG standard)
DIS = {
    "00002a29-0000-1000-8000-00805f9b34fb": "Manufacturer",
    "00002a24-0000-1000-8000-00805f9b34fb": "Model",
    "00002a25-0000-1000-8000-00805f9b34fb": "Serial",
    "00002a27-0000-1000-8000-00805f9b34fb": "Hardware rev",
    "00002a26-0000-1000-8000-00805f9b34fb": "Firmware rev",
    "00002a28-0000-1000-8000-00805f9b34fb": "Software rev",
    "00002a23-0000-1000-8000-00805f9b34fb": "System ID",
}
BATTERY = "00002a19-0000-1000-8000-00805f9b34fb"
PMD_SERVICE_PREFIX = "fb005c80"  # Polar Measurement Data service


async def find_polar() -> str | None:
    print("Scanning for a Polar device (10 s)...")
    devices = await BleakScanner.discover(timeout=10.0)
    for d in devices:
        if d.name and "polar" in d.name.lower():
            print(f"  found: {d.name}  [{d.address}]")
            return d.address
    print("  no Polar device found.")
    print("  Wear the strap and moisten the electrodes - a dry H10 on the desk")
    print("  does not advertise.")
    return None


async def main(address: str | None) -> None:
    if address is None:
        address = await find_polar()
        if address is None:
            return

    print(f"\nConnecting to {address} ...")
    async with BleakClient(address) as client:
        print("connected.\n")

        print("=== Device information ===")
        # Enumerate what the device actually offers rather than guessing UUIDs,
        # and never swallow the error - a failing read here is itself a finding,
        # because it points at the same encryption/bonding wall as PMD.
        dis_service = next(
            (s for s in client.services if s.uuid.startswith("0000180a")), None
        )
        if dis_service is None:
            print("  no Device Information Service on this device")
        for char in dis_service.characteristics if dis_service else []:
            label = DIS.get(char.uuid, char.uuid[4:8])
            if "read" not in char.properties:
                print(f"  {label:14}: (not readable, properties: {char.properties})")
                continue
            try:
                raw = await client.read_gatt_char(char)
            except Exception as exc:
                print(f"  {label:14}: READ FAILED - {type(exc).__name__}: {exc}")
                continue
            try:
                value = raw.decode("utf-8").strip("\x00").strip()
            except UnicodeDecodeError:
                value = raw.hex()
            print(f"  {label:14}: {value}")

        try:
            batt = await client.read_gatt_char(BATTERY)
            print(f"  {'Battery':14}: {batt[0]} %")
        except Exception as exc:
            print(f"  {'Battery':14}: READ FAILED - {type(exc).__name__}: {exc}")

        print("\n=== GATT services exposed ===")
        pmd_seen = False
        for service in client.services:
            known = ""
            if service.uuid.startswith(PMD_SERVICE_PREFIX):
                known = "  <-- PMD (raw ECG/ACC)"
                pmd_seen = True
            elif service.uuid.startswith("0000180d"):
                known = "  <-- Heart Rate (what RiftRec uses)"
            elif service.uuid.startswith("0000180a"):
                known = "  <-- Device Information"
            elif service.uuid.startswith("0000180f"):
                known = "  <-- Battery"
            print(f"  {service.uuid}{known}")

        print()
        print("PMD service advertised:", "yes" if pmd_seen else "no")
        print()
        print("Compare the firmware against Polar's release notes:")
        print("  4.2.0  offline recording fix, M460/M430 pairing")
        print("  4.1.10 lower idle power, Android 15 connectivity")
        print("  4.0.4  BLE SECURE PAIRING, factory reset, file access control")
        print("  -> from 4.0.4 onwards secure pairing is in play, which is the")
        print("     prime suspect for the PMD reconnect problem (EW-86 notes).")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
