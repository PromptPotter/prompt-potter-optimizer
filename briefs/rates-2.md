# rates-2: the rates refresh keyed on the file, owned where it is priced (cloud)

`briefs/common.md` binds you. Base: **`origin/main`** at dispatch. Row: `docs/specs/code-debt-cleanup.md`
§ "wave-5 · rates (what rates-1 left at its landing)", its one row "The rates refresh is keyed on the
process, the rates on the home". Read `briefs/rates-1.md` (its invariant stands: no refresh thread
outlives its launch, and a launch prices off what upstream lists).

## The fix
- `pricing.py::refresh_rates_once` (a `functools.cache` keyed on nothing) goes. The file's mtime is
  already the once-a-day key (`refresh_rates`'s TTL check): every refresher calls that.
- Who refreshes: every process that PRICES. A launch (`runner/mint.py::open_launch`, as now) and the
  API process: `application/jobs/reaper.py::periodic_sweep`, the API's one periodic owner, refreshes
  the rates on its tick (off the loop), so an API that launches nothing still prices off rates at
  most a day old. An embedded host is covered by its launches.
- A failing upstream costs at most ONE fetch timeout per TTL per home, never one per launch or per
  sweep tick: record the failed attempt where the freshness check reads it (keyed on the home's
  rates file, not the process). Decide how, the smallest that holds; say it in the commit.
- The once-cache's test seam goes with it: `tests/test_world_edges.py`'s `cache_clear()`, and
  `tests/test_security.py:3276`'s `pricing.cache_path` patch if the root makes it needless (that
  hunk only; store-3 owns the rest of that file).
- The row's own Re-test on main is wrong (`git grep -c "cache_clear" -- tests` also counts
  `send_pacing`'s): use `git grep -c "refresh_rates_once" -- promptpotter tests` is 0.
**Re-test:** an API process (the sweep, no launch) refreshes a day-old `rates.json`; a second sweep
tick against an unreachable upstream within the TTL makes no fetch; `refresh_rates_once` is gone.

## Owns
`promptpotter/infrastructure/llm/pricing.py`, `promptpotter/application/jobs/reaper.py`,
`promptpotter/application/runner/mint.py` (the refresh call only), `tests/test_world_edges.py`,
the one `tests/test_security.py` hunk named above, and the docs they make stale. NOT
`infrastructure/store/**`, `application/{cycle_reads,pipeline_resolve}.py`, `application/jobs/mint.py`,
`application/maintenance/**`, `presentation/cli/**` (store-3); NOT `application/{scoring,bench,
evidence,intelligence,optimizers}/**`, `domain/{scoring,results,verification}.py`,
`tests/{factories,test_identity,test_ruler,test_scoring,test_spend}.py` (readings-5).

## Target
`promptpotter` nets ≤ 0 or each added line named. The Re-test before and after. Full
`python scripts/gate.py` green, `offline_run.py --against` UNMOVED, `--digests` identical. Every
new fact holds on Windows.

## Report
Per `common.md`, with a Symptoms list.
