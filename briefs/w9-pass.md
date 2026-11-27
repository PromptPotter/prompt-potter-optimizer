# w9-pass: a pass that measures is one record (root C1) (cloud · Opus)

`briefs/common.md` and `briefs/w9-shared.md` bind you. Lands last of wave 9's batch.

SPEC: `docs/specs/code-debt-cleanup.md`, three rows, one root:
1. **Every pass is a recorded pass (root C1)**.
2. **Overlap and repair keep their own pass families**. DECIDED (operator's side, 2026-11-27): option
   (b). The overlap reading and its cells are one origin-panel record that the round and its
   restatement carry, so `.overlap` goes whole and `RoundRestatedRecord` folds into the one record.
   Re-test: `git grep -n "\.overlap\b" -- promptpotter` is empty.
3. **A locked-in leader's caught-up cells reach the scoreboard only at the round's close**: the
   parent's catch-up announces its arm before the walks, so it records as that arm's pass and
   `MeasurementRole.BACKFILL` goes (`git grep -c BACKFILL -- promptpotter` is 0).

Root: overlap, repair and the parent's catch-up are recorded passes with an arm. `RoundCells.overlap`
/ `.line`, `RoundRestatedRecord`, `CatchUpPool` and `MeasurementRole.BACKFILL` go. Today
`overlap|line_results|RoundRestated|CatchUpPool` holds 156 hits in 42 files.

OWNS: `domain/results.py`, `domain/origin_panel.py`, `domain/run_standing.py`,
`domain/records/run_records.py`, `domain/dashboard_rows.py`, `domain/pass_kinds.py`,
`shared/measurement_context.py`, `application/runner/round.py`, `application/scoring/catch_up.py`,
`closed_rounds.py`, `selection.py`, `query_loop.py`, `application/bench/node_context.py`,
`round_analysis.py`, `application/views/readout.py`, `views/render/phase.py`, `primitives.py`,
`application/served_dashboard.py`, `infrastructure/projections/live_dashboard.py`,
`store/campaign_store/ledger_scan.py`, `store/lineage_queries.py`, potter's `.overlap` readers
(`panels.py`, `members.py`, `escalation.py`, `election.py`), the webapp readers of `overlap`, and
their tests. Not yours: `scripts/offline_run.py` (w9-trace's; edit only the `ROUND_KEYS` /
`ELECTION_KEYS` members you remove), `domain/export.py` (w9-fields'; its one `.overlap` read only),
`runner/entry.py` and `runner/termination.py` (w9-entry's).

Keys: every behaviour key moves (potter and capo-pobb by `decisions.ledger.arms`, the rest by the
round's shape); name each old→new. Wipe: `RoundStatement`, so every ledger round line, round file
and banked round reading.

LEAN: `promptpotter/` shrinks (expected about +250 / −600).
