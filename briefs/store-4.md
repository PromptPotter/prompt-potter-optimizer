# store-4: what tests-x left in the store (cloud)

`briefs/common.md` binds you (read BASE FIRST). Base: **the sha the dispatch names** (origin/main).
Push to `arc/store-4`. Your rows: `docs/specs/code-debt-cleanup.md` § `wave-5 · store (what store-2f,
store-2g and store-3b left at their landing)`, ONLY the two rows from tests-x's report (read their
full text): **A store under the data root off its workspace shapes** and **io's path spelling is
private**. One link per row. Runs beside `cfg-3-fix` (owns `domain/campaign_config.py`,
`domain/bench.py`, `domain/judges.py`, `assets/datasets/`, the dataset yamls; it may touch one
`BENCH` line of `scripts/offline_run.py`: leave that line alone) and `rates-5` (owns
`application/jobs/reaper.py`, `infrastructure/pricing.py` or wherever `_ROUTE_OUTAGE` lives). Do not
touch `application/config_map.py` or `scripts/offline_world.py`.

## The rows
1. The parent reads a leg through the leg's own process (`scripts/offline_run.py::interrupted`), then
   `build_stores` matches `io.py::WORKSPACE_SHAPES` and refuses any other root. **Re-test:**
   `build_stores` on `<home>/projects/x/projects` raises.
2. io names its path spelling publicly; `stores.py` imports the public name. **Re-test:**
   `git grep -c "import.*_plain" -- promptpotter` is 0 outside io.

## Proof
Equals-main per `common.md` (UNMOVED; every digest move named old → new). Full gate green. Report on
`out/store-4-report` (Links, Re-tests, Proof, Decided in-run, Rows closed, Rows to file, Symptoms).
