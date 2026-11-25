# tw-r3: what a campaign bought ahead is named where it is bought, and filed once (cloud, Opus)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is `origin/land/test-world` at
`<BASE-R3>` = `71bc072c5`, which is `52a173216` plus tw-r2 (read `out/tw-r2-report:report.md` first),
tw-s, and my fix to the dspy children's import root. Replace the ancestor
check with `git merge-base --is-ancestor 9cd30b5a7 <BASE-R3>`. Equals-main runs against `<BASE-R3>`'s
offline home. Push to `arc/tw-r3`, one commit per link, with the full gate green at the tip.

## The facts (read at `<BASE-R3>`)
1. **Filed at arrival.** Under a feed, `query_loop.py::_acquire` files every fresh cell under its claim
   as the cell arrives (`ctx.recorder.take` before `claim.retire`), so a waiter in another process can
   replay it.
2. **Filed again at a bank.** `Walk.bank` passes the finished fresh cells to `_ArchiveRecorder.bank`
   (`search_point_scorer.py`). Its `_file` files every cell it gets, while `take` skips a cell that
   already has an answer. `measurement_archive.py::file_answers` mints a new uuid ref per call. So a pause
   files a measurement `_acquire` already filed a second time, under a second ref. tw-r2 found this
   (its § Symptoms).
3. **Named only on one ending.** tw-r2 writes `UntakenBoughtRecord` in `_ScoringPhase.land` from the walks'
   `dropped` lists.
   - On a pause, the `except` arm runs `land()`, then `bank()`, then `discard()`. `discard()` runs in
     `finally` and ends every walk still racing: their cells join `dropped`, but no record follows.
   - A cancel goes straight to `discard()`.
   - A crash writes nothing at all.

   On all three endings, what the campaign bought ahead stays its own memory. That is the bug tw-r2 closed
   for `land` only (its § Rows to file).

## Links
1. **Pin both as failures.** Offline legs, no pytest:
   - a paused-and-resumed leg compares, beside its bills and priced keys, the filed answers per
     (configuration, sample) with its straight twin's, and is RED at `<BASE-R3>` if fact 2 holds;
   - a pressed leg paused inside a round and resumed reads the warm run's requests, and is RED at
     `<BASE-R3>`.

   Paste both reds. If a red does not come, the fact is wrong: say so and stop that half.
2. **The root.** The fact "this campaign filed answer X before any of its turns took it" exists the
   moment the filing in fact 1 happens. Weigh recording it there, so every ending, a crash included,
   leaves it named. Then `Walk.dropped`, `Walk.untaken_bought` and `land`'s hook go.
   - **One filing rule:** a cell is filed exactly once, whichever of take and bank meets it first.
   - **A banked cell stays the campaign's own memory:** it is SURE to be taken, so a resume replays it
     and re-buys nothing. Find what names a banked cell today (the ledger's records, the priced keys,
     `scan_ledger_answers`) before you add a name.
   - A record whose meaning changed gets a name that says what it now holds.
   - State the cost: ledger records added per round on the pressed leg, and one `SampleIndex.refresh` on a
     20-campaign home, base vs tip.

   **Accepted when, together:**
   - both pins are green;
   - `potter/pressed` is UNMOVED, requests included;
   - every other leg is UNMOVED against `<BASE-R3>`, or names the decision that moved and why the new one
     is right.
3. `behaviour_keys.json` and the `surface-ledger` ceilings at your green tip, with the reasons. I
   regenerate both at integration.

## Owns
- **Production:**
  - `application/scoring/**`;
  - `infrastructure/store/archive_queries.py`, and `measurement_archive.py` only for the filing rule;
  - `application/initialization/scoring_scope.py`;
  - `domain/records/untaken.py`;
- **Docs:** `docs/concepts/scoring-and-memory.md`;
- **Legs:** `scripts/offline_run.py` and `scripts/offline_world.py`.

**NOT yours:** every file under `tests/`. Chain tw-x runs beside you in `tests/world.py`, `conftest.py`,
`factories.py`, test_archive, test_ruler, test_identity and test_scoring, and owns the rest of
`infrastructure/store/**`. Chain tw-s runs in test_spend, `infrastructure/llm/**` and the spend stores.

## Proof
- Link 1's two reds, pasted.
- Run `scripts/offline_run.py` DIRECTLY on a fresh home, `--against <BASE-R3>`'s home. Give its wall time
  and child RSS, base vs tip.
- The digest moves and the run-data reshapes. Count every reshape before you wipe anything.
- Full gate green at the tip, naming the cloud's `-` job peaks.

**Report:** common.md's FINAL REPORT on `out/tw-r3-report`, with the mechanism chosen and why each other
candidate could not carry it.
