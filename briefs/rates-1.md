# rates-1: a refresh belongs to the process that prices; the offline world raises its own fault (cloud)

`briefs/common.md` binds you. Base: `origin/main` at your start; it must hold the store landing
("a write never re-makes a campaign or cycle a delete removed": `io.py::_created` makes every
parent per level, so an install write needs no landing's mint; if main lacks it, stop and report).
Rows in `docs/specs/code-debt-cleanup.md` as they stand on `origin/main`; check each at its line
first; one that no longer names a defect is reported, not built. One commit per link, each quoting
its row's title; cut each row to what is left. A filesystem fact must hold on Windows too: no
symlink, no delete of a file a holder keeps open.

## Links
1. **A fact's home outlives it** (§ batch/32 · tests-w): `runner/mint.py` calls
   `pricing.py::refresh_rates_in_background` per mint, a daemon thread that can outlive its fact's
   `tmp_path`. Root first (the row's): decide by who READS the rates whether a refresh belongs to a
   mint at all or to the process that prices (the API's lifespan, the CLI's run, an embedded run's
   entry), and move it there, once per process, on the channel all five entry points share
   (`application/`, CLAUDE.md § entry-point parity). Name each entry point and how it gets the
   refresh. **Re-test:** the row's (no refresh thread alive past its fact). Behaviour keys and
   `--digests` stay identical.
2. **The offline world reads a fault in its own policy as a refused call** (§ wave-5 · store):
   verify it first at base (a fake policy raising `KeyError`); if the run already fails with that
   traceback, report the row closed (it says "unverified since `ea020d03f`") and delete it. Else: a
   fault inside the policy raises out of the scenario. **Re-test:** the row's.

## Owns
`promptpotter/infrastructure/llm/pricing.py`, `promptpotter/application/runner/mint.py` (the
refresh call only), the entry that takes the refresh (`main.py` lifespan, `presentation/cli/`'s run
entry, `application/embedded_run.py`: name each), `scripts/offline_run.py`, `scripts/offline_world.py`,
the rows; a fact per link in a NEW small test file. NOT `tests/factories.py`, NOT
`application/runner/{round,bench}.py`, `application/evidence/`, `application/scoring/`,
`application/intelligence/` (readings-4), NOT `application/cycle_reads.py`,
`application/read_params.py`, `presentation/api/routers/reads.py` (webd-2 lands beside you), NOT
`application/optimizers/potter/`, NOT the webapp.

## Target
Net ≤ 0 lines outside tests, or each added line named. Full `python scripts/gate.py` green;
`offline_run.py --against` UNMOVED and `--digests` identical.

## Report
Per `common.md`, with a Symptoms list.
