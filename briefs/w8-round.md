# w8-round: the round's decision names each arm once (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you. w8-spine3 landed root C2's link (a decision is
a `DecisionRecord`, and an invalid arm has no reason). The round's own files are free now.

OVERRIDE of w8-shared (operator rule: a re-key never defers a fix). This chain may move behaviour
keys and digests. Land each move, explain it by its mechanism, and name it old→new. A leg whose
decisions move for a reason you cannot name stops the link.

SPEC: `docs/specs/code-debt-cleanup.md`, section "wave-6 · what the round-decision restructure left".
One commit per row:
1. **Round 0 is CROWNED on its `Election` and holds no election as served**.
2. **`RoundOutcome.reference` and `Election.parent` name one arm twice**.
3. **The race a round closes on can differ from the one its election read**.
4. **"Rebased to fork" names no fork**: the readout's stop line names the round the fork was cut at
   and the fork it rebased onto.

OWNS: `promptpotter/domain/results.py`, the election (grep `class Election`), `application/runner/round.py`,
`application/bench/round_analysis.py`, `application/views/readout.py`, `domain/records/` (the round's
records only), and their tests. Not yours: `domain/narration.py`, `ledger_scan.py`,
`live_dashboard.py`, `scoring/evaluators.py` and `paired_reading.py` (w8-spine4's);
`bench/resume_and_fork/` and `runner/entry.py` (w8-fork's). Each is a re-point only.

LEAN: `promptpotter/` shrinks. Run data: name each wipe.
