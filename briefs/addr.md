# addr: every address is read, never re-derived (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `addr`, arc `arc/addr`.

- **Base:** `origin/main` at `0a498a5fb` (landed 89: faults-2b, tv-2 and the typed command log). Your
  `<base sha>` in common.md is this commit. faults-2b's gate bans `import logging` in `application/`
  and `domain/`: a fault is a typed record or a served field there. A `CommandRecord.payload` is its
  kind's payload model since 89; read it typed, never as a dict.
- **Spec:** the ledger section `### batch/26 · addr`, every row (9). Re-run each row's Re-test at
  your base first; a row already closed is deleted with its evidence in the commit body.
- **The code shrinks**: the regexes, the parent counting, the browser's genealogy fold and the
  per-read sorts go. A served fact that replaces them is the substance; name its lines per link.
  Run `git diff --shortstat <base> HEAD -- promptpotter webapp` after each link.
- **Beside you:** **money-2** (cloud) owns `jobs/launcher/admission.py`, `jobs/quota.py`,
  `identity/accounts.py`, `infrastructure/llm/spend_book.py`, `served_dashboard.py` and every
  campaign load site it sweeps (`CampaignStore.campaign(id)`). You share
  `infrastructure/store/campaign_store/store.py`: you touch only its parent-counting lines
  (`:407`, `:835` at 5273b6adb), money-2 only its campaign loads and `read_standing_budget`; name the
  file in your commit body. `maintenance/landing.py` is admit-2's: leave `:256` and `:380` alone.
  Delete only your own ledger rows.

## The census (orchestrator, at 5273b6adb; re-grep it on your base)

- Sibling kind: `infrastructure/store/layout.py::sibling_kind` (7 hits), read in `runner/output.py`
  and `application/hierarchy.py`; the mint is `CampaignStore.mint_cycle` / the cycle index.
- The browser's id parsing: `webapp/lib/ids.ts` (`rootCycleId`, `shortFamilyTail`),
  `lib/hooks/useCampaignForest.ts` (`parentId`, `isInheritedSibling`), `lib/format.ts::shortId`,
  `ForestRows::shortOrigin`, `components/compare/EvidenceCharts.tsx:77`.
- Parent counting: `store/account_trail.py:114`, `campaign_store/store.py:407,835`; the allowlist the
  row names (`read_model.py:181`, `layout.py:232`, `archive_maintenance.py:57`, `config/paths.py:9`).
- The feed: `application/cycle_reads.py::feed_frames`, `application/hierarchy.py::entries_version`,
  `hierarchy_subtree.py`'s version, `read_model.py:224` (`LEDGER_INDEX_MAX`), `reads.py:214-232`,
  `jobs/machine_status.py:220,225,262`, `evidence/read.py:551-554`, `measurement_log.py:271-280`.
- Lineage: `application/hierarchy_subtree.py` (`_answering`, `_earlier`, `_descent`, `:551-555`,
  `:610-631`), `webapp/components/searchpoint/SearchpointPanels.tsx::buildSpines`,
  `webapp/lib/derivations/lineage-descendants.ts`, `lineage-candidates.ts`,
  `components/compare/channel-hooks.ts:53,103`, `webapp/CLAUDE.md:9,33`, `RunMasthead.tsx`,
  `useLeafDatasetName`.

## Decisions (orchestrator; build them, do not reopen them)

- **The mint records the sibling kind** (root | fork) on what it already writes for the cycle, and
  every reader takes it; `sibling_kind` goes. The served tree carries each cycle's parent and spawn,
  so `rootCycleId` goes. A reshape of run data wipes it (count it first), never a reader of two shapes.
- **An address is read from `Subject` / `CycleHop`**, never from a path's parents.
- **One version per subject, stamped by its writer**: the append door and the sweep keep it, a read
  version is that stamp, so no tick walks a store and no archive-wide signature stands in for a read's
  version. The row's hot-path list is checked against it; an item it does not close is reported.
- **Lineage resolves by address**: spines from the served `main_line`, invalidation from the served
  descent; `descendantsOf` and its test go, and `webapp/CLAUDE.md` stops endorsing it. "Closer to"
  and descent are folded once per candidate on the ledger, not per read.
- **The tree decides nothing**: a retired arm's reading carries its retirement and cut from the
  record that owns them (`arm_reading.py::arm_verdict` already takes `retired=`); the copies go.
- **The viewed dataset is the served subtree tag** everywhere; `useLeafDatasetName` goes.
- **A short label is served per subject and node** (`application/hierarchy.py`,
  `application/evidence/read.py`); the three browser parsers and the `split("/")` go.

## Links (one commit each, in order)

1. The recorded sibling kind and the served parent/spawn (rows 1–2).
2. Addresses from `Subject` / `CycleHop`, and the per-subject writer-stamped version (rows 3, 8).
3. Lineage by address, the retired arm, the dataset tag, the once-folded descent (rows 4–7).
4. The served short label (row 9).

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg (no run behaviour moves); digests unmoved. A landing-key move is
  named with its wipe, and an account survives.
- Each row's re-test, before and after.
- `playwright` green at the tip, with shots at 390 px of: two branches of one campaign in compare
  (both draw), and an L4 inner run's masthead naming its own dataset.
