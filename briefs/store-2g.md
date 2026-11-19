# store-2g: store-2f's fix pass — the anchor by its place, a bounded climb (cloud)

`briefs/common.md` binds you. Base: **`origin/arc/store-2f`** (3cfdb22f4; read `briefs/store-2f.md`,
whose invariant stands). Rebase it onto `origin/main` first (main moved: `scripts/complexity_ledger.py`'s
`package_lines` conflicts; keep both reasons, the ceiling ends at the measured count), then build
below; push the result to `arc/store-2g`. An independent review of store-2f, run on Windows, found
these CONFIRMED (probes beside this brief: `briefs/store-2g-anchor-probe.py`,
`briefs/store-2g-junction-probe.py`; on main every case in them is OK):

## Fixes
1. **The anchor is recognized by a directory NAME** (`io.py:257`,
   `_DELETED_UNITS.get(path.parent.parent.name)`), so any missing dir under a parent named `campaigns`
   or `cycles` refuses, wherever it sits:
   - a dataset slug named or containing `cycles` / `campaigns` (`_DATASET_NAME_RE` admits it): its
     definition commit (`stores.py:183`, `.staged/`) answers 404 "cycle '.staged' is gone" forever;
   - a harbor panel under `materialized/cycles/`;
   - a `PROMPTPOTTER_HOME` (or a checkout / worktree) not yet created inside a folder so named: every
     first write refuses;
   - a tenant named `cycles` / `campaigns`: an L4 inner seat's root mint in a fresh sandbox.
   Decision: the anchor is recognized by its PLACE in the layout, never its name: `campaigns/<id>`
   only where it is a workspace's campaigns root (`campaigns_root_dir_for`'s: the data root's
   `projects/<tenant>/campaigns/` and an inner sandbox's), and `cycles/<cid>` only at
   `<that campaigns root>/<id>/cycles/`. The module that spells the layout owns the test (if
   `layout.py` imports `io.py`, the shapes move down beside `CAMPAIGNS_DIR`, spelled relative to the
   data root); the path is normalized first (`..` reaches no anchor unseen; the review's latent case:
   an append through `cycles/a/../b/.runtime/` re-made the deleted `b`). **Re-test:** the anchor probe
   reads OK for every case, and a fact over the same table (a dataset named `cycles`, a home inside a
   `campaigns` folder, a tenant `campaigns`, a deleted campaign and a deleted cycle) — the deleted two
   refuse, the rest write.
2. **`_created` / `make_dirs` recurse without bound** (`io.py:258-268`) when a level exists but is not
   a directory (a regular file, a dangling junction; a dangling POSIX symlink by the code): the create
   raises `FileNotFoundError`, `mkdir` raises `FileExistsError` (suppressed), forever →
   `RecursionError`, which no `OSError` handler catches (main: a plain `FileNotFoundError`). Action:
   one climb, then one retry of the create; a second failure raises the original error.
   **Re-test:** the junction probe on Windows, and a fact with a regular file standing at a level:
   the write raises an `OSError`, never `RecursionError`.
3. **The reaper aborts on a delete between `mkstemp` and `os.replace`** (the same on main):
   `_reap` catches `NotFoundError` but not that `FileNotFoundError`. Action: a cycle deleted under the
   sweep is skipped however the write learns it; the sweep never aborts. **Re-test:** a fact.
4. The brief's old `store-2f-probe.py` imports `mint_install`, which exists nowhere: drop that import
   (the anchor probe covers the same writers).

## Owns
store-2f's Owns. NOT the webapp, `application/cycle_reads.py` (webd-2), NOT
`application/intelligence/rasch.py`, `domain/{scoring,paired_reading,ruler,crowning_bar,
verification}.py`, `application/scoring/paired.py`, `application/evidence/comparison.py` (readings-3),
NOT `application/optimizers/potter/`.

## Target
The fixes net ≤ 0 or each line named. Full `python scripts/gate.py` green, `offline_run.py --against`
UNMOVED, `--digests` identical; the two probes' output in the report (the junction one can fail only
on Windows: reason it from the code if you cannot run it, and say so).

## Report
Per `common.md`, with a Symptoms list.
