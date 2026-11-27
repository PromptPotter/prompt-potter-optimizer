# w11-tests2: area 14, the rework's open half: every fact through a world door (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. `tests/` is yours now:
w10-tests has landed. The other w11 chains may each add their own area's facts beside you; leave their
facts alone. SPEC: `briefs/map.md` § 14, the debt doc's `## 14` section, and `briefs/tests-survey.md`.
Target: a fact is proven through a door of the offline world, never a hand-seated record; a guard at one
choke point is tested once; the gate answers from what changed and fits its budgets.

Links, deletion first, each with its measured seconds before and after:
1. **`write_resolved_run(` 2 callers → 0** (`tests/test_identity.py`), and **`ForkCut(` seated by hand**
   (`tests/test_security.py`): each fact opens its run through the world's door.
2. **`tests/test_spend.py` patches `execute_round`**: it becomes an offline leg (`run_one`'s knobs).
3. **The 19 parked facts**: each gets a world door, or a row naming the door it lacks.
4. **The 27 hand-appended ledger records**: triaged, each made a door's output or a row.
5. **A guard is tested once over its doors**: start on the guard with the most doors, one table over
   them, and leave one fact per door only where the door spells the guard itself.
6. **The landing key's two `git add` calls** (`scripts/gate.py`): one.

Not yours: the slow `offline` facts whose rule is decided inside `application/runner/`. They wait for
that area's chain, so file nothing new for them.

OWNS: `tests/**` (less facts another w11 chain adds), `scripts/gate.py`, `scripts/offline_world.py`,
`scripts/offline_run.py`, `webapp/e2e/**`, `tests/CLAUDE.md`, `docs/developer/offline-run.md`.

DONE WHEN: `git grep -cE "write_resolved_run\(|ForkCut\(" -- tests` is 0 · `git grep -c execute_round --
tests/test_spend.py` is 0 · pytest's wall, timed at base and at tip on your own box, is lower at tip ·
no fact lost (list each moved fact and where its claim is proven now) · `python scripts/gate.py` green ·
every behaviour key unmoved.
