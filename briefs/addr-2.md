# addr-2: lineage resolves by address; a retired arm, a short label and a family root are served (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `addr-2`, arc `arc/addr-2`.

- **Base:** `origin/main` at `927cce1b7` (landed 90: addr, money-2 and the gate's one-thread probes). Your
  `<base sha>` in common.md is this commit. Since 90 a cycle's kind is its recorded mint
  (`CycleIndex.minted_as`), a cycle is listed by its `CycleHop`, the viewed dataset is the leaf's
  served subtree tag, a cycle's short name is its served `label`, and `CampaignStore.campaign(id)` is
  the one refusing campaign read.
- **Spec:** the ledger section `### batch/26 · addr`, four rows only:
  - "Lineage spines key on `facts.individual` and on the origin"
  - "The tree rewrites a retired arm's election"
  - "A campaign's short label is parsed two ways"
  - "A cycle's family root is still parsed from its id"

  Re-run each row's Re-test at your base first. **Not yours:** "sorts every candidate per candidate"
  (a record kind, its own arc), "The feed's version spans a whole store" (a version chain of its own),
  "The reaper swallows an unreadable cycle" (faults 3). Delete only your own rows.
- **The code shrinks**: `descendantsOf` and its test, the per-id `_answering` pick, the browser's
  genealogy folds, the retired-arm copies, the label parsers and the `_fork_` regex go. The served
  fact that replaces them is the substance; name its lines per link. Run
  `git diff --shortstat <base> HEAD -- promptpotter webapp` after each link.
- **Beside you:** **small-4** (cloud) owns `application/runner/`, `application/maintenance/`,
  `domain/sample.py`, `infrastructure/store/archive*.py` and `presentation/cli/commands/writes.py`.
  You own `application/hierarchy_subtree.py`, `application/hierarchy.py`,
  `application/evidence/read.py`, `infrastructure/store/layout.py`,
  `application/bench/resume_and_fork/fork_siblings.py`, `campaign_store/store.py`'s stub checks
  (`_stub_deletion_blocked`, `stub_forks`) and the webapp files below. Any other file you both need is
  named in your commit body, and you touch only your hunk.

## The census (orchestrator, at 55765dc08 + 90; re-grep it on your base)

- Lineage: `hierarchy_subtree.py` (`_answering`, keys at `:610-631`, `_descent`),
  `webapp/components/searchpoint/SearchpointPanels.tsx::buildSpines` (`parent_ids[0]`),
  `webapp/lib/derivations/lineage-descendants.ts`, `lineage-candidates.ts:107,123`,
  `components/compare/channel-hooks.ts:53,103` (the `mine[0]` fallback), `webapp/CLAUDE.md:9,33`.
- The retired arm: `hierarchy_subtree.py:551-555` (three `model_copy`s), `domain/arm_reading.py::arm_verdict`
  (takes `retired=`), `webapp/components/candidates/lineage-tree.ts:84-85,248` (crowns off
  `election.selected` / `crown`).
- The label: `webapp/lib/format.ts::shortId`, `ForestRows::shortOrigin`,
  `components/compare/EvidenceCharts.tsx:81` (`c.split("/").pop()`); 15 hits at addr 4.
- The family root: `infrastructure/store/layout.py::root_cycle_id` (`_SIBLING_SEP_RE`), read at
  `campaign_store/store.py::_stub_deletion_blocked` / `stub_forks` and `fork_siblings.py::_sibling_id`;
  `Campaign.root_cycle_id` and `CycleIndex.minted_as` already hold the fact.

## Decisions (orchestrator; build them, do not reopen them)

- **Lineage resolves by address.** Spines come from the served `main_line`; invalidation comes from the
  served descent. `descendantsOf` and its test go, and `webapp/CLAUDE.md` `:9` and `:33` stop
  endorsing it. A subject is keyed by its address, never `facts.individual`, which repeats across cycles.
- **The tree decides nothing.** A retired arm's served reading carries its retirement and its cut from
  the record that owns them (`arm_verdict(retired=)`). The browser reads that served field before the
  three copies go, so no retired arm is crowned in between.
- **A short label is served per subject and per node** (`application/hierarchy.py`,
  `application/evidence/read.py`). The two parsers and the `split("/")` go. Two datasets' cells never
  share a label.
- **A family root is read, never parsed.** The stub checks read the cycle index they already load, and
  the sibling id takes the campaign's `root_cycle_id`. `root_cycle_id()` and `_SIBLING_SEP_RE` go.
  A reshape of run data wipes it (count it first), never a reader of two shapes.

## Links (one commit each, in order)

1. The family root read from the index (its row).
2. The served retired arm, the browser on it, then the copies gone.
3. Lineage by address: spines, invalidation, `descendantsOf` gone.
4. The served short label.

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg; digests unmoved. A landing-key move is named with its wipe.
- Each row's re-test, before and after.
- `playwright` green at the tip, with shots at 390 px of two branches of one campaign in compare
  (both draw) and of a retired arm in the lineage tree (not crowned).
