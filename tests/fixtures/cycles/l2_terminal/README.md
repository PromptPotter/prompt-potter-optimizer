# Fixture: cycle ended with L2 as the last phase

A `dashboard.json` in the writer's current shape, cut to the "L2-terminal" exit: three
rounds closed cleanly after the origin, then round 4 closed at `max_rounds` before any of
its candidates scored.

**Shape that matters:**

- `state = "stopped"`, `stop_reason = "max_rounds"`, `current_round.round = 4` with no
  candidates — round 4 never reached scoring.
- `rounds[]` carries rounds 0–4. Rounds 0–3 are real closed rounds (`is_selected`,
  `stamps_theta`, `optimizer_facts`, `total`); **round 4's `candidates` array is empty** —
  the stub a round closed before its measurement leaves behind.

**Bug class exercised:** an empty historical entry must neither count as a closed round nor
suppress the in-flight branch for the same round number.

**Regenerating it.** Run `scripts/offline_run.py --optimizer potter` into a scratch
`PROMPTPOTTER_HOME`, then cut that cycle's `dashboard.json` through `LiveDashboardState`:
keep rounds 0–3, append a `RoundSummary(round=4, total=0, …)` with no candidates, set
`current_round` to an empty round-4 `CurrentRound`, and re-validate before writing.
Validating through the model is what keeps the file the writer's shape rather than a
hand-edited one. The origin's `changes_description` names the machine's dataset dir, so it
is rewritten to a relative one; identifiers are deterministic placeholders, and the tests
assert on derived shape, not on identity.
