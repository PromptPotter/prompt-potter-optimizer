# refuse-1: a payload refusal names its expected form; a cap reads one book (cloud)

`briefs/common.md` binds you. Base: `origin/main` (54b2112e5 or later). Rows are in
`docs/specs/code-debt-cleanup.md`; quote each row's title in the commit that closes it and delete
it. One link per row, in this order; a row that reads false at its line is reported, not built.

## Links
1. **A payload refusal says no expected form** (the row's Action, at
   `shared/errors.py::PayloadInvalidError.validating`, so REST's 422 and every CLI verb read the
   same words; no adapter renders its own). **Re-test:** the row's; and one REST 422 on the same
   payload carries the same entry (entry-point parity).
2. **The cap halts on money its served reading omits** (the row's Action: `exhausted()` reads the
   remainder net of the calls out, as `_room` / `refuse_unless_room` do, through `_left`, so one
   reading decides both). Name every offline leg whose stop moves, and why. **Re-test:** the row's.

## Owns
`shared/errors.py`, `infrastructure/llm/spend_book.py`, `application/run_phase_control.py` (the
`exhausted()` reader), `presentation/cli/main.py` (only to delete a rendering the root makes
redundant), `presentation/api/` (only the same), tests in modules of your choosing OTHER than
`tests/conftest.py`, `tests/factories.py`, `tests/test_spend.py`, `tests/test_identity.py` (the
`load-2` chain owns them: a fact that belongs there is reported), generated specs/types. NOT
`application/optimizers/potter/`, `application/jobs/`, `infrastructure/store/`,
`application/initialization/`.

## Target
Net ≤ 0 `promptpotter/` lines. Run the `--against` legs and `--digests`; name every leg and digest
that moves, old→new. A moved `behaviour_keys.json` fingerprint or L4 digest is reported before it
is pushed.

## Report
Per `common.md`.
