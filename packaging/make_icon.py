"""Generate the application icon from the tray palette (EW-89).

The .ico is derived rather than committed: it is the same green "ready" dot the
tray already draws, so the taskbar, the installer and the notification area
cannot drift apart, and the repository stays free of binary assets.

    python packaging/make_icon.py [out.ico]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from riftrec.rte.state import RecorderState  # noqa: E402
from riftrec.app.tray_icons import color_for  # noqa: E402

# Windows picks the size it needs per context (taskbar, alt-tab, installer
# header), and scales badly from a single bitmap - so ship all of them.
SIZES = [16, 24, 32, 48, 64, 128, 256]
DEFAULT_OUT = Path(__file__).with_name("riftrec.ico")


def render(size: int) -> Image.Image:
    """A filled dot on a dark rounded tile - readable on light and dark taskbars."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = max(2, size // 5)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill="#1e2430")
    pad = max(2, size // 4)
    draw.ellipse([pad, pad, size - pad - 1, size - pad - 1],
                 fill=color_for(RecorderState.READY))
    return img


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    base = render(256)
    base.save(out, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"wrote {out} ({', '.join(str(s) for s in SIZES)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
