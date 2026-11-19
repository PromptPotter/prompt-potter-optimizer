# store-2: a write never re-makes a deleted campaign; the launch pair lives in `domain/` (cloud)

`briefs/common.md` binds you. Base: `origin/main` (with `263c47187` load-3). The rows are
`docs/specs/code-debt-cleanup.md` § batch/31 · small: "A write re-makes a deleted cycle's
directories" (link 1) and "A launch's (campaign, hop) pair is spelled again below `application/`"
(link 2). Quote the row's title in each commit; delete or cut the row the link closes. Check every
site at its line first; one that no longer reads as stated is reported, not built.

## Links
1. **A write creates its leaf alone.** `infrastructure/store/io.py::_tmp_beside` (`os.makedirs`,
   `:248`) and `io.py::_open_lock` (`:486`) make the whole path, so a held ledger handle, a
   `write_json` and a `take_lock` each re-create `campaigns/<id>/` after a `delete-campaign`; a fork
   mint's `campaign_store/store.py:509` `mkdir(parents=True)` runs after its cut. Action: io makes
   no directory chain (the leaf's parent must exist, or the write raises a typed not-found); every
   door that makes a chain is a mint or a first write that names itself (grep every caller that
   relied on io's `makedirs`: account dirs, dataset banks, caches, `.inner/`, the job log, drafts,
   the landing; list each in the commit with the door that now makes its dir). The doors the row
   names (set-limits' cycle-axis appends and arm act outside the admission gate, the reaper's
   `stores.py::declare_ended`) answer not-found on a deleted cycle. **Re-test:** a fact: after a
   delete, a held handle's append, `write_json` and `take_lock` under the cycle each raise and leave
   no `campaigns/<id>/`; and `python scripts/offline_run.py` scenarios all UNMOVED.
2. **The carrier is a domain type.** `CampaignStore.armed_budget` / `run_limits`,
   `pipeline_resolve.py::cycle_config` and `admission.py::campaign_sends` take (campaign, hop) because
   infrastructure cannot import `quota.py::CampaignCycle`. Action: the pair moves to `domain/`
   beside `Campaign` and every reader takes it; `admission.py:391`'s re-read before
   `refuse_campaign` is either named a fresh read under the gate (a comment saying what it guards)
   or deleted. **Re-test:** no (campaign, hop) parameter pair left in `infrastructure/store/`.

## Owns
`promptpotter/infrastructure/store/**`, `promptpotter/infrastructure/ledger.py`,
`promptpotter/domain/campaign.py` (or a new `domain/` module for the pair),
`application/jobs/{quota,mint}.py`, `application/jobs/launcher/*`, `application/commands/*`,
`application/pipeline_resolve.py`, `application/bench/resume_and_fork/*`, `application/runner/*`,
any caller of io whose dir it now makes, their tests (`tests/test_archive.py`, `tests/test_spend.py`'s
set-limits facts, `tests/test_identity.py`, `tests/factories.py`). NOT
`application/optimizers/potter/` (it moves the L4 key), NOT `connectors/*`,
`infrastructure/llm/*`, `application/scoring/sample_measurement.py` (send-2 runs beside you on
them). **Shared with send-2:** `tests/test_spend.py`, your set-limits facts only.

## Target
`promptpotter/` net ≤ 0 per link, or each added line named. No digest moves and no wipe: say so
with the command that showed it. `python scripts/gate.py --py` green.

## Report
Per `common.md`, with a Symptoms list for anything off-row you saw.
