# usage-1: a step's usage is built from what was reported; two tests stand by their claim (cloud)

`briefs/common.md` binds you. Base: `origin/main` (61fff035c or later). Rows are in
`docs/specs/code-debt-cleanup.md` § passes-6; quote each row's title in the commit that closes it
and delete it. One link per row, in this order; a row that reads false at its line is reported.

## Links
1. **A cell's step usage drops reasoning** (the row's Root: `StepUsage` is built from the reported
   usage alone, one constructor every connector calls: `dspy_module.py`, `harbor.py` (both sites),
   `dbllmbench.py`, `scoring/sample_measurement.py`; and `openai_compat.py::ReportedUsage.tokens`
   keeps an unreported breakdown `None`, as `TokenAccount` reads it). **Re-test:** a dspy cell's
   served step usage carries `reasoning` when the reply reports it, and `None` cache parts where it
   reports none.
2. **Two tests fail beside their claim** (both halves: `tests/test_security.py` collects without the
   `mcp` extra, its MCP facts skipped by name; `tests/test_scoring.py::_peer_cycle` files round-0
   cells, so a noise floor through it reads them). **Re-test:** `pytest --collect-only
   tests/test_security.py` in a venv without `mcp` lists its non-MCP facts; a noise-floor fact
   through `_peer_cycle` reads its round-0 cells.

## Owns
`connectors/dspy_module.py`, `connectors/harbor.py`, `connectors/dbllmbench.py`,
`infrastructure/llm/openai_compat.py` (`ReportedUsage` / usage only), `domain/scoring.py`
(`StepUsage`), `application/scoring/sample_measurement.py` (its `StepUsage` builder only),
`tests/test_security.py`, `tests/test_scoring.py`, generated specs/types. NOT `tests/conftest.py`,
`tests/factories.py`, `tests/test_spend.py`, `tests/test_identity.py` (the `load-2` chain),
`infrastructure/llm/spend_book.py`, `shared/errors.py` (the `refuse-1` chain),
`application/optimizers/potter/`.

## Target
Net ≤ 0 `promptpotter/` lines. Run the `--against` legs and `--digests`; name every leg and digest
that moves, old→new (a token count a formula reads can move a decision: name it). A moved
`behaviour_keys.json` fingerprint or L4 digest is reported before it is pushed.

## Report
Per `common.md`.
