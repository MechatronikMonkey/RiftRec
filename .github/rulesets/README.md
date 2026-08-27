# Repository rulesets

The branch and tag protection for this repository, as JSON. GitHub can import
these files directly, so the settings are versioned, reviewable and restorable
instead of living only in a settings page nobody can diff.

JSON allows no comments — the reasoning is here.

## Applying them

**In the web UI** (no tooling needed):

> Settings → Rules → Rulesets → **New ruleset** → **Import a ruleset** → upload
> `main.json`, then repeat for `release-tags.json`.

**Or with the GitHub CLI**, which reads exactly these files:

```powershell
gh auth login
powershell -ExecutionPolicy Bypass -File .github\setup-repo-rules.ps1
```

Re-running updates the existing rulesets instead of adding duplicates.

**Order matters.** Push `main` first, while direct pushes are still allowed, and
let CI run once so GitHub has seen the check names. A required status check that
never reports blocks merging forever, and a wrong name is invisible until a pull
request hangs. `-Evaluate` (or setting `"enforcement": "evaluate"` before
importing) creates the rules in report-only mode: violations show up under
Settings → Rules → Rule Insights and nothing is blocked, which is the cheap way
to confirm the names before they can block anything.

Do **not** also add a classic branch protection rule. One branch governed by two
mechanisms means every future "why can't I merge this" costs an afternoon.

## `main.json`

Pull requests only, no direct pushes — including for the maintainer.

* `deletion`, `non_fast_forward` — main cannot be deleted or force-pushed.
* `required_linear_history` plus `allowed_merge_methods: ["squash"]` — one commit
  per change on main, matching the history the project already has, and the
  merge button cannot produce anything else.
* `pull_request` with **`required_approving_review_count: 0`** — deliberate.
  GitHub does not let anyone approve their own pull request, so a review
  requirement would block every merge while the project has one maintainer. The
  structure (branch → PR → checks → merge) is in place regardless; raise this to
  `1` the day a second person joins, and nothing else changes.
* `required_review_thread_resolution` — a comment thread has to be resolved, not
  merely outlived.
* `required_status_checks` with **`tests`** and **`installer`** — the job names
  in [`../workflows/ci.yml`](../workflows/ci.yml). If those job names ever change
  without this file changing, pull requests wait forever on a check that no
  longer exists; `tests/test_packaging.py` compares the two and fails first.
* `strict_required_status_checks_policy` — a branch must be up to date with main
  before it can be merged, so the checks that passed are the checks that matter.
* `bypass_actors`: repository admins, `bypass_mode: always` — an emergency route
  for the case where CI itself is broken. It is recorded in Rule Insights, so
  using it leaves a trace.

## `release-tags.json`

`v*` tags cannot be moved, deleted or force-updated, and **nobody** can bypass
it.

This is not housekeeping. A release tag is the identity of the build a
participant installed, and every recording stores that version in
`session.app_version`. If `v0.1.0` can be repointed at a different commit, then
six months later there is no way to say which software produced which data — and
the study only runs once.
