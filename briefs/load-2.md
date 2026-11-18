# load-2: a launch carries the campaign its door loaded, every carrier owned (cloud)

`briefs/common.md` binds you. Base: `origin/main` (with owner-1, tests-1 and served-1, which share
its files and land first). load-1 stopped at its
census because the carrier runs through files it did not own; this brief owns them. The rows are
in `docs/specs/code-debt-cleanup.md`; quote each row's title in the commit that closes it and
delete it. One link per row, in this order; a row that reads false at its line is reported.

## Links
1. **A resume loads its campaign 4×** (the row's Action and suggested shape: `Session.bound(hop, *,
   campaign)` with `purpose` derived, `open_launch` reads `session.campaign`, admission and the
   ceiling reads take the `Campaign`). **Re-test:** the row's counts, embedded resume 4 → 1.
2. **A terminal or REST resume reloads its campaign at every hop of the dispatch** (the row's
   Action). **Re-test:** CLI and REST resume launch path 8 → 1 (entry-point parity: name each door).
3. **A run's projections re-read the manifest per round**: only if links 1–2 leave a carrier the
   projections can read; else report it. **Re-test:** the row's.

## Owns
`infrastructure/store/campaign_store/store.py`, `application/jobs/quota.py`,
`application/jobs/launcher/admission.py`, `application/jobs/mint.py`,
`application/initialization/session.py`, `application/initialization/wiring.py`,
`application/pipeline_resolve.py` (`cycle_config` only), `application/runner/mint.py`,
`application/embedded_run.py`, `application/served_dashboard.py`,
`application/commands/dispatcher.py`, `application/cycle_addressing.py`,
`presentation/cli/commands/launch.py`, the REST resume handler, the projections link 3 names,
`tests/test_spend.py`, `tests/factories.py`, `tests/test_identity.py`, tests in modules of your
choosing, generated specs/types. NOT `application/optimizers/potter/`. A file outside Owns the
root needs: report it.

## Target
Net ≤ 0 `promptpotter/` lines. Run the `--against` legs and `--digests`; name every leg and digest
that moves, old→new.

## Report
Per `common.md`.
