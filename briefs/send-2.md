# send-2: every sender raises its close (cloud)

`briefs/common.md` binds you. Base: `origin/main` (with `d32902ab1` err-1). The row is
`docs/specs/code-debt-cleanup.md` § passes-6, "Two senders still classify after their close": build
it, quote its title in each commit, delete or cut the row. Read `promptpotter/infrastructure/llm/CLAUDE.md`
and err-1's commit (`git show d32902ab1`) first: a wire failure raises
`send_failure.py::FailedSendError` carrying its `SendOutcome`; a typed refusal, a remedy and a bug
raise as themselves.

## Links
1. **dspy raises its close.** `connectors/dspy_module.py::_settle` closes its admission with
   `failed_send(failure)` (`:199`), then re-raises the raw litellm exception, which
   `scoring/sample_measurement.py::_failed_row` classifies again. Action: it raises what the close
   decided, the shape `openai_compat` raises; `_failed_row` reads a raw exception no more than it
   must (say which senders still reach its `failed_send`, if any). **Re-test:** the dspy cell facts
   (`tests/test_spend.py::test_a_dspy_cell_*`) pass unchanged in what they assert, and a 4xx credit
   refusal through dspy reads PROVIDER_CREDIT.
2. **harbor reads its own close.** `connectors/harbor.py::_infrastructure_failure` (`:858`) matches
   credit words in a trial's text; for an agent whose sends ride our client the close already
   classified them. Action: an agent on our client takes the category its send's close carried; the
   text match stays only for an installed agent whose provider speaks through the harness alone
   (say which agents those are, at their lines). `openai_compat.py::FaultedReplyError` stops
   subclassing `httpx.HTTPStatusError` if the close can carry its status itself. **Re-test:**
   `git grep -n "failed_send(" -- promptpotter` lists only closes; harbor's facts pass.

## Owns
`promptpotter/connectors/{dspy_module,harbor}.py`, `promptpotter/infrastructure/llm/*`,
`application/scoring/sample_measurement.py`, `application/scoring/judges/*`, their tests, and in
`tests/test_spend.py` the dspy and harbor facts only. NOT `promptpotter/infrastructure/store/**`,
`application/jobs/*`, `application/runner/*`, `application/commands/*` (store-2 runs beside you on
them), NOT `application/optimizers/potter/` (it moves the L4 key). **Shared with store-2:**
`tests/test_spend.py`.

## Target
`promptpotter/` net ≤ 0. Name every error category that moves (old → new, which sender, which
status). No digest moves: say so with the command that showed it. `python scripts/gate.py --py` green.

## Report
Per `common.md`, with a Symptoms list for anything off-row you saw.
