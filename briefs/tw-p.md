# tw-p: the reworked suite runs faster and holds less than the one it replaced (cloud, Opus)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is `origin/land/tw-x` at
`<BASE-P>` = `9aad46933`, which is `land/test-world`'s `af224d3bd` plus tw-x (read
`out/tw-x-report:report.md` first). Replace the ancestor check with
`git merge-base --is-ancestor 9cd30b5a7 <BASE-P>`. Equals-main runs against `<BASE-P>`'s offline home. Push
to `arc/tw-p`, one commit per link, with the full gate green at the tip.

**Your base fails the operator's desk gate on memory alone.** All 602 tests pass, but the pytest job peaks
at 448 MB against its 393 MB cap (`scripts/gate.py`'s `mb=` for pytest). At `af224d3bd` it peaked at
349 MB. The cap is not raised. Your tip brings the suite back under it: your cloud gate prints `-` for job
peaks, so the in-process RSS you measure stands in, and I check the cap on the desk.

**`tests/CLAUDE.md` binds whole**, its § Budget per test above all: ≤ 2 s and ≤ 2 MB left behind, a shared
world run included.

## Why
The operator asked whether the rework would also improve the suite's run time and memory. tw-x moved the
hand-seated facts onto real runs, which was right, and the suite paid for it. In tw-x's own session:
- the suite went from 80.8 s to 113.8 s, and the pytest process's RSS at the end from 271 to 365 MB;
- test_archive went from 8.5 s to 38.5 s. The family-tree run takes about 11 s, the L4 run about 8 s,
  and the `line` cold setup about 8 s;
- 17 tests leave more than 2 MB behind: archive's formula fact 53 MB, its inner-run fact 26 MB, the rest
  between 3 and 13 MB (tw-x § Appendix D).

Cloud numbers differ from one session to the next, so every before → after you state is measured in YOUR
session, at `7dce00a96` (where the rework started), at `<BASE-P>` and at your tip.

## Links
Each link names the seconds and MB it removes, measured.
1. **Rules a world run stands in for become tables.**
   - tw-x's row 4: `application/hierarchy_subtree.py`'s resolution rules (a candidate's line, retirement,
     placement) are exposed as pure functions and tested as tables. The family-tree world run and the L4 run
     go wherever only those rules needed them. The tree shapes tw-x listed as untested (inner hangs,
     supersede of a supersede, a restated round, tied rival cuts) become rows of those tables.
   - tw-x's row 6: `application/bench/resume_and_fork/repair.py::apply_correction` is "unreachable by any
     live walk". Find every caller and door. With none, it is dead: delete it and its tests. Otherwise
     expose `_earlier` and the correction rule as tables.
2. **What a shared run leaves behind.** Find what each module or session fixture keeps alive after its last
   reader (tracemalloc snapshots around it): the stores, caches, the offline world, the event loop. Release
   it at teardown, or fix the production owner that caches per process (a row if that is outside your
   table). tw-x's row 9: `World` hands out a reader scoped to a `Shared`, so no fixture re-points
   `PROMPTPOTTER_HOME` by hand.
3. **One run per shape, the smallest that carries its facts.** Where test_archive, test_identity and
   test_scoring each run their own `line`, `steered_line` or `head_to_head`, one session-scoped run serves
   them all, if its facts allow it. Each run uses the fewest rows and rounds its facts need, and the cost per
   fact is stated in `tests/CLAUDE.md` § The world.

**Accepted when, together:**
- the suite's wall and the pytest process's end RSS are below `7dce00a96`'s, in your session;
- every test still over 2 s or 2 MB is named with its reason;
- no fact was deleted to buy time unless it fails the admission rule, which you quote.

## Owns
- `tests/world.py`, `tests/conftest.py`, `tests/factories.py`, `tests/CLAUDE.md`;
- `tests/test_archive.py`, `test_identity.py`, `test_scoring.py`, `test_ruler.py` and `test_connectors.py`,
  except each file's `# Journeys awaiting a leg` block;
- in production, `application/hierarchy_subtree.py` and `application/bench/resume_and_fork/**`.

**NOT yours:** chain tw-j runs beside you. It owns `scripts/offline_run.py`, `scripts/offline_world.py`, every
journeys block, test_spend, and the production tw-j row of `briefs/tw-2b.md` (`application/scoring/**`,
`optimizers/**`, `runner/**`, `intelligence/**`, `bench/node_context.py`). tw-x's rows 1 and 2 (the offline
world's Groq host and its zero-cost usage) are in its files, so you leave them.

## Proof
- The three-point before → after (`7dce00a96`, `<BASE-P>`, tip), per file and for the suite: lines, tests,
  lines per test, wall, and the pytest process's RSS at start and end.
- Coverage diff (`sys.monitoring` PY_START, `<BASE-P>` vs tip). Every body `<BASE-P>` ran and the tip does
  not is deleted code, restored, or named with its reason.
- Equals-main against `<BASE-P>`: every optimizer and leg UNMOVED.
- Full gate green at the tip.

**Report:** common.md's FINAL REPORT on `out/tw-p-report`, plus the factories builders left with zero
callers.
