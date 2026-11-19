# tests-x: a fact's store sits under its home — the data root has one source (cloud)

`briefs/common.md` binds you. Base: **the sha the dispatch names, else `ee8f1e4d1`** (origin/main; store-3b landed there: io's anchor
refuses a write into a deleted campaign, cycle or draft, matched whole from `paths.py::user_data_root()`).
Push to `arc/tests-x`. Three rows of `docs/specs/code-debt-cleanup.md` (read their full text):
§ wave-5 · store "The data root has two sources" and "A test home is built beside
`factories.workspace`", § batch/32 · tests-w "A fact's home outlives it". One commit per row at most.

## Rows
1. **"The data root has two sources."** io reads `user_data_root()` on every miss while a store carries
   `Stores.projects_root`; where they disagree every door fails open silently (the store-3 review's
   probe: a store outside the home re-makes a deleted cycle). In the suite they always disagree:
   `conftest.py::own_home` sets `<tmp>-home` BESIDE `tmp_path` while `built_stores` is
   `workspace(tmp_path)`, so the anchor is off in every fact but `test_archive.py::homed_stores`'.
   Decision: one binding source. `stores.py::build_stores` refuses a `projects_root` that is not under
   the data root (name every production builder and confirm each already passes one: the API's
   `deps.py`, the CLI, `embedded_run.py`, an inner sandbox, an exhibit's stores); every fact's store
   is built under its own home. `own_home`'s docstring gives a reason for "beside, never in"
   ("a fact's own data root there is one a landing proves file by file"): measure that cost on the
   suite (wall time of the facts that land) and either show it is gone or keep the landing off a
   fact's tree by what the landing reads, never by a second root. **Re-test:** a store built outside
   the home raises; `homed_stores` goes (every fact is homed); the store-3 review's anchor probe (a
   write into a deleted cycle through `built_stores`) raises `NotFoundError` in an ordinary fact.
2. **"A test home is built beside `factories.workspace`."** Facts in `test_spend.py` and
   `test_security.py` build ledgers, locks and homes under paths nothing minted and passed because io
   made dirs. They mint through `factories.workspace` (under the home, row 1). **Re-test:** with row 1
   in, those facts pass with no hand-built home.
3. **"A fact's home outlives it."** `tmp_path_retention_policy = "failed"` (pyproject) prunes `tmp_path`,
   but the home beside it is never pruned. With row 1 the home is under `tmp_path`, so pytest's own
   retention takes it. **Re-test:** after a green run, no `*-home` dir is left in pytest's base temp.

## Owns
`tests/**` except `tests/test_world_edges.py` (rates-4), `promptpotter/infrastructure/store/stores.py`
(`build_stores` only), `pyproject.toml`'s pytest block. NOT `promptpotter/infrastructure/store/io.py`
(its 8.3 fix lands from the desk), `promptpotter/infrastructure/llm/pricing.py`,
`promptpotter/application/jobs/**` (rates-4), `promptpotter/application/{intelligence,scoring}/**`,
`promptpotter/application/runner/round.py`, `promptpotter/application/jobs/mint.py` (small-12),
`webapp/**`, `hierarchy.py` (the operator's sidebar work). A production change beyond `build_stores`
is a row in your report, not an edit.

## Target
Each Re-test before and after. Full `python scripts/gate.py` GREEN, `offline_run.py --against` the base
UNMOVED, `--digests` identical. `promptpotter` nets ≤ 0 or each added line named; tests shrink or
each added line named. Every fact holds on Windows (the orchestrator lands there).

## Report
Per `common.md`, with a Symptoms list.
