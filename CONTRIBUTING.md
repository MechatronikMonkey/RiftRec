# Contributing to RiftRec

RiftRec records heart rate and in-game data during a research study. It runs
**unattended, on other people's PCs, while a measurement wave is running**. A
session that fails to record cannot be repeated — the player already played that
game. That is the reason for most of the rules below.

## The rules that are not negotiable

1. **No real participant data in this repository.** It is public. No recordings,
   no `.sqlite` files, no real Riot IDs, no screenshots showing a participant's
   ID or name. Test fixtures are synthetic — see `tests/test_riot_raw.py` for the
   pattern. A raw `allgamedata` dump contains the Riot IDs of ten people, nine of
   whom never consented to anything.
2. **Every change needs a test**, and the existing recording path must keep
   working. `PYTHONPATH=. python -m pytest tests/` has to be green before a pull
   request is opened, not after.
3. **Nothing is filtered at capture time.** Aggregation, smoothing and artefact
   rejection belong in the analysis (RiftLab), never in the recorder. Raw data
   that was not written is gone forever; an analysis idea that only comes up in
   six months must not fail on a convenience decision made today.
4. **Nothing recorded is ever overwritten or deleted.** Each run mints its own
   uniquely named file. The only thing the recorder may remove is a file it
   created itself and that provably contains no session at all.
5. **A schema change bumps `SCHEMA_VERSION`** in `riftrec/__init__.py`, and you
   check that [RiftLab](https://github.com/MechatronikMonkey/RiftLab) still reads
   the file. `storage/schema.sql` is a contract between two repositories. Prefer
   additive changes — RiftLab still reads version 1 files.

## Working on a change

`main` is protected: it takes no direct pushes. Everything goes through a pull
request, including one-line fixes, and including changes by the maintainer.

```bash
git switch main && git pull
git switch -c ew-89-status-window      # <ticket>-<what>, or feat/<what>
# ... work, with tests ...
PYTHONPATH=. python -m pytest tests/
git commit
git push -u origin ew-89-status-window
gh pr create --fill                    # or open it in the web UI
```

**Branch names:** `ew-<number>-<short-description>` when a Jira ticket exists,
otherwise `feat/`, `fix/`, `docs/` or `chore/` plus a short description.

**Commit messages** follow the existing history — conventional commits with a
scope, and a body that says *why*:

```
feat(app): name the reason in the tray, drop empty session files (EW-89)

A start attempt with the strap on the desk produced 72 failed connects ...
```

Useful scopes: `app`, `rte`, `storage`, `sources`, `hal`, `packaging`, `docs`.

**Pull requests** are squash-merged, so the PR title becomes the commit on main —
give it the same shape as a commit message. Two checks must be green before the
merge button unlocks:

| Check | What it does |
|---|---|
| `tests` | the full suite on Windows |
| `installer` | freezes the app, runs `RiftRec.exe selfcheck`, compiles the installer, and uploads it as an artifact |

The `installer` artifact is worth using: it is a real, installable build of your
branch, so packaging changes can be tried on an actual machine before merging.

No approving review is required while the project has one maintainer — GitHub
does not allow approving your own pull request, so a review requirement would
block every merge. When a second person joins, raise it to 1 (see below).

## Releasing

A release is cut from `main` by pushing a tag. That tag is the "this is the state
I want" moment; everything after it is automatic.

```bash
# 1. bump the version through a normal pull request
#    riftrec/__init__.py:  __version__ = "0.2.0"

# 2. tag the merge commit on main
git switch main && git pull
git tag v0.2.0
git push origin v0.2.0
```

[`release.yml`](.github/workflows/release.yml) then refuses to continue unless the
tag matches `__version__` and sits on `main`, runs the tests, builds the
installer, and publishes a GitHub release with `RiftRec-Setup-0.2.0.exe`, its
SHA256, and release notes generated from the merged pull requests.

The version matters beyond the file name: every recording stores it in
`session.app_version`, so a mislabelled installer makes it impossible to tell
later which build produced which data. That is why the tag check exists.

## First-time repository setup

Once, by someone with admin rights, and **in this order**:

1. Push `main` while direct pushes are still allowed.
2. Let CI run once, so GitHub has seen the check names `tests` and `installer`.
   A required check that never reports blocks merging forever, and a typo in the
   name is invisible until a pull request hangs.
3. Apply the rules. They live in the repository as importable JSON —
   [`.github/rulesets/`](.github/rulesets/) — so the settings are versioned,
   reviewable and restorable rather than living only in a settings page.

   In the web UI: **Settings → Rules → Rulesets → New ruleset → Import a
   ruleset**, upload `main.json`, then repeat for `release-tags.json`.

   Or with the CLI, which reads exactly those files:

   ```powershell
   gh auth login
   powershell -ExecutionPolicy Bypass -File .github\setup-repo-rules.ps1
   ```

   Two **rulesets**, not classic branch protection — and do not add both. One
   branch governed by two mechanisms turns every future "why can't I merge this"
   into an afternoon.

   * **main** — pull requests only, `tests` and `installer` must pass, linear
     history, squash-merge only, no force-push, no deletion. Repository admins
     keep a bypass for the case where CI itself is broken.
   * **release tags** — `v*` tags cannot be moved or deleted, by anyone. A
     release tag is the identity of the build a participant installed, and every
     recording stores that version in `session.app_version`; a tag that can be
     repointed makes it impossible to say later which software produced which
     data.

   `-Evaluate` (or `"enforcement": "evaluate"` before importing) applies them in
   report-only mode: violations show up under **Settings → Rules → Rule
   Insights** and nothing is blocked. That is the cheap way to confirm the check
   names are right before they can block a pull request.

4. Tag the first release.

To require reviews later, change `required_approving_review_count` in
`main.json` (or pass `-RequiredApprovals 1`) and apply again.
[`.github/rulesets/README.md`](.github/rulesets/README.md) explains every rule
and why it is set the way it is.

## Running things locally

```bash
pip install -r requirements.txt              # full toolset
PYTHONPATH=. python -m pytest tests/         # no hardware, no match needed
python -m riftrec record --source fake --seconds 5 --db demo.sqlite
python -m riftrec gui                        # the tray recorder itself
```

Building the installer needs Inno Setup
(`winget install JRSoftware.InnoSetup`); see [packaging/README.md](packaging/README.md).

## What to expect from a review

The questions asked of every change, in roughly this order:

- Can this lose data, on a PC nobody is watching?
- What happens when the strap is not worn, Bluetooth is off, the antivirus
  interferes, or the machine is slow?
- Does a participant find out *why* something is not working, without opening a
  log file?
- Is there a test that would fail if this regressed?
