"""Spike: connect to the Polar H10 via BleakHeart and pull a handful of
frames from every stream we need (HR/RR, ECG, ACC) - NOT a continuous
recorder, just walking the full data path once. Times how long each
stream takes to deliver its samples and prints min/avg/max inter-arrival
stats, similar to how `ping` reports round-trip-time statistics.

ECG/ACC use the PMD protocol, which requires BLE pairing/bonding. On this
machine, pairing reactively (via the Windows dialog bleak/Windows pops up
on first PMD access) failed with "Verbindungsfehler"; pairing the device
proactively via Windows Settings -> Bluetooth & devices worked.

Known, reproducible limitation on this machine (Windows + this BT adapter):
ECG/ACC (PMD protocol) notifications only arrive on the very FIRST BLE
connection after a fresh Windows pairing. Every reconnect after that gets
SUCCESS from the PMD control point (available_settings, start_streaming)
but zero actual data notifications - control writes and push notifications
apparently don't share the same encrypted-link fate on reconnect. Tried
and confirmed NOT to fix it: physical H10 reset (strap off skin ~60s),
`use_cached_services=False`, staggered notify calls, a full PC reboot, and
an explicit `client.pair()` call. See
https://github.com/hbldh/bleak/issues/1943 and
https://github.com/fsmeraldi/bleakheart/issues/5 for the wider community
reports of the same class of problem (no confirmed fix as of 2026-07).

Conclusion: HR/RR (standard service, no pairing needed) is reliable on
every connection and covers the actual MVP requirement (EW-26). ECG/ACC
via PMD is parked as a known-broken path on Windows for now.
"""

import asyncio
import statistics
import time

from bleak import BleakScanner, BleakClient
from bleakheart import HeartRate, PolarMeasurementData

SAMPLES_PER_STREAM = 3
STREAM_TIMEOUT_S = 15


async def scan():
    print("Scanning for Polar device...")
    return await BleakScanner.find_device_by_filter(
        lambda d, adv: d.name and "polar" in d.name.lower())


async def collect_into(name, queue, n, frames):
    """Append up to n frames to `frames`, tagging each with local arrival
    time (monotonic clock, for measuring inter-arrival latency). Frames
    already appended survive even if this coroutine is cancelled/timed
    out partway through."""
    while len(frames) < n:
        frame = await queue.get()
        frames.append((frame, time.monotonic()))
        print(f"  [{name}] {len(frames)}/{n}: {frame}")


async def collect(name, queue, n, timeout):
    """Collect up to n frames from queue, each stream timing out on its
    own so a slow/silent stream doesn't discard another stream's results."""
    frames = []
    try:
        await asyncio.wait_for(collect_into(name, queue, n, frames), timeout=timeout)
    except asyncio.TimeoutError:
        print(f"  [{name}] timed out after {timeout}s, got {len(frames)}/{n}")
        if name in ("ECG", "ACC") and len(frames) == 0:
            print(f"  [{name}] no PMD data arrived at all - this is the known")
            print(f"  [{name}] H10 stuck-session issue (see module docstring).")
            print(f"  [{name}] Try: take the strap off skin for ~60s, then retry.")
    return frames


def report(name, frames):
    if len(frames) < 2:
        print(f"{name}: only {len(frames)} frame(s), not enough for timing stats")
        return
    deltas = [b[1] - a[1] for a, b in zip(frames, frames[1:])]
    print(f"{name}: inter-arrival  min={min(deltas) * 1000:6.1f}ms  "
          f"avg={statistics.mean(deltas) * 1000:6.1f}ms  "
          f"max={max(deltas) * 1000:6.1f}ms")


async def main():
    device = await scan()
    if device is None:
        print("Polar device not found. Is it worn with moistened electrodes?")
        return

    print(f"Connecting to {device}...")
    t_connect_start = time.monotonic()
    # Force a fresh (uncached) GATT service query - after several
    # connect/disconnect cycles Windows can serve a stale cached service
    # list, which is one suspected cause of PMD notifications silently
    # not arriving even though control-point commands succeed.
    async with BleakClient(device, winrt=dict(use_cached_services=False)) as client:
        print(f"Connected in {(time.monotonic() - t_connect_start) * 1000:.0f} ms")

        hr_queue = asyncio.Queue()
        ecg_queue = asyncio.Queue()
        acc_queue = asyncio.Queue()

        heartrate = HeartRate(client, queue=hr_queue, instant_rate=True, unpack=True)
        pmd = PolarMeasurementData(client, ecg_queue=ecg_queue, acc_queue=acc_queue)

        print("ECG settings:", await pmd.available_settings('ECG'))
        print("ACC settings:", await pmd.available_settings('ACC'))

        # Small gaps between enabling notifications on three separate
        # characteristics in quick succession, in case Windows drops a
        # CCCD write when they arrive back-to-back.
        await heartrate.start_notify()
        await asyncio.sleep(0.5)
        err, msg, _ = await pmd.start_streaming('ECG')
        if err:
            print(f"ECG start failed: {msg}")
        await asyncio.sleep(0.5)
        err, msg, _ = await pmd.start_streaming('ACC', RANGE=2, SAMPLE_RATE=25)
        if err:
            print(f"ACC start failed: {msg}")

        try:
            t0 = time.monotonic()
            hr_frames, ecg_frames, acc_frames = await asyncio.gather(
                collect('HR', hr_queue, SAMPLES_PER_STREAM, STREAM_TIMEOUT_S),
                collect('ECG', ecg_queue, SAMPLES_PER_STREAM, STREAM_TIMEOUT_S),
                collect('ACC', acc_queue, SAMPLES_PER_STREAM, STREAM_TIMEOUT_S),
            )
            t1 = time.monotonic()
        finally:
            # Always stop notifications/streaming, even on error or Ctrl+C,
            # so this script never leaves the H10/Windows GATT session
            # dangling for the *next* run (see module docstring).
            if client.is_connected:
                await heartrate.stop_notify()
                await pmd.stop_streaming('ECG')
                await pmd.stop_streaming('ACC')

    print()
    print(f"Total time to gather {SAMPLES_PER_STREAM} frames/stream: {(t1 - t0) * 1000:.0f} ms")
    report('HR', hr_frames)
    report('ECG', ecg_frames)
    report('ACC', acc_frames)


if __name__ == "__main__":
    asyncio.run(main())
