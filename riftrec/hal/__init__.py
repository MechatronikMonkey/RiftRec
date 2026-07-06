"""HAL - Hardware Abstraction Layer for BLE access.

The HAL boundary is deliberately the *BLE transport* (scan, connect, notify on /
write to characteristics) - NOT the Polar semantics. A later switch from the
Windows Bleak stack to a USB dongle (nrf52840 + Bumble) swaps only the host BLE
stack, not the Polar GATT protocol. So everything Polar-specific (HR/RR parsing,
later PMD/ECG/ACC) lives ABOVE this layer in the H10Source; only the raw BLE
primitives sit behind it.
"""

from .ble import BleTransport

__all__ = ["BleTransport"]
