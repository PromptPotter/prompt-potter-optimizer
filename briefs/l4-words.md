# l4-words: a holed panel and an errored L4 cell stop in their own words (cloud)

`briefs/common.md` binds you. Base: `origin/main` (2975f7225 or later). The rows are in
`docs/specs/code-debt-cleanup.md`; quote each row's title in the commit that closes it and delete it.
One link per row. Law (`CLAUDE.md`): a state the system can enter but not report is the costliest
bug here.

## Symptom (observed on the desk, 2026-11-18)
The dyck known-sign check's outer round 1 (`promptpotter-self.dyck__2bd880`, cycle_4ec5c80c24a4)
closed with `panel_coverage` holes {positive 1, crippled 1} and read **"Paused by interrupt"**: no
signal had reached the process, and nothing named which cell or why. `l4-valid` (on main) gave a
backend's hole its own stop (`BACKEND_HOLE`, re-asked once by the outer) and our bound's
(`PANEL_CUT`); every other hole still falls through to a bare `PAUSED`.

## Links
1. **A holed panel pauses "by interrupt", with no words, unless the hole is the backend's or our
   bound's**: `runner/termination.py::panel_gate_tripped` ends on `StopLoop(StopReason.PAUSED)` for
   any other hole, and `domain/phases.py::SELF_PAUSE_CAUSE` has no row for it. Largest excision
   first: decide whether the two-row category loop plus the bare `PAUSED` become ONE stop the panel
   gate raises on its holes, its detail each holed row's category and error (a variant is a state of
   the existing thing), keeping what `l4-valid` made true: a backend hole is re-asked once, a resume
   re-measures every hole. State the decision and why in the commit. **Re-test:** the dyck shape
   (one UNSCOREABLE row per arm, error text set) reads those rows' errors, never "interrupt"; a
   backend hole still re-asks once.
2. **An L4 outer's errored cell carries no backend words**: `domain/cycle_result.py::CycleResult`
   holds no stop detail, so `runner/inner/spawn.py::run_inner_cycle` grades a second
   `backend_hole` as "stopped on backend_hole" and the backend's status and message stay on the
   inner ledger. Action: the outer cell's error reads the inner stop's detail, off the one record
   the inner run declared its stop on. **Re-test:** two injected 504s in one inner cell put the
   backend's words in the outer cell's error.

## Owns
`runner/termination.py`, `runner/round.py` (the panel gate), `domain/phases.py` (stop reasons,
`SELF_PAUSE_CAUSE`, `stop_said`), `runner/inner/spawn.py`, `domain/cycle_result.py`, their tests.
NOT `infrastructure/llm/spend_book.py`, `domain/spend.py`, `run_observers.py`, `read_model.py`,
`runner/bench.py`, `runner/loop.py`, `runner/entry.py` (the `spend-served` chain), NOT
`store/dataset_access.py`, `domain/layer_fold.py`, `datasets/prompts.py`, `runner/inner/tasks.py`
(the `panel-rule` chain). A file outside Owns that the root needs: report it, do not edit it.

## Target
Net ≤ 0 `promptpotter/` lines. Run the `--against` legs (the L4 and wounded legs above all); name
every leg and digest that moves, old→new. A `StopReason` member added or dropped is a persisted enum:
name the wipe it causes.

## Report
Per `common.md`.
