"""HAL - Hardware Abstraction Layer für den BLE-Zugriff.

Die HAL-Grenze ist bewusst der *BLE-Transport* (scannen, verbinden, auf
Characteristics notifien/schreiben) - NICHT die Polar-Semantik. Ein späterer
Wechsel vom Windows-Bleak-Stack auf einen USB-Dongle (nrf52840 + Bumble)
tauscht nur den Host-BLE-Stack, nicht das Polar-GATT-Protokoll. Deshalb liegt
alles Polar-Spezifische (HR-/RR-Parsing, später PMD/ECG/ACC) ÜBER dieser
Schicht in der H10Source; nur die rohen BLE-Primitive stecken dahinter.
"""

from .ble import BleTransport

__all__ = ["BleTransport"]
