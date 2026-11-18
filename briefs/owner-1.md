# owner-1: an inner init resolves its cells once; a definition carries its owner (cloud)

`briefs/common.md` binds you. Base: `origin/main` (with small-9: `dataset_access.py::connector_of`).
Rows are in `docs/specs/code-debt-cleanup.md` (link 2's is below, not yet filed: file it in the
commit that closes it, then delete it in the same commit, so the report names it). Quote each row's
title in the commit that closes it. One link per row, in this order; a row that reads false at its
line is reported, not built.

## Links
1. **`resolve_cells` runs twice per run init** (the row's Action: the cells are resolved once, off the
   panel the workload holds; `identity_config` takes `(stores, experiment)`, the connector protocol
   moving with it). **Re-test:** the row's grep; every `research/datasets/promptpotter-self/` member's
   inner cells resolve byte-equal to the base.
2. **A definition's connector is looked up by name at each reader** (small-9's report): about 15
   `connectors.CONNECTORS.get(definition.connector)` sites in `application/` (`wiring.py:174`,
   `startability.py:94`, `head_to_head_price.py:147`, `pipeline_resolve.py:402/413/893/983`, …) each
   resolve the name a definition holds, and `runner/inner/tasks.py:249` takes `CONNECTORS.table.get`,
   so an unknown name reads as "not self-optimization" where `connector_of` raises. Suspected root: a
   definition carries its connector's name, not its owner. Action: the definition (or the one
   resolver it already passes through) answers its `Connector`; the readers in Owns take it; an
   unknown name raises once, where the definition is read. A registry lookup keyed by a user's or a
   listing's name (`backend_listing.py`, `draft_patch.py`, `health.py`) is the registry's job and
   stays. **Re-test:** `git grep -n "CONNECTORS.table.get" -- promptpotter/application/runner` is
   empty; the remaining `CONNECTORS.get(` sites each name why they hold a bare name.

## Owns
`application/runner/inner/spawn.py`, `application/runner/inner/tasks.py`, `connectors/protocol.py`,
`connectors/*.py` (an `identity_config` signature only), `infrastructure/store/dataset_access.py`,
`application/initialization/wiring.py`, `application/datasets/startability.py`,
`application/head_to_head_price.py`, `application/pipeline_resolve.py`,
`application/scoring/formula/compiler.py`, `presentation/cli/commands/launch.py`, tests in modules
of your choosing OTHER than `tests/test_spend.py`, `tests/test_security.py`, `tests/test_identity.py`,
`tests/factories.py`, `tests/conftest.py` (the `spend-4` chain), generated specs/types. NOT
`application/jobs/` (quota.py, admission.py, mint.py: other chains), `infrastructure/backend.py`,
`infrastructure/llm/pricing.py`, `served_dashboard.py`, `application/embedded_run.py`,
`application/initialization/session.py`, `infrastructure/store/store.py`. A file outside Owns the
root needs: report it.

## Target
Net ≤ 0 `promptpotter/` lines. Run the `--against` legs and `--digests`; name every leg and digest
that moves, old→new (an L4 estimator digest that moves is a re-key: say which input moved it).

## Report
Per `common.md`.
