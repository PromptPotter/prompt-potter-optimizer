# admit-3: admit-2's leftover rows, each its own link (cloud)

`briefs/common.md` binds you. Base: `origin/main`. The rows are in `docs/specs/code-debt-cleanup.md`
§ batch/26 · admit-2; quote each row's title in the commit that closes it and delete it. One link per
row, in this order; a row that reads false at its line is reported, not built.

## Links
1. **A closed `BackendClient` reopens its pool on next use** (`infrastructure/backend.py::BackendClient.http`):
   a closed client refuses a send. **Re-test:** the row's.
2. **No test pins that a raising `run_campaign` leaves no open client** (`embedded_run.py::run_campaign`):
   after link 1 a send on a leaked client is loud, so first read `tests/CLAUDE.md`'s charter: write the
   two tests only if the leak stays silent AND unrecoverable after link 1; else delete the row and say why.
3. **A non-launch command filed through `host_dispatcher` sweeps nowhere** (`embedded_run.py::host_dispatcher`):
   the sweep is the dispatcher's, for every kind. **Re-test:** the row's.
4. **Framing precedence is decided twice** (`jobs/mint.py`, `task_context.py`): one rule, `task_framing:
   off` asked first, so no paid check-in overwrites a dataset's committed framing. **Re-test:** a
   `--task-text` launch on a `task_framing: off` dataset runs no check-in and writes no framing.
5. **A head-to-head on a bank with no `dataset_split` runs blind** (`jobs/mint.py::arm_plans`): it refuses,
   naming the missing split. **Re-test:** a head-to-head mint on a bank with no held-out rows refuses.

## Owns
`infrastructure/backend.py`, `application/embedded_run.py`, `jobs/mint.py`, `task_context.py`, their
tests, generated specs/types. NOT `presentation/teleprompter.py` or `application/maintenance/**` (the
row "`acompile` lands twice" waits: `landing.py` rides the `l4-words-fix` chain), NOT
`runner/inner/spawn.py`, `runner/termination.py`, `domain/phases.py`, the spend files
(`spend_book.py`, `domain/spend.py`, `served_dashboard.py`, `jobs/quota.py`), `intelligence/potter/pobb.py`,
`mask/record.py`, `domain/round_diagnostics.py`, `tests/factories.py`, `tests/test_spend.py`,
`tests/test_scoring.py` (three other chains in flight). A file outside Owns the root needs: report it.

## Target
Net ≤ 0 `promptpotter/` lines. Run the `--against` legs and `--digests`; name every leg and digest
that moves, old→new.

## Report
Per `common.md`.
