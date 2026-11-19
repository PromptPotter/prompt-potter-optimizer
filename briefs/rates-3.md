# rates-3: a dead price list is said where the sweep's steps are said (cloud)

`briefs/common.md` binds you. Base: **`origin/main`** (fbb346122; rates-2 landed there: the home's rates
file is the once-a-day key, the API's sweep owns the refresh, `reaper.py::_PRICES`). Push to
`arc/rates-3`. Two rows of `docs/specs/code-debt-cleanup.md` (read their full text): § wave-5 · rates
"A dead price list is said nowhere" and "The offline world has no dead upstream" (it rides here, not
with tests-x: this chain's re-test needs it).

## Rows
1. **"A dead price list is said nowhere."** `pricing.py::refresh_rates` logs a failed fetch, stamps the
   rates file fresh and returns `False`; `reaper.py::_refreshed` records a clean pass whatever it
   returned, and the stamp keeps every later tick clean for a day. Decision: the refresh answers the
   fault it hit (a typed answer or a raise, never a `bool` a caller may drop), and as long as the list
   it prices off is not a successful fetch's, that fault stands: the step's pass records it on the
   existing channel (`JobRegistry.record_pass` → `MachineStatusResponse.unswept`, which the CLI already
   prints), naming the fault and the age of the last SUCCESSFUL fetch (or "the bundled floor" where
   the home never fetched). The age is the rates file's own fact, never an mtime a failed attempt
   stamped: decide where it lives (the file's body, `RatesFile`, is a cache the loader drops when it
   does not load, so its shape may change freely; say so) and keep "a dead upstream costs one timeout
   a day". A launch that prices stays as rates-2 left it. Make `unswept`'s description true (it names
   "the sweep, the route answers").
2. **"The offline world has no dead upstream."** `scripts/offline_world.py` gains an
   upstream-unreachable switch and a fetch count beside its other policies, so a fact stops patching
   `pricing.UPSTREAM_URL` and reading `Router.unrouted`. Re-point the facts in `tests/test_world_edges.py`
   that patch it.

**Re-test** (one fact, `tests/test_world_edges.py`): with the world's upstream dead, a sweep's rates
step reads in `machine_status(...).unswept` with the fault and the list's age on every tick until a
fetch succeeds, the world counts ONE fetch per day of ticks, and a successful fetch clears it.

## Owns
`promptpotter/infrastructure/llm/pricing.py`, `promptpotter/application/jobs/{reaper,machine_status,registry}.py`
(registry: the step-pass record only), `scripts/offline_world.py`, `tests/test_world_edges.py`, a
regenerated file. NOT `presentation/cli/**`, `infrastructure/store/**`, `application/jobs/mint.py`,
`application/cycle_reads.py` (store-3b); NOT `application/runner/inner/**`,
`infrastructure/run_scope.py`, `infrastructure/llm/telemetry.py`, `run_observers.py` (inner-1); NOT
`application/{scoring,bench,evidence}/**`, `shared/statistics.py`, `domain/cell_fold.py`,
`tests/{test_scoring,test_ruler}.py` (readings-6); NOT `webapp/**` except a regenerated file. If the
CLI or webapp must change to say it, do not: report it as a row.

## Target
The Re-test before (red) and after. Full `python scripts/gate.py` green, `offline_run.py --against` the
base UNMOVED, `--digests` identical. `promptpotter` nets ≤ 0 or each added line named. Every new fact
holds on Windows.

## Report
Per `common.md`, with a Symptoms list.
