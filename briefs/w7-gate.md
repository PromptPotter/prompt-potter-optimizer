# w7-gate: what makes a gate and a CI run take a quarter hour (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you (read BASE FIRST). Base **`2c7dff34e`**.

SPEC: `docs/specs/roadmap.md` item **"Then: what makes a gate and a CI run take a quarter hour"**, its
script half (operator, 2026-11-26: "that sounds like unnecessary stale stuff"), and these
`docs/specs/code-debt-cleanup.md` rows: **`Check.slots` is a guess beside `mb`'s measured peak**;
**Vitest's start still meets load from outside the gate lock**; **`config/` has no layering row**;
**A build stamp names HEAD while the build reads the worktree**.

DECIDED:
1. Measure first, in your session: `CI=1 python scripts/gate.py` wall and each check's seconds, and
   the cores you ran on. The roadmap's numbers (737 s wall on 16 cores; playwright 337, pytest 315,
   offline-run 300, offline-run-controlled 102, walk-world 45) are the desk's. Report yours before and
   after; CI's own wall (`gh run list` if `gh` is there; if not, read `.github/workflows/ci.yml` and
   say what it runs twice).
2. What one check walks that another already walked goes first: `walk-world` and the two offline legs
   each run `scripts/offline_run.py`; one run serves every check that reads it. Then each check that
   re-reads the whole tree answers from what changed where its answer allows it.
3. Do not edit `tests/test_*.py`. Run `pytest --durations=40` and file each test over
   `tests/CLAUDE.md` § Budget per test as a "row to file" (its seconds, what it walks, whether another
   test walks the same).
4. Then the four rows, each its own link, built as their Root says.
5. If time remains, two webapp edge rows, each its own link: **A pane's data read in flight reports
   nothing** (`useRead`'s loading marks its pane busy; `harness.ts::drawn` keeps only its `aria-busy`
   check) and **A focus ring is re-skinned outside `base.css`**.

OWNS: `scripts/gate.py`, `scripts/offline_run.py` (but the one line naming `review.md` is w7-text's),
`scripts/offline_world.py`, `scripts/complexity_ledger.py` (the gate's own rows), `.github/workflows/ci.yml`,
`webapp/playwright.config.ts`, `webapp/vite.config.ts`, `webapp/e2e/harness.ts`, `webapp/e2e/walk_world.py`,
`webapp/e2e/serve*`, `promptpotter/config/first_run.py`, `promptpotter/application/health.py::webapp_bundle`
(that function only: the rest of `health.py` is w7-small's), `webapp/lib/hooks/useRead.ts`,
`webapp/app/styles/foundation/base.css` and the two selectors the focus-ring row names
(`domains/shell.css:64`, `domains/compare.css:477`), `docs/developer/offline-run.md` and `tests/CLAUDE.md` where a check's contract moves.

LANDS LAST of wave 7: every chain's proof runs this gate. A check you make faster must still fail on
what it failed on: for each changed check, show one deliberate break it still catches.
