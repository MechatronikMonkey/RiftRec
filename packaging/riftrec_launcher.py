"""Frozen entry point for the packaged RiftRec (EW-89).

PyInstaller freezes this file rather than ``riftrec/__main__.py``: a bundled exe
is started with no arguments (double-click, Start menu), so the default has to be
the tray recorder rather than argparse's "command required" error, which a
windowed build cannot even display.

It also works from a source checkout - running it puts this folder on sys.path,
so the repo root is added explicitly.
"""

from __future__ import annotations

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from riftrec.cli import main  # noqa: E402  (after the sys.path fix-up)


def _argv() -> list[str]:
    """Default to `gui`, but keep `selfcheck` and `record …` reachable."""
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        return ["gui", *args]
    return args


if __name__ == "__main__":
    main(_argv())
