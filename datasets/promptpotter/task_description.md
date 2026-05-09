# PromptPotter Self-Optimization

Optimize the L1 / L1_CRITIQUE / L2 / L3 **meta-prompts** that drive
PromptPotter itself.

## Domain

- Input: a `round_context` snapshot — optimizer state at round N
  (the parent `OptSearchPoint`, the prior `l1_critique` output, the
  current `task_context` framing, the live `l1_config` knobs, and the
  parent's measured accuracy)
- Output: a `next_brief` — the change the optimizer made between
  rounds N and N+1 (one of: a new L1 candidate's
  `changes_description`, a refined L2 `task_context` delta, or an L3
  `plan` rewrite)
- Challenge: predict an evidence-anchored improvement to the
  meta-prompt that produces a positive `score_delta`. Random or
  unjustified mutations score below the no-op baseline.

## Success criteria

- Mean `score_delta > 0` across rows: the proposed brief lifts
  measured accuracy on the next round.
- Bonus: positive `score_delta` on the L1→L2 escalation rows
  specifically — escalations are the highest-leverage transitions
  and the easiest to regress on.

## Key failure modes

- Speculative briefs that don't cite a specific axis, sample, or
  yield number from `round_context`
- Layer mismatch: proposing an L1 candidate change when the
  `escalation_layer` field shows the source row was an L2 or L3
  fire (the prior optimizer chose to escalate for cause)
- Verbatim repeats: a brief that, applied to the parent prompt,
  merges to a no-op delta
