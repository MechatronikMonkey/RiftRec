"""Confirmation spike: does SimpleBLE hit the exact same "PMD notifications
silently never arrive" problem as bleak on this machine, or is it specific
to bleak/WinRT?

Uses SimpleBLE directly against the raw BLE UUIDs (no BleakHeart) since
BleakHeart is bleak-only:
  - Standard HR service/characteristic (no pairing needed)
  - Polar's proprietary PMD service/control-point/data characteristics
    (requires the bonded/authenticated link)

This is NOT a real recorder - just enough to prove/disprove the hypothesis
that the problem lives in Windows' native BLE stack rather than in bleak
specifically (SimpleBLE also goes through WinRT on Windows).
"""

import time

import simplepyble

HR_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"

PMD_SERVICE = "fb005c80-02e7-f387-1cad-8acd2d8df0c8"
PMD_CONTROL_POINT = "fb005c81-02e7-f387-1cad-8acd2d8df0c8"
PMD_DATA = "fb005c82-02e7-f387-1cad-8acd2d8df0c8"

# START_MEASUREMENT(0x02) / ECG(0x00) / SAMPLE_RATE=130Hz / RESOLUTION=14bit
START_ECG_CMD = bytes([0x02, 0x00, 0x00, 0x01, 0x82, 0x00, 0x01, 0x01, 0x0E, 0x00])

SCAN_TIMEOUT_MS = 5000
COLLECT_S = 15


def find_h10(adapter):
    print(f"Scanning for {SCAN_TIMEOUT_MS}ms...")
    adapter.scan_for(SCAN_TIMEOUT_MS)
    for p in adapter.scan_get_results():
        if p.identifier() and "polar" in p.identifier().lower():
            return p
    return None


def main():
    adapters = simplepyble.Adapter.get_adapters()
    if not adapters:
        print("No Bluetooth adapter found.")
        return
    adapter = adapters[0]
    print(f"Using adapter: {adapter.identifier()}")

    peripheral = find_h10(adapter)
    if peripheral is None:
        print("Polar device not found. Is it worn with moistened electrodes?")
        return

    print(f"Connecting to {peripheral.identifier()} [{peripheral.address()}]...")
    t0 = time.monotonic()
    peripheral.connect()
    print(f"Connected in {(time.monotonic() - t0) * 1000:.0f} ms")

    hr_frames = []
    pmd_frames = []

    peripheral.notify(HR_SERVICE, HR_MEASUREMENT,
                       lambda data: hr_frames.append((data, time.monotonic())))
    peripheral.notify(PMD_SERVICE, PMD_DATA,
                       lambda data: pmd_frames.append((data, time.monotonic())))

    print("Writing START ECG command to PMD control point...")
    try:
        peripheral.write_request(PMD_SERVICE, PMD_CONTROL_POINT, START_ECG_CMD)
        print("Control point write: OK (no exception)")
    except Exception as e:
        print(f"Control point write raised: {e!r}")

    print(f"Collecting for {COLLECT_S}s...")
    time.sleep(COLLECT_S)

    try:
        peripheral.unsubscribe(HR_SERVICE, HR_MEASUREMENT)
        peripheral.unsubscribe(PMD_SERVICE, PMD_DATA)
    finally:
        peripheral.disconnect()

    print()
    print(f"HR notifications received:  {len(hr_frames)}")
    for data, ts in hr_frames[:5]:
        print(f"  {ts:.3f}  {data.hex()}")
    print(f"PMD/ECG notifications received: {len(pmd_frames)}")
    for data, ts in pmd_frames[:5]:
        print(f"  {ts:.3f}  {data.hex()}")


if __name__ == "__main__":
    main()
