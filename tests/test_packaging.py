"""EW-89: the installer must keep matching the app it installs.

The build itself is verified by building it (packaging/build.ps1 and the CI
workflow run `RiftRec.exe selfcheck` against the frozen result). What these
tests cover is the drift that a green build would not notice: a version bumped
in one place only, a data file that stopped being bundled, a dependency added
without a hidden import, or the deliberate design decisions in the .iss being
quietly undone.
"""

from __future__ import annotations

from pathlib import Path

import riftrec
from riftrec.cli import _selfcheck

ROOT = Path(__file__).resolve().parents[1]
ISS = (ROOT / "packaging" / "riftrec.iss").read_text(encoding="utf-8")
SPEC = (ROOT / "packaging" / "riftrec.spec").read_text(encoding="utf-8")
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
RELEASE = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
BUILD_ACTION = (ROOT / ".github" / "actions" / "build-installer" / "action.yml").read_text(encoding="utf-8")
RULES = (ROOT / ".github" / "setup-repo-rules.ps1").read_text(encoding="utf-8")
RULESET_DIR = ROOT / ".github" / "rulesets"


def _ruleset(name: str) -> dict:
    import json

    return json.loads((RULESET_DIR / name).read_text(encoding="utf-8"))


def _rule(ruleset: dict, kind: str) -> dict:
    """The one rule of a given type, so a missing rule fails loudly."""
    matches = [r for r in ruleset["rules"] if r["type"] == kind]
    assert len(matches) == 1, (kind, ruleset["name"])
    return matches[0]
LAUNCHER = (ROOT / "packaging" / "riftrec_launcher.py").read_text(encoding="utf-8")


# -- the app can actually start ---------------------------------------------

def test_selfcheck_passes_from_source() -> None:
    """The same check the frozen build runs. If it fails here, it fails there."""
    assert _selfcheck() == 0


def test_selfcheck_covers_every_riftrec_module_the_recorder_uses() -> None:
    """A new module has to join the list, or a frozen build can drop it and the
    only symptom is a window that never opens."""
    from riftrec.cli import _RUNTIME_MODULES

    listed = set(_RUNTIME_MODULES)
    for package in ("app", "rte", "sources", "storage", "hal"):
        for module in (ROOT / "riftrec" / package).glob("*.py"):
            if module.stem in ("__init__", "base", "fake", "ble", "schema"):
                continue     # protocols and test doubles carry no runtime imports
            assert f"riftrec.{package}.{module.stem}" in listed, module


# -- installer stays in step with the app -----------------------------------

def test_iss_fallback_version_matches_the_package() -> None:
    """A bare ISCC run must not label the installer with a stale version."""
    assert f'#define AppVersion "{riftrec.__version__}"' in ISS


def test_iss_installs_what_pyinstaller_produces() -> None:
    """The .iss picks up the exact folder name the spec's COLLECT writes."""
    assert 'name="RiftRec"' in SPEC
    assert r'Source: "..\dist\RiftRec\*"' in ISS
    assert r'Filename: "{app}\{#AppExe}"' in ISS


def test_installer_is_per_user_so_no_admin_prompt_is_needed() -> None:
    """Participants install on their own PCs; a UAC prompt is a drop-off point."""
    assert "PrivilegesRequired=lowest" in ISS


def test_installer_can_be_signed_later_without_touching_the_script() -> None:
    """Certificate lead time is weeks - the build must not wait for it (EW-89)."""
    assert "#ifdef SIGN" in ISS
    assert "SignTool=signtool" in ISS
    assert "SignedUninstaller=yes" in ISS


# -- decisions that must not be undone by accident --------------------------

def test_no_autostart_entry_is_registered() -> None:
    """Recording stays a conscious act: the participant has to put the strap on,
    so starting RiftRec belongs to the same decision. No logon entry."""
    assert "[Registry]" not in ISS
    assert "CurrentVersion\\Run" not in ISS


def test_uninstall_removes_the_app_but_never_the_recordings() -> None:
    """EW-52: nothing recorded is ever deleted - not even by an uninstall.

    Checks the [UninstallDelete] directives themselves rather than the file
    text, so a comment mentioning a filename cannot pass or fail this.
    """
    deletes = [ln.strip() for ln in ISS.splitlines() if ln.strip().startswith("Type:")]
    assert any("prefs.ini" in ln for ln in deletes)
    for line in deletes:
        assert ".sqlite" not in line, line       # recordings, never
        assert "riftrec.log" not in line, line   # the log explains the uninstall


def test_build_is_one_folder_not_one_file() -> None:
    """A one-file build unpacks to %TEMP% on every launch - the write pattern
    already suspected behind the stuttering in EW-51."""
    assert "COLLECT(" in SPEC
    assert "upx=False" in SPEC


# -- things a frozen build silently drops -----------------------------------

def test_schema_is_bundled() -> None:
    """schema.sql is read from disk at runtime and is the contract to RiftLab."""
    assert "schema.sql" in SPEC
    assert (ROOT / "riftrec" / "storage" / "schema.sql").exists()


def test_hidden_imports_cover_the_dynamic_backends() -> None:
    """bleak, pystray and PIL choose their backend at runtime, so a static
    analysis does not see them - the classic missing-module failure."""
    for module in (
        "pystray._win32",
        "PIL._tkinter_finder",
        "bleak.backends.winrt.client",
        "bleak.backends.winrt.scanner",
        "winrt.windows.devices.bluetooth",
        "winrt.windows.devices.bluetooth.genericattributeprofile",
        "winrt.windows.devices.enumeration",
        "winrt.windows.foundation",
        "winrt.windows.storage.streams",
    ):
        assert f'"{module}"' in SPEC, module


def test_every_runtime_requirement_is_in_the_lean_requirements_file() -> None:
    """requirements-recorder.txt is what both build paths install."""
    reqs = (ROOT / "requirements-recorder.txt").read_text(encoding="utf-8").lower()
    for package in ("bleak", "httpx", "pystray", "pillow"):
        assert package in reqs, package


# -- the entry point --------------------------------------------------------

def test_launcher_defaults_to_the_tray_recorder() -> None:
    """A double-clicked exe gets no arguments, and a windowed build cannot show
    argparse's "command required" error - so `gui` has to be the default."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_riftrec_launcher", ROOT / "packaging" / "riftrec_launcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    import sys

    original = sys.argv
    try:
        sys.argv = ["RiftRec.exe"]
        assert module._argv() == ["gui"]
        sys.argv = ["RiftRec.exe", "selfcheck"]
        assert module._argv() == ["selfcheck"]
        sys.argv = ["RiftRec.exe", "--some-flag"]
        assert module._argv() == ["gui", "--some-flag"]
    finally:
        sys.argv = original


# -- CI does the same thing ------------------------------------------------

def test_the_build_steps_exist_once_and_both_workflows_use_them() -> None:
    """CI and release must not grow separate build paths - the installer people
    download has to be produced by the steps that were already green on the PR."""
    for step in ("riftrec.spec", "riftrec.iss", "selfcheck", "make_icon.py"):
        assert step in BUILD_ACTION, step
    for workflow in (CI, RELEASE):
        assert "./.github/actions/build-installer" in workflow
        assert "pyinstaller --noconfirm" not in workflow  # only in the action


def test_both_workflows_run_the_test_suite() -> None:
    """An installer must never be built from a red suite."""
    assert "pytest tests/" in CI
    assert "pytest tests/" in RELEASE


def test_release_refuses_a_tag_that_disagrees_with_the_package_version() -> None:
    """Every recording stores app_version, so a mislabelled installer makes it
    impossible to tell later which build produced which data."""
    assert "__version__" in RELEASE
    assert "does not match" in RELEASE
    assert "origin/main" in RELEASE      # and it must be released from main


def test_required_checks_match_the_ci_job_names() -> None:
    """The one drift that blocks every pull request forever: the ruleset waiting
    on a status check whose job was renamed. GitHub gives no warning - the pull
    request simply never becomes mergeable."""
    import re

    checks = _rule(_ruleset("main.json"), "required_status_checks")
    required = [c["context"]
                for c in checks["parameters"]["required_status_checks"]]
    assert required, required

    jobs_block = CI.split("jobs:", 1)[1]
    job_names = set(re.findall(r'^  ([a-zA-Z][\w-]*):$', jobs_block, re.MULTILINE))
    for check in required:
        assert check in job_names, (check, sorted(job_names))


def test_main_takes_no_direct_pushes_and_keeps_a_linear_history() -> None:
    main = _ruleset("main.json")
    assert main["target"] == "branch"
    assert main["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    for kind in ("deletion", "non_fast_forward", "required_linear_history",
                 "pull_request", "required_status_checks"):
        _rule(main, kind)
    checks = _rule(main, "required_status_checks")["parameters"]
    assert checks["strict_required_status_checks_policy"] is True


def test_the_settings_live_in_the_repository_not_in_the_script() -> None:
    """The rules are importable JSON so they can be diffed and restored; the
    script only uploads them, and the web UI can do the same by hand."""
    assert "rulesets" in RULES
    assert "ConvertFrom-Json" in RULES
    assert '$checks = @(' not in RULES     # no second copy of the check names


def test_release_tags_cannot_be_moved_or_deleted() -> None:
    """A release tag is the identity of the build a participant installed, and
    every recording stores that version in session.app_version. A tag that can
    be repointed makes it impossible to say later which software produced which
    data - and the study only runs once."""
    tags = _ruleset("release-tags.json")
    assert tags["target"] == "tag"
    assert tags["conditions"]["ref_name"]["include"] == ["refs/tags/v*"]
    for kind in ("deletion", "update", "non_fast_forward"):
        _rule(tags, kind)
    assert tags["bypass_actors"] == []       # nobody, not even an admin


def test_no_review_requirement_while_the_project_is_one_person() -> None:
    """GitHub does not allow approving your own pull request, so a review
    requirement would block every merge for a solo maintainer. The structure is
    in place either way; this number is the only thing that changes."""
    params = _rule(_ruleset("main.json"), "pull_request")["parameters"]
    assert params["required_approving_review_count"] == 0
    assert params["allowed_merge_methods"] == ["squash"]


def test_admins_keep_a_way_through_if_ci_itself_breaks() -> None:
    bypass = _ruleset("main.json")["bypass_actors"]
    assert bypass, "no emergency route out of a broken CI"
    assert all(a["actor_type"] == "RepositoryRole" for a in bypass), bypass


def test_every_ruleset_is_valid_json_and_enforced() -> None:
    for path in RULESET_DIR.glob("*.json"):
        ruleset = _ruleset(path.name)
        assert ruleset["name"], path
        assert ruleset["target"] in ("branch", "tag"), path
        assert ruleset["enforcement"] == "active", path


def test_ci_runs_on_every_pull_request_without_a_path_filter() -> None:
    """A required check that is skipped because no path matched leaves the pull
    request waiting for a report that never comes."""
    trigger = CI.split("jobs:", 1)[0]
    assert "pull_request:" in trigger
    assert "paths:" not in trigger


def test_launcher_is_the_frozen_entry_point() -> None:
    assert "riftrec_launcher.py" in SPEC
    assert "from riftrec.cli import main" in LAUNCHER
