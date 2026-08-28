# Packaging RiftRec (EW-89)

Turns the source tree into **one file a participant can run**:
`dist/RiftRec-Setup-<version>.exe`. No Python, no pip, no internet connection needed on
the target PC — the three things that stopped the zipped version from starting.

## Build it

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

Six steps. Steps 2, 3, 4 and 6 are also a composite action
([`.github/actions/build-installer`](../.github/actions/build-installer/action.yml)) shared
by [`ci.yml`](../.github/workflows/ci.yml) and
[`release.yml`](../.github/workflows/release.yml), so the installer a participant downloads
is produced by exactly the steps that were already green on the pull request:

| # | Step | Output |
|---|------|--------|
| 1 | build venv with `requirements-recorder.txt` + PyInstaller | `.venv-build/` |
| 2 | `make_icon.py` renders the app icon from the tray palette | `packaging/riftrec.ico` |
| 3 | PyInstaller freezes the app per `riftrec.spec` | `dist/RiftRec/` (~50 MB) |
| 4 | `RiftRec.exe selfcheck` against the frozen build | exit 0, or the build fails |
| 5 | optional: sign `RiftRec.exe` | — |
| 6 | Inno Setup wraps it per `riftrec.iss` | `dist/RiftRec-Setup-<version>.exe` (~17 MB) |

Prerequisites: Python 3.12 and [Inno Setup 6](https://jrsoftware.org/isdl.php)
(`winget install JRSoftware.InnoSetup`). Without Inno Setup the script stops after step 4
and leaves the frozen folder; `-SkipInstaller` does the same on purpose.

**Expect the antivirus to interrupt.** Every build produces a brand-new, unsigned
executable, which is exactly what on-access scanners are built to distrust. The build or
the first launch of `RiftRec.exe` can freeze for a few seconds while it is inspected and
then carry on normally; build times swing accordingly. If it gets in the way, exclude the
`dist/` folder on the development machine - but never suggest that to a participant, where
the scanner's verdict is the information we actually want.

## Why it is built this way

**One folder, not one file.** A PyInstaller `--onefile` build unpacks its whole runtime
into `%TEMP%` on *every* launch. That is slow, and it is exactly the many-small-writes
pattern already suspected behind the in-game stuttering in EW-51, where an on-access Avast
shield scans each write. The folder lives inside the installer, so a participant still only
ever downloads and runs a single file.

**No UPX compression.** Packed executables are a favourite antivirus heuristic trigger, and
this software already has an antivirus problem.

**Per-user install** (`PrivilegesRequired=lowest`). No UAC prompt on a machine we do not
own — one less dialog between a participant and a working recorder.

**No autostart entry.** EW-89 originally asked for start-with-Windows. Dropped by decision:
recording is meant to be a conscious act, and the participant has to put the chest strap on
anyway, so launching RiftRec belongs to the same decision. A recorder that came up silently
at logon would be recording without anyone having chosen to.
`tests/test_packaging.py::test_no_autostart_entry_is_registered` keeps that from being
undone by accident.

**Uninstall** removes the program, the shortcuts, `prefs.ini` and the stale lock file.
It never touches recordings — those live in the folder the participant chose and are the
point of the whole study (EW-52) — and it keeps `riftrec.log`, because if somebody
uninstalls after something went wrong, that log is the only evidence of why.

## `selfcheck` — the smoke test that matters

```powershell
Start-Process dist\RiftRec\RiftRec.exe -ArgumentList selfcheck -Wait -PassThru
```

Imports every module the recorder touches at runtime — including the paths only reached
once a strap connects or a window opens — and verifies that `schema.sql` is inside the
bundle. Exit code 0 or 1; the report goes to `%APPDATA%\RiftRec\riftrec.log`, because a
windowed build has no stdout.

This exists because a missing dependency in a frozen, windowed app produces **no error at
all** — just a window that never appears. The list lives in `riftrec/cli.py`
(`_RUNTIME_MODULES`) and a test fails if a new `riftrec/app/` module is not added to it.

## Code signing

Not done yet: a certificate costs roughly EUR 100–400/year, needs an identity check of the
company, and has **weeks** of lead time. The build does not wait for it — but nothing has to
change structurally when it arrives:

```powershell
powershell -File packaging\build.ps1 -SignCommand 'signtool.exe sign /fd sha256 /f cert.pfx /p <pw> /tr http://timestamp.digicert.com /td sha256 {f}'
```

`{f}` is replaced with the file being signed. The script signs `RiftRec.exe`, then passes
`/DSIGN /Ssigntool=...` to Inno Setup, which activates the `#ifdef SIGN` block in
`riftrec.iss` and signs both the setup and the uninstaller.

In CI, put the certificate in repository secrets and add the same two flags to the
"Compile the installer" step of the composite action; nothing else changes, and because
both workflows share that action, CI and release stay signed the same way.

Until then every participant meets **"Windows protected your PC"** on first run
(More info → Run anyway), which the pilot guide in the top-level README spells out.
Signed binaries are also touched less by antivirus software, which is the second reason to
get this done before the cohort grows.

## Files here

- `build.ps1` — the local build, end to end
- `riftrec.spec` — PyInstaller: hidden imports for bleak/winrt/pystray, bundles `schema.sql`
- `riftrec.iss` — Inno Setup: per-user install, Start-menu entry, uninstall, signing hook
- `riftrec_launcher.py` — frozen entry point; defaults to `gui` when started with no arguments
- `make_icon.py` — generates `riftrec.ico` from the tray colours (not committed, built each time)
