# tw-r2: a press moves the bill, never the running campaign's record, and memory still outlives campaigns (cloud)

`briefs/common.md` binds you (read BASE FIRST), with ONE override. The base is
**`origin/land/test-world` at `3625a6687`**. That is `9cd30b5a7` plus:
- tw-m, the app's memory;
- my gate fix `2927d903c`, by which ts-types declares the served contract it writes;
- tw-r's links 1, 2 and 4 from `arc/tw-r`: the catch-up root, the arrival leg, and the cap-red clear.

Replace the ancestor check with `git merge-base --is-ancestor 9cd30b5a7 3625a6687`. Equals-main runs against
`3625a6687`'s offline home.

Push to `arc/tw-r2`, one commit per link, with the full gate green at the tip.

## Why tw-r's link 3 was not taken
tw-r's link 3, `67b68fa46` on `arc/tw-r` (read its diff and `out/tw-r-report`), fixed bug 3. A press used
to move potter's L2 request: the cells it bought past a close entered the next round's `SampleIndex`, so
persistent failures went from 4 to 5. The fix narrowed OPTIMIZE memory (`archive_queries.py`, `OpenAt`)
to the answers that some campaign's walk ledger names.

That breaks what memory is for:
- `store/CLAUDE.md` § The archive is not scoped by campaign: the archive "outlives the campaigns that
  filled it";
- `decisions.md` § 0009: optimize reads past the run;
- § 0011: run data is disposable and a cache is kept.

After link 3, deleting a campaign (routine sidebar cleanup) or wiping run data empties `SampleIndex` and
`measurement_log::_dataset_cells`, while the archive still replays every one of those cells. Nothing
reports it. Link 3 also scanned every campaign's walk spans on each population read.

**The invariant is narrower: a press moves the bill, never the RUNNING campaign's record.** A cell another
campaign measured is a fact about (configuration, sample) whyever it was bought, and it stays memory. Only
the running campaign's own cells, bought but not taken by its record, are kept out of its own memory. My
tw-r brief said "never evidence the record took", which read broader than meant.

## Links
1. **Pin the objection.** One world edge (`tests/world.py`, extended in this link if it must be):
   - campaign A runs through the real entry;
   - A is deleted through its production door (the delete verb or command, not a rmtree);
   - campaign B on the same dataset then runs, and its memory still holds A's cells. Read that through a
     production door: B's L2 request's persistent failures, or `SampleIndex` built at B's scope.

   Prove it twice: GREEN at `3625a6687` and RED with `67b68fa46`'s `archive_queries.py` applied. Paste both.
2. **Make the bug a failure.** `read_warm` FAILS a moved optimizer request: take `67b68fa46`'s
   `offline_run.py` hunk. `potter/pressed` turns RED (`L2ContextOutput_0` moved, persistent failures
   4→5). Paste the red.
3. **The root.** Keep the running campaign's own bought-but-untaken cells out of its own memory, and nothing
   else. Find what carries "the running campaign's own" from what exists. Check in this order and report
   each finding:
   1. the `role` and `provenance` a press-bought cell is filed under today (`domain/sample.py::FiledAnswer`,
      the filing in `application/scoring/**`);
   2. whether a later walk taking a banked cell re-files it or only names it in the ledger;
   3. what the running campaign's ledger and scope already name: its walks, its catch-ups, its bills and
      priced keys, `CampaignWalks`, and `OpenAt`, which today carries no campaign.

   A new ledger record or field is the last resort, because it is a new concept. If you need one, it names
   what the walk or press BOUGHT, and the commit body says why nothing existing carries it.

   A cell that arrives before the close and is never taken counts as bought-but-untaken too, so filing time
   alone does not decide it.

   **Accepted when, together:**
   - link 1's pin is green;
   - `potter/pressed` is UNMOVED, requests included;
   - every other leg is UNMOVED against `3625a6687`.

   The fix in a scope's reader costs no scan of other campaigns' ledgers per population read. Measure one
   `SampleIndex.refresh` on a home with 20 campaigns, before and after.
4. `behaviour_keys.json` and the `surface-ledger` ceilings, at your green tip, with the reasons. I
   regenerate both again at integration.

## Owns
- In production: `infrastructure/store/archive_queries.py`, `application/intelligence/indexes/sample.py`,
  `application/scoring/**`, `application/initialization/scoring_scope.py`, `domain/sample.py`, and
  `domain/records/**` only for the last-resort record;
- in docs: `docs/concepts/scoring-and-memory.md` and `infrastructure/store/CLAUDE.md`, where the wording
  states the narrower invariant;
- `scripts/offline_run.py` (`read_warm` and what the pressed leg reads) and `tests/world.py`;
- one test file, the one owning the archive or memory edge (`tests/test_archive.py`), for link 1's pin
  only.

**NOT yours:** `tests/test_spend.py`, `infrastructure/llm/**`, and the spend stores. Chain tw-s runs beside
you there.

## Proof
- Link 1's green and red, and link 2's red, pasted.
- `scripts/offline_run.py` run DIRECTLY, on a fresh home, `--against 3625a6687`'s home:
  - every leg UNMOVED, `potter/pressed` requests included;
  - a leg that moves names its decision and why the new one is right.
- The digest moves and run-data reshapes. Count every reshape before you wipe anything.
- Full gate green at the tip, with the cloud's `-` job peaks named. State `offline_run.py`'s wall time and
  child RSS, base vs tip.

**Report:** follow common.md's FINAL REPORT on `out/tw-r2-report`. Include:
- the three findings of link 3, in order;
- the mechanism chosen, and why each earlier candidate could not carry it.
