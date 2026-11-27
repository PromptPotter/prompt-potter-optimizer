# w7-open3: a fork's share and cut belong to its admission and its opening (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7`'s tip** (main `2c7dff34e` plus the wave-7 landings, w7-rows3 the last). It is not
on main yet, so skip the is-ancestor check and report `git log -1 origin/land/w7`.

SPEC: `docs/specs/code-debt-cleanup.md` § wave-7, two rows that w7-open2 filed. Read both whole, and
read w7-open2's commit on land/w7 (`git log --grep=w7-open2 origin/land/w7`) before the first edit:
1. **An admitted fork holds no share until it is cut**: an admitted request's share is held on its
   campaign from admission, so a sibling admitted between a fork's admission and its cut reads the
   fork's share, the same as one admitted beside a mint.
2. **A fork's cut is not withdrawn by its opening's exit**: the opening's exit withdraws the cut once
   its job is released, on every path. `commands/launching.py::_held_fork`'s partial withdraw goes,
   which leaves one owner.

DECIDED: one commit per row, row 1 first, because the share it moves is what row 2's exit releases.
Each row's re-test is a test that fails without its fix (state that you ran it that way). No new
class: the share and the cut ride the existing `Opening` / `LaunchRequest` / quota channels. If a row
needs a design the row does not decide, stop it and report.

OWNS: `promptpotter/application/jobs/**` (`launcher/`, `quota.py`, admission),
`promptpotter/application/commands/launching.py`, `application/opening.py`, and their tests. Not yours:
`tests/factories.py` and the test world (w7-tests rewrites it on land/w7-wipe; name every test file
you touch), `scripts/gate.py`, `presentation/mcp/` (w7-rows4 holds those).

LEAN: `promptpotter/` shrinks or holds. Equals-main (every leg), digests and behaviour keys are
unchanged. Run data: none expected. If a job file's shape moves, it is a SURVIVOR: stop and report,
and do not migrate.
