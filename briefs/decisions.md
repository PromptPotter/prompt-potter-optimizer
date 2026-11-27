# Open operator decisions (base `release/next` 176ed7b4c, 2026-11-27)

`debt:L` = `docs/specs/code-debt-cleanup.md` line at HEAD, `rm:L` = `docs/specs/roadmap.md`, `MAP nn` = an UNDECIDED
target in `.scratch/map/MAP.md`. "Unblocks" = debt rows and map areas that cannot reach target without the answer.
Ordered by unblocking. One line each: decision · source · unblocks · recommendation.

**Operator answers 2026-11-27 — ALL DECIDED:** every item takes its recommendation, except: 9 = an exact bound on
the share of disagreeing cells (one rule for a constant paired side; honest at its stated level, ~±0.17 for 20 ties
and ~[0.83, 1] for 20 wins on 0/1 grades; its bounded-variable form for graded scores), replacing both the `1/(4n)`
floor and the support bound (CORRECTED same day: for 20 wins the exact bound is ~[0.66, 1], not [0.83, 1], since the
non-win cells may be losses, μ ≥ 1 − 2·0.168; it beats the support bound only on ties); 19, 25, 27 approved as
deferrals/orderings, not answers (25: the identity chain's grants part is a read-only review); 17 = DROPPED: delete the "An open panel lags its trigger by a frame" row and never
raise CSS anchor positioning again. His criterion for anything left open: whatever facilitates the development
process most and yields the cleanest implementation.

## Decisions
0. WITHDRAWN 2026-11-27 by the orchestrator after the operator's ok: `landing.py::_landed` reads the key at every
process entry, so computing it there costs ~2.8 s a start (the reason it is committed), and four other generated
files conflict at a merge the same way; the key stays committed. Original line: (added by the orchestrator; filed nowhere until now, HANDOFF 1065) The built key's rewrite: `application/maintenance/built_key.py` is a committed code hash that every landing regenerates, so it conflicts in every merge (all four wave-9 merges today) · HANDOFF:1065 · every landing's merge step; debt row "a second `derived_landing_key()` takes under 0.2 s" (~debt:700) · computed where read and memoized per process, the file deleted, since a generated file in git conflicts by construction; the 2.16 s closure cost rides with it (the row's memo).
1. Where the optimizer plugin contract lives (`optimizers/nodes.py`, imported by 13 modules outside `optimizers/`) · MAP 09, debt:148 · rows 138 (bench knot), 37; areas 03, 09 · below the bench, since §0 says the bench imports no optimizer.
2. What names the kind of a nested cell's hold (`spend_book.py::_ENCLOSED`) · debt:459 · rows 453, 464, 535 and the money fold 549 · the spawning pass names it and an inner cell's is its connector's name, since no fallback is then needed.
3. Whether the debt doc is re-sectioned by area · MAP's premise · every chain's census, and about 170 unfiled candidates need a home · yes, 14 sections in MAP's order, since an untouched module then shows as an empty section someone must explain.
4. An errored, charged cell on resume: replay the error, or retry and state the bill · debt:644 · rows 635, 642, rm:348 · replay, since §0 says no retry of a (sample, candidate) pair.
5. Which of `enable_2pl_graduation`, `lift_reference`, `fitness_ceiling` / `rounds_to_ceiling`, `degradation_fatal_fastpath` stay · rm:315 · areas 07, 04 · keep the fast path (§0 names it) and delete the rest, since `git grep -l` over `paper/` finds none of the five in a plan.
6. Whether resume re-executes members against the content caches (about −1.2k lines) · rm:309 · area 03's size, row 763 · not now, since §0.5 holds resume and fork-on-divergence load-bearing and the caches are evictable.
7. Whether "runner is 8 modules" is the target · debt:255, MAP 03 · row 252 · replace the count with the concepts that own a module, since it closed at 9 twice with no shape to check.
8. A Windows replace whose reader never reads a present file as absent · debt:403 · row 394 · hold it and keep the retrying reads, since both measured fixes were worse and the box is Linux.
9. What a constant paired side should claim · debt:437 · row 433 · keep the `1/(4n)` floor as the declared rule, since the support bound costs the race its power (±0.42 against ±0.026 at n = 20).
10. What scale an edited formula replays on · debt:122 · row 118 · refuse below a coverage and serve the share re-graded, since a verdict on a quarter of the scale says nothing.
11. Whether a narrowed `param_allowed_values` is fork-only · debt:328 · row 327 · yes, one `ForkProposal` member mapped to one `ConfigOverrides` edit, since a rebase is the only door applying config.
12. Run-folder JSON indented or compact · debt:673 · row 671, part of 677 · price both first as the row says; the folder is the headless UI, so only `dashboard.json`, which nothing reads back, is a candidate.
13. Who clocks an inner node · debt:448 · row 445 · a node ran where its run's ledger holds a call of it, since the ledger is the trace.
14. A corpus source the repo can commit under 1 MB for the estimator · debt:741 · row 738 · the offline world's L4 leg outputs, since account data on the box may be wiped.
15. The sub-cent money rule · debt:320, rm:312 · row 317 · three significant digits under a dollar in `usd_text`, since it already feeds the webapp's `fmtUsd`.
16. Whether a cut send's late usage is collected at all · debt:471 · the tail of 464 · no, it stays an unreported `Lapse`, since the cancelled request never returns.
17. CSS anchor positioning without iOS before 26 · debt:493 · row 490 · wait, since the operator's surface is the phone.
18. Whether a 1,545-line connector fits "one module here" · MAP 08 · area 08's target · survey `harbor.py` first, since no row has read it as a shape.
19. The host assistant's four questions (thread persistence, writes that skip the click, loop or SDK, model) · rm:641 · deletes `CheckinOutput`, `RaisedCommand`, `familiesNamed`; decides rows 64, 69 · thread persistence first, since it is a survivor.
20. Can a compare campaign seat optimizer-prompt arms · rm:499 · L4's validity item · yes if it rides `RunDeclaration.purpose`, since §0 gives the controlled comparison one declaration.
21. Which reading the bench compares for CAPO's survivors · rm:351 · one INVESTIGATE · the reference's, since the bench claims to run the paper's optimizer.
22. Tie-breaking at the budget cap · rm:618 · one right-winner item · ship pick-by-point-estimate as written and count how often it fires.
23. Whether an inner L4 run inherits its outer's overshoot switch · rm:316 · area 05's L4 edge · inherit, since an inner run already spends under its root's book.
24. Whether `{bench: N}` becomes the total · rm:313, rm:366 · one mechanism item · read the box's definition first (action 6).
25. Delegation: who edits grants, whether a delegate is its own tenant · rm:727 · area 13's target · decide before the access review of `account.py` and `grants.py`.
26. Prompt-field order as an optimizer axis or an operator setting · rm:735 · C10 only · operator setting, since a reorder re-keys every cell.
27. Whether an upload's bytes are kept before the parse · rm:313 · nothing filed · decide with anon.
28. Whether the visits analytics move to the site repo · rm:309 · nothing filed · leave.

Already decided, so not asked again: θ leaves the round's statement and stays the race's instrument and L4's level
series (rm:283–290); `domain/` and `application/` stay flat, since §0 rules out a concept-first re-hierarchy; overlap
is one origin-panel record, option (b) (debt:49, HO1115, built in `arc/w9-pass`); a queued launch is a job on disk
(debt:19); a machine-held connector's arming waits on the pool (debt:759); a model keeps its host pin only where
cache share and $/M beat uncached (debt:333); `api_keys.json` is encrypted under a box key (debt:322).

## Operator actions (the box, the desk or a paid run; not decisions)
1. Land `arc/w9-entry`, `arc/w9-trace`, `arc/w9-pass` (each re-keys; pass wipes round lines).
2. Box update, then the MCP connector in claude.ai (debt:288), TermNorm's `xconn` branch (debt:76, 264, 272, 277) and the box key for `api_keys.json` (debt:322).
3. Time a resume on the box (debt:352) and a cold verify on a paused cycle (debt:27).
4. Paid: the L4 walk's stop from one depth-2 run (debt:167); the host-pin twins (debt:331); the `l1_critique` proof (debt:480); the "slot" prompt comparison (debt:336).
5. Desk: Vitest under five builders plus a gate on Windows (debt:725); five runs of capo's skip twin (debt:730); three runs at press max (debt:763).
6. Read banked ledgers on the desk and the box for the three INVESTIGATEs (rm:348, 351, 362) and the `hotpotqa-closedbook` definition (rm:366); free.
7. The `update.sh` hunk of the landing wait (debt:411); which env file the box keeps (rm:312); PRs #76 and #77; `git prune` of 39,042 loose objects (HO1139).
