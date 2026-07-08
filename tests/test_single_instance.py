"""Single-instance guard (EW-43): a second lock on the same file is refused
while the first is held, and granted again once it is released."""

from __future__ import annotations

import tempfile
from pathlib import Path

from riftrec.app.single_instance import _lock_file


def test_second_lock_refused_until_first_released() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "riftrec.lock"

        fh1, s1 = _lock_file(path)
        assert s1 == "ok" and fh1 is not None

        # A second attempt while the first is held must be refused.
        fh2, s2 = _lock_file(path)
        assert s2 == "locked" and fh2 is None

        # Release the first; the lock is now free again.
        fh1.close()
        fh3, s3 = _lock_file(path)
        assert s3 == "ok" and fh3 is not None
        fh3.close()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK - {name}")
    print("OK - all single-instance tests passed")
