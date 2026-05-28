# Fixture: cycle ended with L2 as the last phase

Stripped-down `dashboard.json` reproducing the "L2-terminal hang" shape
called out in `docs/specs/code-debt-cleanup.md`. Mirrors the operator's
real `justlogic__ca6d4d / cycle_2451d3cf6ebc` exit state: four full
L1 rounds (`l1_generate` → `l1_critique` → `l1_score`) closed cleanly,
then round 5 ran `l1_generate` → `l1_critique` → `l2_context` (refining
`task_context`) and the run stopped at `max_rounds` without an
`l1_score` ever firing on round 5.

**Shape that matters:**

- `state = "stopped"`, `stop_reason = "max_rounds"`.
- `current_round.round = 5`, `current_round.nodes` carries
  `l1_generate` + `l1_critique` + `l2_context` keys but **no
  `l1_score`** — round 5 didn't reach scoring.
- `rounds[]` carries five entries (rounds 1–5). Rounds 1–4 have real
  candidate summaries; **round 5's `candidates` array is empty** —
  the round-display projection materialized a stub row when the round
  closed mid-L2 without any candidates ever being scored.

**Bug class exercised:** an empty historical entry suppresses the
in-flight L1_SCORE branch for the same round number. With this shape:
`historicalRounds` previously added 5, blocked the in-flight check for
round 5, and rendered 0 bars for that round — even when live L1_SCORE
data was available. On *completed* cycles the empty round 5 stub stays
forever, producing the operator-visible "round 5 has no bars" symptom.

**Fix:** skip empty historical entries when building `historicalRounds`
in `round-candidates.ts`. Empty entries had nothing to double-count
against the in-flight branch anyway; gating the in-flight check on
them was incidental, not intentional.

Identifiers (`campaign_id`, `cycle_id`, `session_id`) are deterministic
placeholders, not anonymized real values — the tests assert on derived
shape, not on identity.
