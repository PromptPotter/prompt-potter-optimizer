# w8-spine2: root C1's rest, the walk fill, then root C2 (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you. This is the critical path, the records spine,
so build it deletion-first.

SPEC: `docs/specs/code-debt-cleanup.md`. Read each row whole and close it as its Root says, one
commit per link:
1. **Every pass is a recorded pass (root C1)**, its open half: the overlap family (`.overlap`,
   `RoundRestatedRecord`, `CatchUpPool`) becomes recorded passes. A catch-up walk continues the
   prior's arm rather than recording `BACKFILL` with no arm. Close **Overlap and repair keep their
   own pass families** and **A locked-in leader's caught-up cells…**. `views/readout.py` is free now
   (w8-resolve landed).
2. **An arm's walk fills by key, not by what a row is**: name the walk fill once
   (`CellSheet.filled_from`, called by `ArmWalk.continued` and `QueryLoopState.running`). It keeps a
   measured row over its replay and a scored row over an errored one, in either order. Check the
   row's three suspected sites. Pin both re-test scenarios. **This link may move decisions and a
   behaviour key** (an override of w8-shared): explain each moved leg by the mechanism (which row now
   stands), name each key old→new, and push it as done. A leg that moves for a reason you cannot name
   stops the link.
3. **A record holds no value another record derives (root C2)**: `Invalid` beside `.invalid`,
   `RoundDiagnostics` on the envelope, and `.running` stamped on every cell, along with the rows it
   names by title. Readers re-derive off the record that holds the fact.

OWNS: `promptpotter/domain/records/`, `domain/scoring.py` (`CellSheet`), `domain/arms.py`,
`domain/pass_kinds.py`, `application/bench/cycle.py`, `application/bench/round_analysis.py`,
`application/scoring/` (`query_loop.py`, `catch_up.py`, `passes.py`, `paired.py`),
`application/runner/round.py`, `application/views/readout.py` (its overlap readers), and their tests.
A reader in `served_dashboard.py` or `cycle_reads.py` (w8-web's) is a minimal re-point only.

LEAN: `promptpotter/` shrinks. Equals-main on every leg, digests and behaviour keys unchanged, except
link 2 as stated. Run data: name each wipe (a record losing a field is one).
