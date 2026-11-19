# rates-5: what rates-4 left (cloud)

`briefs/common.md` binds you (read BASE FIRST). Base: **the sha the dispatch names** (origin/main).
Push to `arc/rates-5`. Your rows: `docs/specs/code-debt-cleanup.md` § `wave-5 · rates (what rates-4
left at its landing)`, BOTH rows (read their full text): **A sweep's reap is observed by its count**
and **A route outage is remembered per process**. One link per row. Runs beside `cfg-3-fix` (owns
`domain/campaign_config.py`, `domain/bench.py`, `domain/judges.py`, `assets/datasets/`, the dataset
yamls) and `store-4` (owns `infrastructure/store/stores.py`, `infrastructure/store/io.py`,
`scripts/offline_run.py`). Shared file: `tests/test_security.py` (cfg-3-fix may touch it): keep your
edit there to the reaper fact. Do not touch `application/config_map.py` or `scripts/offline_world.py`.

## The rows
1. The sweep returns its faults; the facts read the reaped cycles' `stop_reason`, never a count.
   **Re-test:** `git grep -c "nonlocal" -- promptpotter/application/jobs/reaper.py` == 0.
2. A route outage is a fact of the route answer on disk (its `at` already dates the try), read by
   every process; the process-global memo goes. **Re-test:** `git grep -c "_ROUTE_OUTAGE" --
   promptpotter` == 0, and a fact: a second process inside the outage window does not re-fetch.

## Proof
Equals-main per `common.md` (UNMOVED; behaviour keys unchanged or each move named old → new). Full gate
green. Report on `out/rates-5-report` (Links, Re-tests, Proof, Decided in-run, Rows closed, Rows to
file, Symptoms).
