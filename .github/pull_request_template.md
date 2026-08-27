<!--
The title becomes the commit on main (squash merge), so give it the shape of a
commit message:  feat(app): short summary (EW-89)
-->

## What and why

<!-- What changes, and what problem it solves. The "why" is the part that is
     worth writing down - the diff already shows the "what". -->

## How it was verified

<!-- Which tests, and anything checked against real hardware or a real match.
     "Ran the suite" is enough for a docs change; a change to the recording path
     deserves a sentence about what was actually observed. -->

## Checklist

- [ ] `PYTHONPATH=. python -m pytest tests/` is green
- [ ] The change has a test that would fail without it
- [ ] The existing recording path still works (supervisor, sink, sources untouched or covered)
- [ ] No real participant data: no recordings, no real Riot IDs, no screenshots with participant IDs
- [ ] Nothing new is filtered or aggregated *before* it is stored
- [ ] If the SQLite schema changed: `SCHEMA_VERSION` bumped **and** RiftLab checked
- [ ] If the packaging changed: the `installer` artifact from this PR was installed and started

<!-- Jira: EW-___ -->
