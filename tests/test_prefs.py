"""Persisted pilot prefs (EW-43): participant id + storage folder survive a run,
and a missing/corrupt file never blocks recording."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from riftrec.app.prefs import Prefs, load_prefs, save_prefs


def _with_appdata(tmp: str):
    """Point prefs at a temp APPDATA and restore the old value after."""
    old = os.environ.get("APPDATA")
    os.environ["APPDATA"] = tmp
    return old


def _restore_appdata(old) -> None:
    if old is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = old


def test_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        old = _with_appdata(tmp)
        try:
            save_prefs(Prefs(participant_id="P07", storage_folder=r"D:\riftrec\data"))
            got = load_prefs()
            assert got.participant_id == "P07"
            assert got.storage_folder == r"D:\riftrec\data"
        finally:
            _restore_appdata(old)


def test_missing_file_returns_empty_defaults() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        old = _with_appdata(tmp)
        try:
            got = load_prefs()  # nothing saved yet
            assert got.participant_id is None
            assert got.storage_folder is None
        finally:
            _restore_appdata(old)


def test_corrupt_file_does_not_raise() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        old = _with_appdata(tmp)
        try:
            path = Path(tmp) / "RiftRec" / "prefs.ini"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("this is not [valid ini", encoding="utf-8")
            got = load_prefs()  # must swallow the parse error
            assert got.participant_id is None and got.storage_folder is None
        finally:
            _restore_appdata(old)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK - {name}")
    print("OK - all prefs tests passed")
