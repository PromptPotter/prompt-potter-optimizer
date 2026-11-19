# store-2f: a write never re-creates what a delete removed (store-2's fix pass, cloud)

`briefs/common.md` binds you. Base: **`origin/main`** at your start (549291d40 or later), NOT
`arc/store-2`. Push to `arc/store-2f`. store-2 (`origin/arc/store-2`: link 1 `508fa656e`, link 2
`99bad20ba`) failed its review: its link 1 rule ("a write creates its leaf alone; the mint doors own
every chain") needs a complete inventory of writers that nothing enforces. About 25 sites were
patched with `with_parents`, at least 3 were missed, and the gate stayed green because conftest
refuses the network. Reproduced on that branch, and fine on main:
- `<workspace>/.cache/model_capabilities.json` (`capabilities.py:275`, reached by every session
  opener, an L4 inner sandbox's included, and every check-in) is never made.
- `jobs/machine/<connector>/` (`backend.py:118/123`) is never made, so harbor and dbllmbench cells
  fail at `MachineSlots.hold`.
- `harbor_panel.py:39` deletes `tasks/` and then writes nested files, and `loaders.py:351` writes into
  `materialized/<prefix>/`; neither directory is made.
- `with_parents` drops `_long_path`, so a 344-character first write raises `WinError 206`.
The probes are beside this brief: `briefs/store-2f-probe.py` and `briefs/store-2f-longpath.py`. The
long-path one can only fail on Windows, so run it there or reason it out from the code.

## Decision: the narrower invariant
**A write never creates the directory a delete removes.** A delete removes a campaign
(`campaigns/<id>/`) or a cycle (`cycles/<id>/`; `cleanup-empty-cycles` removes a cycle and keeps its
campaign). A write under a campaign or a cycle names that directory as its ANCHOR, which must already
exist. `io.py` makes every level below the anchor with a per-level `mkdir`, walking down from the
anchor in long-path form. It never makes the anchor itself, and it never checks existence first and
then calls `makedirs`: a delete landing in between would re-create the anchor. A level whose parent
vanished raises `NotFoundError`. Every other write (caches, machine slots, panels, survivors,
install files) makes its parents as main does today, through the one long-path `makedirs`.

1. **Count first.** Find the call paths that name an anchor (the `CampaignStore` / `CycleEventLog`
   doors, a fork mint, the reaper's `declare_ended`, set-limits' appends and arm act). Put the count
   in the commit. If the count is in the dozens, not a handful of store doors, the shape is wrong
   again: report it before you build.
2. **Re-test the invariant:** store-2's delete fact (each of the row's four writers after a delete
   raises and leaves no `campaigns/<id>/`), plus a cycle delete: after `cleanup-empty-cycles`, a
   writer to that cycle leaves no `cycles/<id>/`.
3. **Re-test the old model's absence:** `with_parents`, `_parent_gone` and `mint_install`'s prefix
   walk are gone, or counted and named in the commit. The probes pass. Add ONE fact for the silent
   class: a first write of the capabilities cache on a fresh data root, with the fetch faked to
   succeed (conftest's network refusal is why the suite missed it).
4. **Link 2, carried over:** cherry-pick `99bad20ba` (the `CampaignCycle` carrier in `domain/`) onto
   your link 1, then finish it. The review found the pair still spelled in these places:
   - `launching.py:326` unpacks the pair and `:171` packs it again.
   - `line_config` (`pipeline_resolve.py:1150`), `read_cycle_bench` / `cycle_verify`
     (`bench.py:862/909`), `cycle_hard_samples` (`measurement_log.py:334`) and `_cycle_facts`
     (`read.py:810`) still take `(campaign, hop)`.
   - There are 19 construction sites. Each one takes the carrier, or says why it cannot.
   - `cycle_config(Campaign | CampaignCycle)` picks its meaning by type: split it into two named
     functions.
   - `admission.py` past the queue wait: `campaign_refusal` reads the definition fresh off the dataset
     store, and the claim holds off an archive and a delete, so it asks `refuse_campaign(stores,
     at.campaign)` with no reload. Keep main's comment meaning ("never a definition moved while the
     job waited").
5. **What survives either rule:** a delete landing mid-sweep is expected, so the reaper
   (`reaper.py:62-66`, `sweep_dead_cycles`) skips a cycle deleted under it and never aborts the
   sweep. `readout.py:179-185` and `read_model.py::_stash` answer a missing directory as main does
   (`NotFoundError` is not an `OSError`). `run_job.py:121`'s chain mkdir, `accounts.py:122-123` and
   `closed_rounds.py:178`'s raw mkdir go through `io.py`. `wallet_store.py::_rewrite` writes on a
   missing account dir. `_parent_gone`'s 404 names the campaign or cycle id, never an absolute path.

## Owns
store-2's Owns (`brief/wave-5:briefs/store-2.md`) plus every file the fixes above name. NOT the webapp
(live-3f), NOT `application/optimizers/potter/`, NOT `infrastructure/llm/send_failure.py` /
`openai_compat.py`'s send path.

## Target
Production lines net ≤ 0, or each added line named. The anchor rule should be smaller than link 1;
say by how much. Full `python scripts/gate.py` green, `offline_run.py --against` UNMOVED, `--digests`
identical, both probes green.

## Report
Per `common.md`, with: the anchor count, the probes' output, and every writer whose behaviour moved.
Add a Symptoms list.
