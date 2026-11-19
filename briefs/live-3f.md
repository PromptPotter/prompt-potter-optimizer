# live-3f: live-3's fix pass (cloud)

`briefs/common.md` binds you. Base: **`origin/arc/live-3`** (3068551f6), which built `briefs/live-3.md`'s
three links; push your tip to `arc/live-3f` (live-3's commits + yours; no rebase onto main needed,
it lands squashed). An independent review of live-3 found the items below, each CONFIRMED at its line
on that branch; check each again before building. One commit per item group, each quoting the
row "The webapp's live state lands on the wrong subject or the wrong version".

## Fixes
1. **One arm membership in both halves of the round read.** `cycle_reads.py::served_round`'s unfiled
   half serves `state.current_round.candidates` (`live_dashboard.py:143` drops
   `MeasurementRole.PARENT`); its filed half serves `closed.scoreboard` via `results.py::arm_readings`,
   the parent re-read included. `MeasurementRun.tsx:37` now counts the parent on a filed round, and a
   round that files mid-view gains a row and reorders. Decision: `RoundView.candidates` is the round's
   OWN arms (no parent re-read) in both halves, decided by one function both call; grep every reader of
   `RoundView.candidates` first (`NodeDetail.tsx:367` filters `arm.round === round`; drop that filter if
   the server now owns it). **Re-test:** `useRound.test.ts` stubs DIFFERENT unfiled/filed answers shaped
   like the server's, and a fact that the strip's count holds across the file.
2. **A filed round is not rebuilt per tick.** `reads.ts::roundRead` moved from `once` to `byEtag` +
   `subject: storeOf(path)`; under a live claim `run_state.py:202` stamps `time.time_ns()`, so
   `entries_version` moves every tick and every mounted filed round re-runs `closed_round` (main read it
   once). Root first: if a filed round's served answer depends only on what its close fixed, its
   validator is that close (it 304s under a live claim); if it depends on later state (archive rows, a
   re-fit ruler), the read is `once` after `unfiled` turns false — main's behaviour — in the one
   descriptor. Say which, with the evidence. **Re-test:** a fact: with a live claim ticking, a filed
   round's read costs no rebuild (a 304, or no request).
3. **Time-ray: what is shown never slides.** live-3 re-anchors the earlier pages on every head move:
   a paged-back view loses its oldest steps (`time-ray.test.ts:71` asserts 100..129 vanish), a view
   paged to the origin loses its first steps again on each move, and each move refetches all `pages`
   windows sequentially. Decision: earlier pages keep their fixed cursors; the head stays contiguous
   with them by reading from the newest shown earlier step forward (or one fetch of the k new steps),
   never by moving the pages. **Re-test:** head + one earlier page, the head moves by 30: nothing shown
   disappears, no gap, no repeat, and the move costs one request, not `pages`.
4. **The stitch live-3 left.** `round-candidates.ts::roundCandidates` still merges `dash.rounds` with
   `liveCandidates`; it feeds `rowAt` → `search-point.ts:123` → `SubjectBox`'s headline, while
   `SubjectBox`'s config rides the round read: two sources for one subject. Action: `rowAt` /
   `ObservePoint.row` read the round read (`useRound`); delete the merge and every `ArmRow` field only
   tests read (`key`, `round`, `source`). **Re-test:** the existing facts, rewritten to the one source.
5. **"Unfiled" is decided once.** `served_dashboard.py::_round_axis` and `served_round` decide it on two
   folds (they agree today, nothing ties them). Action: one function both ask.
6. **Small:** `webapp/CLAUDE.md:21` (I10) still says `dashboard.json` serves the in-flight round: fix it
   to the round read. `roundRead` gets `auth: true` like the dashboard and steps reads.
   `picked-point.test.tsx`'s fixture puts `liveRow`'s `samples` / `pipeline_overlay` into
   `RoundView.candidates`, which the wire drops: use the served shape. Off-diff, check
   `SteerForkPanel.tsx:55` (a fresh `overlay` per render resets `NodeConfigEditor`'s rows via
   `base !== prevBase`): fix at its source if real, else report.
7. **One server fact** (the tests charter: a stale 304 is silent): the round read's validator moves
   when an arm lands in an unfiled round. Nothing tests `served_round`'s unfiled branch or the
   `CYCLE_ROUND` framing today.

## Owns
Everything live-3 touched, plus `promptpotter/application/served_dashboard.py` (`_round_axis` only),
`application/live_dashboard.py` / `results.py` if item 1's function lives there, `webapp/lib/derivations/
{round-candidates,search-point}.ts`, `SubjectBox`, `MeasurementRun.tsx`, `NodeDetail.tsx`,
`SteerForkPanel.tsx`, `webapp/CLAUDE.md`, their tests, one test file under `tests/` for item 7
(`tests/test_api*.py` or `tests/test_reads*.py` as found). NOT `promptpotter/infrastructure/store/**`,
`application/{jobs,commands,runner}/*`, `tests/factories.py`, `tests/test_archive.py` (store-2), NOT
`promptpotter/connectors/*`, `infrastructure/llm/*`, `presentation/mcp/*`, `tests/test_security.py`,
`tests/test_spend.py` (small-11), NOT `application/optimizers/potter/`.

## Target
Net ≤ 0 lines outside tests across live-3 + this pass, or each added line named; the
`package_lines` ceiling ends at the measured count. Full `python scripts/gate.py` green. Name every
visual change.

## Report
Per `common.md`: each item built / found not a defect, with its evidence; a Symptoms list.
