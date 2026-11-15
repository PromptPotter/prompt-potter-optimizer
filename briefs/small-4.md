# small-4: one `RoundContext` per round; archive hygiene before the next bulk delete (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `small-4`, arc `arc/small-4`.

- **Base:** `origin/main` at `927cce1b7` (landed 90: addr, money-2 and the gate's one-thread probes). Your
  `<base sha>` in common.md is this commit. Since 90 `CampaignStore.campaign(id)` is the one refusing
  campaign read, and `StandingBudgetRecord` names only what it changes.
- **Spec:** `### batch/31 · small`, two rows only:
  - "A round opens several `RoundContext`s"
  - "Archive hygiene before the next bulk delete"

  Re-run each row's Re-test first. **Not yours:** the paper harness row, any other section.
- **The code shrinks** on the round: one context replaces five constructions and the reads they each
  re-walk. The archive's three facts are new substance; each rides an existing channel and names its
  lines. Run `git diff --shortstat <base> HEAD -- promptpotter` after each link.
- **Beside you:** **addr-2** (cloud) owns `application/hierarchy_subtree.py`, `application/hierarchy.py`,
  `application/evidence/read.py`, `infrastructure/store/layout.py`, `fork_siblings.py`,
  `campaign_store/store.py`'s stub checks and the webapp lineage files. You own `application/runner/`,
  `application/maintenance/`, `domain/sample.py`, `infrastructure/store/archive*.py` and
  `presentation/cli/commands/writes.py`. A file you both need is named in your commit body, and you
  touch only your hunk.

## The census (orchestrator, at 55765dc08 + 90; re-grep it on your base)

- **RoundContext:** `git grep -n "RoundContext(" -- promptpotter/application/runner` gives `round.py`
  `:965` (`ctx`), `:977` (`at_prior`, `settle_owed_outputs`), `:1315` (`ctx`), `:1323` (`drawn`, with
  the draw), `:1349` and `:1427` (`seat`, the controller's), `:1357` (`_adapt`, round 0), and
  `loop.py:218` (`walk_origin`). Each has its own `walks`.
- **Archive:** `application/maintenance/archive_maintenance.py`: `inventory_measurement_archive`
  (`:257`), `purge_cold_store` (`:529`; dataset-scoped, dry-run unless `apply`, refuses while
  `archive_writers` lists a producer), `_each_file`, `_refuse_unowned_home`, `reindex_measurement_archive`.
  The verb is `compact-archive` (`presentation/cli/main.py:542`, `commands/writes.py:205-230`).
  `domain/sample.py::FiledAnswer` carries `provenance: str` (a grade, not a run) and no filing run.

## Decisions (orchestrator; build them, do not reopen them)

- **One context per round, opened with its draw.** The round's draw, its controller seat, the prior
  settle and the round-0 adapt read through the one `RoundContext` the round opens. A read through it is
  seen by every reader of that round. Origin (`walk_origin`) opens its own, as round 0. Name any
  construction that stays, and why.
- **Reclaim is a mode of `compact-archive`, never a second door.** It is dataset-scoped, deletes, plans
  unless `--apply`, and refuses while a producer can append (`archive_writers`). It rides
  `_each_file` and `_refuse_unowned_home` like the purge does. Running `--apply` on the desk or the box
  is the operator's step. You prove it on a fixture home only.
- **An entry names the runs it serves.** The filing run's address (campaign and cycle) is stamped where
  the entry is filed. An L4 outer cell stamps the inner campaigns that produced it. The filing key stays
  the dataset the row measured, never the campaign that paid (`store/CLAUDE.md` § The archive is not
  scoped by campaign). A new field on the archive's wire wipes nothing that loads: name the CACHE fate.
- **The lookup reads that stamp.** `inventory` names, for each entry, the runs it serves, and reclaim's
  plan names the runs its delete breaks. It is not `pipeline_schema.py::reach_map`, which is a node's
  reach.

## Links (one commit each, in order)

1. One `RoundContext` per round.
2. The filing run stamped on an entry, the L4 outer cell's inner campaigns with it.
3. The lookup in `inventory`, then reclaim with its plan.

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg after link 1: no decision or request moves. Digests unmoved, or
  each move named old → new, by spelling alone.
- Each row's re-test, before and after. Link 1's count is `git grep -c "RoundContext(" -- promptpotter/application/runner`.
- Reclaim on a fixture home: the dry run's plan names its runs and touches nothing; `--apply` removes
  exactly the planned files; a live producer refuses both.
- A test only if it meets `tests/CLAUDE.md`'s charter; reclaim's refusal while a producer appends is
  the candidate.
