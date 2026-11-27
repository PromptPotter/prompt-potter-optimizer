# w10-tests: area 14 to its Target, reduction first (cloud · Opus)

`briefs/common.md` and `briefs/w10-shared.md` bind you. BASE: `origin/release/next` (it holds w10-records).
The operator: the whole CI, pytest and gate burden must shrink; the test rework (the test world, world
doors) is half-done and sped nothing up. Read `briefs/tests-survey.md` first: it is measured on the desk and
on CI, one run per number. Its finding: the seconds are not in duplicates (≈4 s) but in real run inits per
test (setup 51 s → 298 s since `7dce00a96`), which the desk's file system makes 9× dearer than CI. The desk's
file system is the operator's step, not yours: every cut below is one that also cuts CI.

Links, deletion first, each with its measured seconds before and after (CI's per-check table and your own
`--durations`; never an estimate unmarked):
1. **A rule reached only through a round is a pure function** (the charter's tier rule, `tests/CLAUDE.md`):
   the 21 `offline` facts over 3 s (survey §B/§C; the budget-cut table, "round file the disk refuses", the
   fork's axis, the cell banked under one formula...). Each fact keeps its claim, tested where it is decided.
2. **A shared run pays for itself or goes**: the module-shared world runs serving ≤7 facts (head_to_head ×4,
   whose table the controlled leg already computes; steered_line ×7; l4_outer ×3; origin_forks ×2;
   family_tree ×1; gated ×2). Fold each fact onto a run that already exists, or link 1's shape.
3. **The gate collects once**: `gate.py`'s `--collect-only` before the suite (24 s).
4. **One potter straight run per gate**, if offline-run and walk-world build the same one (verify by
   diffing their outputs first; the survey only inferred it).
5. **The touch project reruns 86 walk specs at phone width**: measure it; the operator's surface is the
   phone, so the phone run is the one kept and the second width runs only specs whose layout differs.
   Keep a per-spec durations record on green (the gate keeps playwright's output only on failure).
6. **The rework's open half, closed or filed**: `write_resolved_run(` 2 callers → 0; `ForkCut(` in
   `test_security.py`; `test_spend.py`'s `execute_round` patch; `test_scoring.py`'s hand-driven rounds; the
   27 hand-appended ledger records triaged; the 19 parked facts each given a world door or a row; "a guard is
   tested once over its doors" started on the guard with most doors.
7. **Census**: area 14's Unexamined line, each module read against the Target, rows filed, line rewritten.

No fact is lost: the report lists each deleted or moved test with where its claim is now proven.
OWNS: `tests/**` (except the seal's identity tests: `w10-seal` runs beside you in
`tests/test_security.py`'s key-seal tests; leave them and their fixtures alone), `scripts/gate.py`,
`scripts/offline_world.py`, `scripts/offline_run.py`, `webapp/e2e/**`, `webapp/playwright.config.ts`,
`.github/workflows/**`, `tests/CLAUDE.md`, `docs/developer/offline-run.md`. DONE WHEN: pytest's wall and the
full gate's wall, each timed at base and at tip on your own box (CI runs only on PRs to main; for reference
CI run 46050489497 took pytest 70.6 s, gate 273 s), are lower at tip · no fact lost · `python scripts/gate.py`
green · every behaviour key unmoved.
