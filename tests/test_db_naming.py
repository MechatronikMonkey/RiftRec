"""unique_db_path: one fresh, collision-proof .sqlite file per recording run.

The core data-safety guarantee: the recorder must never reuse or overwrite an
existing recording. The user only picks a folder; the filename is minted here.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from riftrec.storage.sqlite_sink import unique_db_path


def test_path_is_inside_folder_and_free() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = unique_db_path(tmp, "P01")
        assert p.parent == Path(tmp)
        assert p.suffix == ".sqlite"
        assert p.name.startswith("P01_")
        assert not p.exists()  # never returns an existing file


def test_never_collides_with_existing_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p1 = unique_db_path(tmp, "P01")
        p1.write_bytes(b"")  # simulate a run that already wrote this second
        p2 = unique_db_path(tmp, "P01")
        assert p2 != p1
        assert not p2.exists()
        # A third collision keeps climbing the suffix, still never reusing.
        p2.write_bytes(b"")
        p3 = unique_db_path(tmp, "P01")
        assert p3 not in (p1, p2)


def test_missing_participant_falls_back_to_session() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = unique_db_path(tmp, None)
        assert p.name.startswith("session_")


def test_participant_is_sanitised_for_filesystem() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = unique_db_path(tmp, "a/b c:d")
        assert "/" not in p.name and ":" not in p.name and " " not in p.name


def test_creates_missing_folder() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "does" / "not" / "exist"
        p = unique_db_path(target, "P01")
        assert p.parent.is_dir()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK - {name}")
    print("OK - all db-naming tests passed")
