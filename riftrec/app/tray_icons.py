"""Tray icon image per recorder state (pure, no I/O - unit-testable).

Colour only. The words that go with a state live in `rte/status.py`, which
names the reason rather than the state alone (EW-89) - keeping a second set
of labels here is how the two would drift apart.
"""

from __future__ import annotations

from typing import Optional

from ..rte.state import RecorderState

_COLORS: dict[RecorderState, str] = {
    RecorderState.IDLE: "#9e9e9e",
    RecorderState.CONNECTING: "#f5a623",   # amber
    RecorderState.READY: "#2ca02c",        # green - connected, waiting
    RecorderState.RECORDING: "#d62728",    # red - like a camera rec light
    RecorderState.WARNING: "#e67e22",      # orange - drawn as a triangle below
    RecorderState.STOPPED: "#607d8b",      # slate
    RecorderState.ERROR: "#8e44ad",        # purple - distinct from rec-red
}


def color_for(state: RecorderState) -> str:
    return _COLORS.get(state, "#9e9e9e")


# Below this the participant should change the coin cell. Set generously: the
# point is to replace it during a quiet moment, not to squeeze out the last
# percent and lose a session to a dead strap mid-match.
BATTERY_WARN_PCT = 30


def battery_text(pct: Optional[int]) -> str:
    """Menu/tooltip wording for the strap's battery level."""
    if pct is None:
        return "Battery: unknown"
    if pct <= BATTERY_WARN_PCT:
        return f"Battery: {pct}% - replace soon"
    return f"Battery: {pct}%"


def make_icon(state: RecorderState, size: int = 64):
    """A status shape as a PIL image (transparent background).

    WARNING is a triangle, every other state a dot. Shape rather than only
    colour, because at 16 px in a crowded notification area two similar
    colours are indistinguishable - and "recording, but the data is
    useless" is the one state that must not look like the others (EW-89).
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = size // 8
    fill = color_for(state)
    if state is RecorderState.WARNING:
        draw.polygon(
            [(size / 2, pad), (size - pad, size - pad), (pad, size - pad)],
            fill=fill,
        )
    else:
        draw.ellipse([pad, pad, size - pad, size - pad], fill=fill)
    return img
