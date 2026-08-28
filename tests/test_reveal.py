"""EW-89: getting the participant from "I played" to "the file is attached".

Every step between those two costs recordings, and the recorder cannot make up
for a file that is never sent. Asking somebody mid-week to navigate to a folder
they chose once, weeks ago, is one such step - so the tray opens it for them.
"""

from __future__ import annotations

from pathlib import Path

from riftrec.app.reveal import open_location


class _Spy:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def __call__(self, path) -> None:
        self.calls.append(Path(path))


def test_an_existing_recording_is_selected_in_its_folder(tmp_path) -> None:
    """Selecting the file beats opening the folder: after a few weeks the folder
    holds a dozen recordings and the participant has to pick the right one."""
    db = tmp_path / "P01_2026-08-28_092036.sqlite"
    db.write_bytes(b"")
    select, folder = _Spy(), _Spy()

    assert open_location(db, select=select, open_folder=folder) is True
    assert select.calls == [db]
    assert folder.calls == []


def test_without_a_recording_the_folder_itself_opens(tmp_path) -> None:
    """A run that has not seen a match yet has no file - opening the empty
    folder still answers "where will it be?"."""
    db = tmp_path / "not-yet.sqlite"
    select, folder = _Spy(), _Spy()

    assert open_location(db, select=select, open_folder=folder) is True
    assert select.calls == []
    assert folder.calls == [tmp_path]


def test_a_folder_path_opens_directly(tmp_path) -> None:
    select, folder = _Spy(), _Spy()
    assert open_location(tmp_path, select=select, open_folder=folder) is True
    assert folder.calls == [tmp_path]


def test_a_path_string_works_like_a_path(tmp_path) -> None:
    """The tray hands over what the status source carries, which is a string."""
    db = tmp_path / "rec.sqlite"
    db.write_bytes(b"")
    select = _Spy()
    assert open_location(str(db), select=select, open_folder=_Spy()) is True
    assert select.calls == [db]


def test_nothing_to_open_is_not_an_error() -> None:
    assert open_location(None) is False


def test_a_vanished_folder_is_reported_not_raised(tmp_path) -> None:
    """An unplugged drive must not take the tray down with it."""
    gone = tmp_path / "gone" / "rec.sqlite"
    assert open_location(gone, select=_Spy(), open_folder=_Spy()) is False


def test_a_failing_file_manager_never_escapes(tmp_path) -> None:
    """This runs on strangers' PCs; a shell that refuses to start is not a
    reason to interrupt a recording."""
    db = tmp_path / "rec.sqlite"
    db.write_bytes(b"")

    def boom(_path):
        raise OSError("shell not available")

    assert open_location(db, select=boom, open_folder=boom) is False


# -- the tray menu entry ---------------------------------------------------

def test_the_tray_opens_the_folder_the_recording_goes_to(monkeypatch, tmp_path) -> None:
    """Wiring check: the menu entry has to hand over the path the recorder is
    actually writing to, not the working directory."""
    from riftrec.app import tray as tray_module

    db = tmp_path / "P01_2026-08-28_092036.sqlite"
    opened: list = []
    monkeypatch.setattr(tray_module, "open_location",
                        lambda target: opened.append(target))

    controller = tray_module.TrayController.__new__(tray_module.TrayController)
    controller._status_source = lambda: {"db_path": str(db)}
    controller._open_folder()

    assert opened == [str(db)]


def test_the_menu_offers_the_entry_before_anything_can_go_wrong() -> None:
    """It must exist even in a run that has not recorded anything yet - that is
    when somebody is most likely to go looking."""
    import inspect

    from riftrec.app import tray as tray_module

    source = inspect.getsource(tray_module.TrayController.__init__)
    assert '"Open data folder"' in source
    assert "self._open_folder" in source
