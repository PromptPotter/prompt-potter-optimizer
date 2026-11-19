# err-1: a send's failure is classified once, where it closes (cloud)

`briefs/common.md` binds you. Base: `origin/main` (with `918e11659` usage-1 and `39642b195`
refuse-1). The row is `docs/specs/code-debt-cleanup.md` § passes-6, "A send's error category is
decided twice": build it, quote its title in each commit, delete or cut the row. Check every site
at its line first; a site the row names that no longer reads as stated is reported, not built.

## Links
1. **The send raises its category.** `infrastructure/llm/spend_book.py::_status_failure` classifies at
   the send's close (`SendOutcome`, `:104`); the cell classifies the raised exception again
   (`scoring/sample_measurement.py::_known_failure` `:685`, `measure_sample`'s
   `except httpx.HTTPStatusError` `:627`, `connectors/harbor.py::_AgentModel.call`). Action: the
   send raises one typed failure carrying its `SendOutcome` (see `infrastructure/llm/send_failure.py`
   and `openai_compat.py::FaultedReplyError`: reuse before adding), and every reader takes the
   category off it.
2. **Every caller of the client reads that contract.** `scoring/judges/call.py`
   (`_ProviderFailedError`, its `httpx` catch) and `infrastructure/backend.py::run_query`
   (`raise_for_status` at `:304` builds a fresh exception after the send closed) read the typed
   failure. A caller whose category would change is listed in the commit with old → new.
   **Re-test:** `git grep -c "HTTPStatusError" -- promptpotter/application` == 0, and one fact: a
   429 and a 5xx each reach a cell's ledger as the category the send's close decided.

## Owns
`promptpotter/infrastructure/llm/*` (with its `CLAUDE.md`), `application/scoring/sample_measurement.py`,
`application/scoring/judges/*`, `connectors/harbor.py`, `infrastructure/backend.py`, their tests,
and in `tests/test_spend.py` the facts on the send's close only. NOT `application/optimizers/potter/`
(it moves the L4 key), NOT `application/scoring/passes.py`, `application/jobs/*`,
`application/runner/*`, `infrastructure/store/*`, `infrastructure/ledger.py` (load-3 runs beside you
on them). **Shared with load-3:** `tests/test_spend.py`.

## Target
Net ≤ 0 in `promptpotter/`. No digest moves: say so with the command that showed it.
`python scripts/gate.py --py` green.

## Report
Per `common.md`, with a Symptoms list for anything off-row you saw.
