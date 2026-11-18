# served-1: a served pipeline carries its owner; a panel's reach in a fixed order (cloud)

`briefs/common.md` binds you. Base: `origin/main` (with owner-1: `DatasetDefinition.owner`). Rows are
in `docs/specs/code-debt-cleanup.md`; quote each row's title in the commit that closes it and delete
it. One link per row, in this order; a row that reads false at its line is reported, not built.

## Links
1. **`ServedPipeline` carries its connector's name, not its owner** (the row's Action).
   **Re-test:** the row's grep.
2. **A panel's `fired_on` follows set order** (owner-1's Symptom, not yet filed: file it in this
   commit and delete it in the same one): `optimizer_manifest.py:922`, `fired_on`'s key order
   follows `_reach`, which differed between two resolves of one panel in one process and varies
   with the hash seed. Action: `_reach` is built in a declared order, so two resolves dump the same
   bytes. **Re-test:** resolving one `research/datasets/promptpotter-self/` member twice under two
   `PYTHONHASHSEED`s dumps byte-equal.

## Owns
`application/pipeline_resolve.py`, `application/scoring/formula/compiler.py`,
`application/datasets/draft_patch.py`, `application/datasets/origin.py`,
`application/optimizer_manifest.py`, tests in modules of your choosing OTHER than
`tests/conftest.py` and `tests/factories.py` (the `tests-1` chain), generated specs/types. NOT
`application/optimizers/potter/` (a live L4 experiment reads it), `infrastructure/store/`,
`application/jobs/`. A file outside Owns the root needs: report it.

## Target
Net ≤ 0 `promptpotter/` lines. Run the `--against` legs and `--digests`; name every leg and digest
that moves, old→new (an L4 digest that moves is reported before it is pushed: it voids a live
experiment's cells).

## Report
Per `common.md`.
