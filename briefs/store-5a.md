# store-5a: what the store reads, once (cloud · lane beside W1)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is **`origin/land/w5i`
at the sha the dispatch names**, the stack main takes next. Push to `arc/store-5a`.

A lane: no key, digest, id or persisted shape moves. Equals-main is UNMOVED in every digest; a move
fails the link. A CACHE file a fix makes stale (the pickled fold snapshot) is dropped by its own key,
never migrated: name it.

## Rows, one link each, in this order (read each row's full text in the ledger at your base)
1. **A round close re-folds the bank's whole archive** (§ batch/26 · rk-opt): a round close folds
   only what it added (`domain/sample.py::SampleIndex.refresh`, `store/measurement_archive.py`).
2. **The cycle index reads its chain beside the store's** (§ hubs): one reader of a cycle's chain
   (`projections/cycle_index.py`, `store/campaign_store/store.py`).
3. **Generated `last` record classes live in module globals so a snapshot can pickle them** (§
   batch/27 · rec): `store/read_model.py::last_fold`.
4. **A machine's cell ceiling is counted twice** (§ Held): one count of one machine fact
   (`infrastructure/backend.py::MachineSlots` and flight-share's in-process count).

NOT yours: **On Windows a replace is refused while a reader holds the file** (Windows-only; it is
built on the desk).

## Owns
`domain/sample.py`, `store/measurement_archive.py`, `store/read_model.py`,
`projections/cycle_index.py`, `store/campaign_store/store.py`, `infrastructure/backend.py`, the
flight-share module the census finds, and their tests. Beside you run **bkeys-3**
(`optimizers/nodes.py`, `runner/inner/tasks.py`, `domain/paired_reading.py`, `domain/results.py`,
`domain/arm_reading.py`, `scripts/offline_run.py`, `scripts/offline_world.py`,
`maintenance/landing.py`) and **addr-3** (`datasets/authored.py`, `domain/layer_fold.py`,
`domain/command_kinds.py`, `jobs/mint.py`, `pipeline_resolve.py`, `assets/datasets/defaults.yaml`,
`store/llm_reuse_cache.py`). Do not edit theirs; a needed edit there goes to "Rows to file".

## Proof
Equals-main per `common.md` against the base: UNMOVED (decisions, requests, digests). Full gate
green. Report on `out/store-5a-report` (per link the commit and +added/−deleted; re-tests
before/after; decided in-run; Rows closed; Rows to file; Symptoms).
