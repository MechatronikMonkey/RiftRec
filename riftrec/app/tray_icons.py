"""Tray icon image + label per recorder state (pure, no I/O - unit-testable)."""

from __future__ import annotations

from ..rte.state import RecorderState

_COLORS: dict[RecorderState, str] = {
    RecorderState.IDLE: "#9e9e9e",
    RecorderState.CONNECTING: "#f5a623",   # amber
    RecorderState.READY: "#2ca02c",        # green - connected, waiting
    RecorderState.RECORDING: "#d62728",    # red - like a camera rec light
    RecorderState.STOPPED: "#607d8b",      # slate
    RecorderState.ERROR: "#8e44ad",        # purple - distinct from rec-red
}

_TITLES: dict[RecorderState, str] = {
    RecorderState.IDLE: "RiftRec — idle",
    RecorderState.CONNECTING: "RiftRec — connecting…",
    RecorderState.READY: "RiftRec — ready (connected, waiting for match)",
    RecorderState.RECORDING: "RiftRec — recording (match live)",
    RecorderState.STOPPED: "RiftRec — stopped",
    RecorderState.ERROR: "RiftRec — error",
}


def color_for(state: RecorderState) -> str:
    return _COLORS.get(state, "#9e9e9e")


def title_for(state: RecorderState) -> str:
    return _TITLES.get(state, "RiftRec")


def make_icon(state: RecorderState, size: int = 64):
    """A filled status dot as a PIL image (transparent background)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = size // 8
    draw.ellipse([pad, pad, size - pad, size - pad], fill=color_for(state))
    return img
