# load-3: a mint creates its cycle; a launch's address has one source (cloud)

`briefs/common.md` binds you. Base: `origin/main` (with `489649771`, load-2). The rows are
`docs/specs/code-debt-cleanup.md` § batch/31 · small: "A cycle's directory is minted by whichever
append reaches it first", "A launch's address is taken from two sources", "Two pass paths load the
campaign twice, and an unbound session is guarded three ways". One link per row, in that order;
quote the row's title in each commit and delete or cut the row the link closes.

## Links
1. **Mint creates the cycle dir.** Today no mint door creates `cycles/<id>/`: the first
   `ledger.py::CycleEventLog.open` (`mkdir(parents=True)`) does, so an append after a
   `delete-campaign` rmtree re-creates a manifest-less campaign. Grep every door whose append is a
   cycle's FIRST write (root mint `jobs/mint.py`, forks `runner/mint.py` + `fork_siblings.py`, the
   inner sandbox `runner/inner/spawn.py`, drafts `draft_store.py` / `bench/task_context.py`, any
   `seats/*` hop, `initialization/scoring_scope.py`) and list them in the commit; each creates its
   dir at the mint; `open` creates `.runtime/` alone; `CampaignStore.append` on a vanished cycle
   raises a typed `NotFoundError`. **Re-test:** the row's.
2. **One address pair.** `Session.bound(hop, *, campaign)` and `quota.py::CampaignCycle(campaign,
   hop)` take the campaign id twice. Action: the carrier is (campaign, cycle id) with the hop derived,
   and `CampaignCycle` folds into the pair the launch already holds (`ExistingCycle` /
   `DispatchedToCycle`) or replaces them: one shape. **Re-test:** the row's.
3. **The row's passes and guards.** **Re-test:** the row's.

## Owns
`promptpotter/infrastructure/ledger.py`, `infrastructure/store/campaign_store/store.py`,
`infrastructure/store/draft_store.py`, `application/initialization/{session,wiring,scoring_scope}.py`,
`application/jobs/{quota,mint}.py`, `application/jobs/launcher/*`, `application/runner/*`,
`application/bench/*`, `application/scoring/passes.py`, `application/served_dashboard.py`,
`application/hierarchy.py`, `application/evidence/head_to_head.py`,
`application/commands/limits_and_queue.py`, `tests/factories.py`, `tests/test_identity.py`,
`tests/test_archive.py`, and these facts in `tests/test_spend.py`: the `CampaignCycle` helper
(`:471`) and `:4674`/`:4710`. NOT `application/optimizers/potter/` (it moves the L4 key), NOT
`infrastructure/llm/*`, `scoring/sample_measurement.py`, `connectors/harbor.py`,
`scoring/judges/call.py`, `infrastructure/backend.py` (err-1 runs beside you on them). **Shared
with err-1:** `tests/test_spend.py`, your facts above only.

## Target
`promptpotter/` net ≤ 0 across the three links. No digest moves, no wipe: say so with the command
that showed it. `python scripts/gate.py --py` green.

## Report
Per `common.md`, with a Symptoms list for anything off-row you saw.
