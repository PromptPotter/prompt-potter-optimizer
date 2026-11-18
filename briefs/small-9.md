# small-9: one panel-owner lookup, one landing per compile, a ledger that names its main (cloud)

`briefs/common.md` binds you. Base: `origin/main` (with bundle C: panel-rule-2, admit-3,
factory-keys). The rows are in `docs/specs/code-debt-cleanup.md`; quote each row's title in the
commit that closes it and delete it. One link per row, in this order; a row that reads false at its
line is reported, not built.

## Links
1. **A panel's owner is looked up beside `dataset_connector`**: one owner lookup (an unknown connector
   name raises, as `dataset_connector` does), `parse_inner_panel` goes for `InnerTasks.model_validate`,
   and `connectors/__init__.py::_validate` refuses a `declared_panel_model` on a connector owning no
   panel. **Re-test:** the row's; every `research/datasets/promptpotter-self/` member resolves byte-equal.
2. **`acompile` lands twice per compile** (it also sweeps twice since admit-3 moved the sweep into
   `embedded_run.py::enter`): the row's Action. **Re-test:** the row's two greps.
3. **`surface-ledger` measures whatever `origin/main` the clone last fetched**
   (`scripts/complexity_ledger.py::_counted_at`). **Re-test:** the row's.

## Owns
`infrastructure/store/dataset_access.py`, `application/runner/inner/tasks.py`,
`application/runner/inner/spawn.py` (`_resolve_inner_panel` only), `connectors/__init__.py`,
`presentation/teleprompter.py`, `application/maintenance/landing.py`, `application/embedded_run.py`,
`scripts/complexity_ledger.py`, tests in modules of your choosing OTHER than `tests/test_spend.py`,
`tests/test_security.py`, `tests/test_identity.py`, `tests/factories.py`, `tests/conftest.py` (the
`spend-4` chain), generated specs/types. NOT `infrastructure/backend.py`, `jobs/mint.py`,
`served_dashboard.py`, `infrastructure/llm/pricing.py`. A file outside Owns the root needs: report it.

## Target
Net ≤ 0 `promptpotter/` lines. Run the `--against` legs and `--digests`; name every leg and digest
that moves, old→new.

## Report
Per `common.md`.
