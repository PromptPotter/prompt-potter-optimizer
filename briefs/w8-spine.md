# w8-spine: the repair readers go, then root C1 (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you. This is the critical path: every later lane
reads pass families, so build it deletion-first and lean.

SPEC: `docs/specs/code-debt-cleanup.md`. Read each row whole, then close it as its Root says, one
commit per link:
1. **The repair's readers have no producer**: a pure deletion. Only the definitions and one `case`
   remain, and `REPAIR` leaves `PASSES`.
2. **Every pass is a recorded pass (root C1)**, in § wave-6 · arch review 2. Decided: the `recorded`
   flag on `domain/pass_kinds.py::PASSES` is deleted outright, with no exception left. Every pass's
   cells reach the ledger as recorded cells.
3. The rows C1 closes, by title: **Overlap and repair keep their own pass families**, **A locked-in
   leader's caught-up cells…**, **Two precedence rules join an arm's rows**, and **A round's own arms
   are spelled beside `MeasurementRole.is_proposal`**. Group them into links as their roots share code.

OWNS: `promptpotter/domain/records/`, `domain/pass_kinds.py`, `application/runner/round.py`,
`application/bench/round_analysis.py`, `domain/run_standing.py`, `application/scoring/passes.py`
(SHARED with w8-resolve: edit only the pass-family code; it edits only the draw and scorer reads),
and their tests.

LEAN: `promptpotter/` shrinks. Equals-main (`scripts/offline_run.py --against` the base home, every
leg), digests and behaviour keys: unchanged, or each move named and explained. A moved decision on
any leg stops the link. Run data: name each wipe.
