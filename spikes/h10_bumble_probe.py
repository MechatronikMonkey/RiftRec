"""Spike: talk to the Polar H10 through Google's Bumble user-space BLE
stack instead of the Windows (WinRT) stack. Same shape as h10_ping.py:
pull a handful of frames from every stream (HR/RR, ECG, ACC), print
inter-arrival stats. NOT a continuous recorder.

Why: on Windows, PMD (ECG/ACC) notifications only arrive on the very
first connection after a fresh pairing (see h10_ping.py docstring and
memory note riftrec-h10-pmd-windows-bug). Bumble runs pairing, the
security manager and the GATT client entirely in this Python process
and owns its controller exclusively, so the WinRT reconnect bug cannot
occur by construction. This spike exists to prove that claim on real
hardware. This script deliberately does NOT persist bond keys, so every
run is a fresh pairing - the one scenario known to work even on WinRT.

Hardware prerequisites (one of):
  - Generic BT USB dongle with the WinUSB driver assigned via Zadig
    (https://google.github.io/bumble/platforms/windows.html), or
  - nRF52840 dongle flashed with Zephyr HCI-UART-over-CDC-ACM firmware,
    which enumerates as a plain COM port (no driver swap needed).

Software prerequisites: pip install bumble

Usage:
  python h10_bumble_probe.py usb:0                # Zadig/WinUSB dongle
  python h10_bumble_probe.py serial:COM7@1000000  # nRF52840 CDC-ACM

IMPORTANT before running: the H10 accepts only ONE central at a time.
If the strap is still paired to Windows' own Bluetooth, Windows may
auto-connect to it first and this spike will never see it in the scan.
Either remove the H10 in Windows Settings -> Bluetooth & devices, or
turn Windows Bluetooth off entirely while testing.

Status 2026-07-05: written ahead of hardware - not yet validated with a
real dongle. Expect small API breakage against future bumble releases.
"""

import asyncio
import statistics
import sys
import time

from bumble.core import UUID, AdvertisingData
from bumble.device import Device, Peer
from bumble.pairing import PairingConfig, PairingDelegate

try:
    from bumble.transport import open_transport
except ImportError:  # older bumble releases
    from bumble.transport import open_transport_or_link as open_transport

SAMPLES_PER_STREAM = 3
STREAM_TIMEOUT_S = 15
SCAN_TIMEOUT_S = 30
CP_RESPONSE_TIMEOUT_S = 10

# Local (dongle) identity - random static address, name is cosmetic.
LOCAL_NAME = 'RiftRec Spike'
LOCAL_ADDRESS = 'F0:F1:F2:F3:F4:F5'

# Standard Heart Rate service (works without pairing, our known-good baseline)
HR_MEASUREMENT_UUID = UUID('00002A37-0000-1000-8000-00805F9B34FB')

# Polar Measurement Data (PMD) - proprietary, needs an encrypted link
PMD_CONTROL_UUID = UUID('FB005C81-02E7-F387-1CAD-8ACD2D8DF0C8')
PMD_DATA_UUID = UUID('FB005C82-02E7-F387-1CAD-8ACD2D8DF0C8')

PMD_MEASUREMENT_TYPES = {0x00: 'ECG', 0x02: 'ACC'}
PMD_CP_STATUS = {
    0x00: 'SUCCESS',
    0x01: 'INVALID OP CODE',
    0x02: 'INVALID MEASUREMENT TYPE',
    0x03: 'NOT SUPPORTED',
    0x04: 'INVALID LENGTH',
    0x05: 'INVALID PARAMETER',
    0x06: 'ALREADY IN STATE',
    0x07: 'INVALID RESOLUTION',
    0x08: 'INVALID SAMPLE RATE',
    0x09: 'INVALID RANGE',
    0x0A: 'INVALID MTU',
    0x0B: 'INVALID NUMBER OF CHANNELS',
    0x0C: 'INVALID STATE',
    0x0D: 'DEVICE IN CHARGER',
}

# Control-point commands (opcode, measurement type, then setting TLVs as
# <setting_id> <array_len> <uint16 value>). Same values bleakheart used:
# ECG fixed 130 Hz / 14 bit, ACC 25 Hz / 16 bit / 2 G.
CMD_GET_ECG_SETTINGS = bytes([0x01, 0x00])
CMD_GET_ACC_SETTINGS = bytes([0x01, 0x02])
CMD_START_ECG = bytes([0x02, 0x00,
                       0x00, 0x01, 0x82, 0x00,   # SAMPLE_RATE = 130 Hz
                       0x01, 0x01, 0x0E, 0x00])  # RESOLUTION  = 14 bit
CMD_START_ACC = bytes([0x02, 0x02,
                       0x00, 0x01, 0x19, 0x00,   # SAMPLE_RATE = 25 Hz
                       0x01, 0x01, 0x10, 0x00,   # RESOLUTION  = 16 bit
                       0x02, 0x01, 0x02, 0x00])  # RANGE       = 2 G
CMD_STOP_ECG = bytes([0x03, 0x00])
CMD_STOP_ACC = bytes([0x03, 0x02])


def parse_hr(data):
    """Standard BLE Heart Rate Measurement -> ('HR', bpm, [rr_ms, ...])."""
    flags = data[0]
    if flags & 0x01:
        bpm = int.from_bytes(data[1:3], 'little')
        offset = 3
    else:
        bpm = data[1]
        offset = 2
    if flags & 0x08:  # energy expended present, skip
        offset += 2
    rr = []
    if flags & 0x10:
        while offset + 1 < len(data):
            rr.append(round(int.from_bytes(data[offset:offset + 2], 'little')
                            / 1024 * 1000))
            offset += 2
    return ('HR', bpm, rr)


def parse_pmd_frame(data):
    """PMD data frame -> ('ECG'|'ACC', device_timestamp_ns, n_samples,
    first_sample). Only the two frame layouts the H10 actually sends for
    our settings are handled (raw ECG, 16-bit ACC)."""
    mtype = PMD_MEASUREMENT_TYPES.get(data[0], f'0x{data[0]:02X}')
    timestamp = int.from_bytes(data[1:9], 'little')
    frame_type = data[9]
    payload = data[10:]
    if mtype == 'ECG' and frame_type == 0x00:
        # 3-byte signed samples, microvolts
        n = len(payload) // 3
        first = int.from_bytes(payload[0:3], 'little', signed=True) if n else None
        return (mtype, timestamp, n, f'{first} uV')
    if mtype == 'ACC' and frame_type == 0x01:
        # int16 x/y/z triplets, milli-g
        n = len(payload) // 6
        first = tuple(int.from_bytes(payload[i:i + 2], 'little', signed=True)
                      for i in (0, 2, 4)) if n else None
        return (mtype, timestamp, n, f'{first} mG')
    return (mtype, timestamp, len(payload), f'unparsed frame type {frame_type}')


async def scan(device):
    """Return the address of the first advertising Polar device."""
    print('Scanning for Polar device...')
    found = asyncio.get_running_loop().create_future()

    def on_advertisement(adv):
        name = (adv.data.get(AdvertisingData.COMPLETE_LOCAL_NAME)
                or adv.data.get(AdvertisingData.SHORTENED_LOCAL_NAME))
        if name and 'polar' in name.lower() and not found.done():
            found.set_result((adv.address, name))

    device.on('advertisement', on_advertisement)
    await device.start_scanning(active=True)
    try:
        return await asyncio.wait_for(found, SCAN_TIMEOUT_S)
    except asyncio.TimeoutError:
        return (None, None)
    finally:
        await device.stop_scanning()
        device.remove_listener('advertisement', on_advertisement)


async def pmd_command(cp_char, cp_queue, label, command):
    """Write a PMD control-point command and wait for its response
    indication. Returns the response payload (after the status byte),
    or None on error/timeout - unlike WinRT, we want every failure
    loud and attributable."""
    await cp_char.write_value(command, with_response=True)
    try:
        while True:
            rsp = await asyncio.wait_for(cp_queue.get(), CP_RESPONSE_TIMEOUT_S)
            if rsp[0] == 0xF0 and rsp[1] == command[0] and rsp[2] == command[1]:
                break
            print(f'  [{label}] ignoring unrelated CP frame: {rsp.hex(" ")}')
    except asyncio.TimeoutError:
        print(f'  [{label}] NO control-point response within '
              f'{CP_RESPONSE_TIMEOUT_S}s - write was acked but the sensor '
              f'never answered')
        return None
    status = rsp[3]
    status_text = PMD_CP_STATUS.get(status, f'unknown 0x{status:02X}')
    print(f'  [{label}] {status_text} (raw response: {rsp.hex(" ")})')
    return rsp[4:] if status == 0x00 else None


async def collect_into(name, queue, n, frames):
    while len(frames) < n:
        frame = await queue.get()
        frames.append((frame, time.monotonic()))
        print(f'  [{name}] {len(frames)}/{n}: {frame}')


async def collect(name, queue, n, timeout):
    """Collect up to n frames, each stream timing out on its own so a
    silent stream doesn't discard another stream's results."""
    frames = []
    try:
        await asyncio.wait_for(collect_into(name, queue, n, frames), timeout=timeout)
    except asyncio.TimeoutError:
        print(f'  [{name}] timed out after {timeout}s, got {len(frames)}/{n}')
    return frames


def report(name, frames):
    if len(frames) < 2:
        print(f'{name}: only {len(frames)} frame(s), not enough for timing stats')
        return
    deltas = [b[1] - a[1] for a, b in zip(frames, frames[1:])]
    print(f'{name}: inter-arrival  min={min(deltas) * 1000:6.1f}ms  '
          f'avg={statistics.mean(deltas) * 1000:6.1f}ms  '
          f'max={max(deltas) * 1000:6.1f}ms')


async def main():
    if len(sys.argv) != 2:
        print(__doc__.split('Usage:')[1].split('IMPORTANT')[0])
        return
    transport_spec = sys.argv[1]

    print(f'Opening HCI transport {transport_spec!r}...')
    async with await open_transport(transport_spec) as hci_transport:
        device = Device.with_hci(LOCAL_NAME, LOCAL_ADDRESS,
                                 hci_transport.source, hci_transport.sink)
        # Just-works pairing, no key persistence: every run of this spike
        # is a fresh pairing on purpose (see module docstring).
        device.pairing_config_factory = lambda connection: PairingConfig(
            sc=True, mitm=False, bonding=True,
            delegate=PairingDelegate(
                io_capability=PairingDelegate.NO_OUTPUT_NO_INPUT))
        await device.power_on()

        address, name = await scan(device)
        if address is None:
            print('Polar device not found. Worn with moistened electrodes? '
                  'Windows Bluetooth off / H10 unpaired from Windows?')
            return

        print(f'Connecting to {name} ({address})...')
        t_connect_start = time.monotonic()
        connection = await device.connect(address)
        print(f'Connected in {(time.monotonic() - t_connect_start) * 1000:.0f} ms')

        try:
            print('Pairing (just works, no stored bond)...')
            await connection.pair()
            print(f'Paired, link encrypted: {connection.is_encrypted}')

            peer = Peer(connection)
            # Raw ECG frames run up to ~229 bytes; the default ATT MTU of
            # 23 would truncate them.
            mtu = await peer.request_mtu(247)
            print(f'ATT MTU: {mtu}')
            await peer.discover_services()
            await peer.discover_characteristics()

            def required_characteristic(uuid, label):
                chars = peer.get_characteristics_by_uuid(uuid)
                if not chars:
                    raise RuntimeError(f'{label} characteristic not found - '
                                       f'is this really an H10?')
                return chars[0]

            hr_char = required_characteristic(HR_MEASUREMENT_UUID, 'HR measurement')
            cp_char = required_characteristic(PMD_CONTROL_UUID, 'PMD control point')
            data_char = required_characteristic(PMD_DATA_UUID, 'PMD data')

            hr_queue = asyncio.Queue()
            ecg_queue = asyncio.Queue()
            acc_queue = asyncio.Queue()
            cp_queue = asyncio.Queue()

            def on_pmd_data(value):
                frame = parse_pmd_frame(bytes(value))
                (ecg_queue if frame[0] == 'ECG' else acc_queue).put_nowait(frame)

            # Order matters: Polar requires the control-point indication
            # subscription BEFORE any command is written, or responses
            # (and possibly the whole stream setup) are lost.
            await cp_char.subscribe(lambda value: cp_queue.put_nowait(bytes(value)))
            await data_char.subscribe(on_pmd_data)
            await hr_char.subscribe(lambda value: hr_queue.put_nowait(parse_hr(bytes(value))))

            await pmd_command(cp_char, cp_queue, 'ECG settings', CMD_GET_ECG_SETTINGS)
            await pmd_command(cp_char, cp_queue, 'ACC settings', CMD_GET_ACC_SETTINGS)
            await pmd_command(cp_char, cp_queue, 'ECG start', CMD_START_ECG)
            await pmd_command(cp_char, cp_queue, 'ACC start', CMD_START_ACC)

            t0 = time.monotonic()
            hr_frames, ecg_frames, acc_frames = await asyncio.gather(
                collect('HR', hr_queue, SAMPLES_PER_STREAM, STREAM_TIMEOUT_S),
                collect('ECG', ecg_queue, SAMPLES_PER_STREAM, STREAM_TIMEOUT_S),
                collect('ACC', acc_queue, SAMPLES_PER_STREAM, STREAM_TIMEOUT_S),
            )
            t1 = time.monotonic()

            await pmd_command(cp_char, cp_queue, 'ECG stop', CMD_STOP_ECG)
            await pmd_command(cp_char, cp_queue, 'ACC stop', CMD_STOP_ACC)
        finally:
            await connection.disconnect()

    print()
    print(f'Total time to gather {SAMPLES_PER_STREAM} frames/stream: '
          f'{(t1 - t0) * 1000:.0f} ms')
    report('HR', hr_frames)
    report('ECG', ecg_frames)
    report('ACC', acc_frames)
    if ecg_frames and acc_frames:
        print()
        print('PMD data arrived through Bumble: the WinRT bypass works.')
        print('Next: rerun this several times back-to-back to prove the')
        print('reconnect case (the exact scenario that is broken on WinRT).')


if __name__ == '__main__':
    asyncio.run(main())
