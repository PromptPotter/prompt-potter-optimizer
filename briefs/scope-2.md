# scope-2: one run-scope carrier, one campaign load, an inline launch that answers its queue place (cloud)

`briefs/common.md` binds you. Base: `origin/main`. Spec: `docs/specs/code-debt-cleanup.md`, three
rows, one link each, in this order. Run each row's Re-test at the base first; a row already closed
is named, not built.

## Links
1. § `### batch/26 · money`, **A run's ambient state rides three carriers**: `RunScope` takes the
   two `shared/measurement_context.py` ContextVars (`_MODE`, `_MEASURED`); `round` rides one carrier,
   not two. Re-test: `git grep -c "ContextVar(" -- promptpotter` is 1. Grep every reader first.
2. § `### batch/26 · money`, **A campaign's absence is still refused beside
   `CampaignStore.campaign`**: the reads take the door's `Campaign` (`cycle_reads.py`,
   `scoring/measurement_log.py::campaign_scorer` takes a `Campaign`, `runner/mint.py::open_launch`,
   the resume path's four loads). Re-test: one load per resume (count it with a patched loader in
   the test you touch).
3. § `### batch/28 · faults`, **An inline launch queued on a full machine prints nothing**: the
   inline `Launched` answers before its wait with the queued `LaunchedJob`, the terminal words it
   with `machine_notice`'s words (nothing on stdout under `--json`), then awaits the hold; no sink on
   `Inline`. Re-test: `new` inline with `MACHINE_RUN_CAPACITY=0` prints its queue place.

## Owns
`infrastructure/run_scope.py`, `shared/measurement_context.py` and its readers (by hunk:
`application/scoring/query_loop.py` and `catch_up.py` are another chain's, so touch only the import
and read lines there), `application/cycle_reads.py`, `application/scoring/measurement_log.py`,
`application/runner/mint.py`, `application/jobs/launcher/**`, `application/commands/launching.py`,
the CLI's launch rendering, their tests, regenerated specs/types. A holder outside these is cut.

## Target
Net negative `promptpotter/` lines (two ContextVars and three re-loads go).

## Report
Per `common.md`, plus the readers each ContextVar had and where each read now lands.
