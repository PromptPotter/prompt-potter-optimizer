# rates-4: a route answer left stale is said; one step's pass has one recorder (cloud)

`briefs/common.md` binds you. Base: **`origin/main` at dispatch** (rates-3 landed there: the rates
cache carries its last try, last fetch and standing fault, and the sweep's rates step records
`RatesFile.unfetched` on `MachineStatusResponse.unswept`). Push to `arc/rates-4`. Two rows of
`docs/specs/code-debt-cleanup.md` § wave-5 · rates (read their full text); one commit per row at most.

## Rows
1. **"A route answer left stale is said nowhere."** `pricing.py::refresh_route_answers` swallows an
   outage (`PriceListUnreachableError`) and logs an `OSError` per row, returning `None`, so the sweep's
   `route-answers` step records a clean pass and a row it cannot refresh or drop stands silently.
   Decision: the refresh answers the rows it left, as `RatesFile.unfetched` does for rates (what
   failed, which route, since when), and the step records that on the existing `unswept` channel
   until a pass leaves none. An outage that keeps an answer young enough to price is still said (the
   answer it prices off and its age), never silent. **Re-test:** a route answer whose refresh raises
   `OSError` reads in `machine_status(...).unswept` under `route-answers`; the world's
   OpenRouter endpoint dead reads there too; a clean pass clears both.
2. **"One step's record has two writers."** `reaper.py::sweep_dead_cycles` records its own pass and
   `periodic_sweep`'s except-branch records a raise for the same step. Decision: a step RETURNS its
   faults and one place records a pass; a launch that calls `sweep_dead_cycles` outside the loop
   records through that same place. **Re-test:** `git grep -c "\.record_pass(" --
   promptpotter/application/jobs/reaper.py` == 1 (3 at the base).

## Owns
`promptpotter/infrastructure/llm/pricing.py` (the route-answer half), `promptpotter/application/jobs/{reaper,registry,machine_status}.py`,
the launches' `sweep(...)` calls (`jobs/launcher/launch.py:136`, `jobs/launcher/head_to_head.py:247`;
`jobs/mint.py` is small-12's), `scripts/offline_world.py` (a dead OpenRouter endpoint switch
if the re-test needs it), `tests/test_world_edges.py`, a regenerated file. NOT `tests/**` beyond that
file (tests-x), `infrastructure/store/**`, `application/{intelligence,scoring}/**`,
`application/runner/round.py` (small-12), `webapp/**` except a regenerated file. If the CLI or webapp
must change to say it, do not: report it as a row.

## Target
Each Re-test before (red) and after. Full `python scripts/gate.py` green, `offline_run.py --against` the
base UNMOVED, `--digests` identical. `promptpotter` nets ≤ 0 or each added line named. Every new fact
holds on Windows.

## Report
Per `common.md`, with a Symptoms list.
