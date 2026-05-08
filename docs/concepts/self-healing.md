# Self-Healing

Failures attach to the candidate that produced them, never to the round. Four wounds are tracked across the optimizer; each has a **producer** (the LLM that left it), a **detector** (the deterministic catcher), and a **nurse** (the LLM that tends it next round). **Healing is gradual** — a guidance update shifts the producer's distribution; if it doesn't fully fix things, the wound retriggers next round with fresh evidence.

## The four wounds

|   | Producer → Detector → Nurse | Failure record | Stored on OSP as |
|---|---|---|---|
| **Wound 1** | L1 → `L1_SCHEMA_COMPLIANCE` (gen-time) → L2 | `ValidationFailure` | `validation_failures` |
| **Wound 2** | L1 → `DegradationCheck` (mid-eval) → L2 | `RuntimeFailure` | `runtime_failures` (accumulates) |
| **Wound 3** | L2 → `l2_patience` exhausted → L3 | (none — patience event) | `escalation.l2.stall_count` |
| **Wound 4** | L2 → `L2_OUTPUT_VALIDATORS` (post-parse) → L3 | `ValidatorOutcome` | `l2_output_failures` |

- **Wound 1.** L1 proposed a value outside the allowed set. Synthetic 0; no backend call. L2 next round writes guidance shifting L1 toward the allowed region.
- **Wound 2.** Candidate ran but degraded (e.g. 100% `reasoning_budget_exhausted`). Real score, candidate eliminated mid-eval, failure mirrored to outer memory. L2 reshapes its own outputs; trail accumulates across rounds.
- **Wound 3.** L2's adjustments aren't moving the metric for `l2_patience` rounds. L3 replans — pipeline composition or strategic frame.
- **Wound 4.** A deterministic validator caught L2's parsed output (cross-field duplication, verbatim self-repeat, catalogue redundancy). L3 fires *immediately*, bypassing patience.

## Healing is gradual

A nurse firing once produces *one* nudge — not a guaranteed fix. The producer's distribution shifts; whether the next proposal lands depends on how clear the evidence was and how strongly the nurse encoded it. If the wound recurs:

- Wounds 1 and 4 retrigger same/next round with the new evidence.
- Wound 2's failure trail accumulates across rounds; L2 sees NEW vs ACCUMULATED and must change angle if the latter survives.
- Wound 3 fires only on stall, but each L3 plan shapes both subsequent L1 and L2.

The system depends on this gradualness. Hard one-shot briefs ("do NOT propose X") aren't required — softer pointers toward the right region are enough, because the nurse is built to retry.

## Validators are Evaluator-shaped

`ValidatorOutcome(id, passed, score, evidence, nurse_target)` mirrors `Evaluator(name, ..., compute → float)`. The `nurse_target` field on `ValidatorOutcome` names which layer tends the wound (`"l2"` or `"l3"`). The `score` field is the seam for future L4 composite scoring — outcomes can flow into the same kind of formula evaluators feed today.

## Wounds attach to candidates, not rounds

One wild mutation can't waste the round; round winners are unaffected. This is what makes the gradual-retrigger property safe.

## Round-over-round feedback (separate)

`l1_critique → l1_generate` fires every round, regardless of failure. Not in the canon — it's performance-driven feedback, not failure-driven healing. See [the-loop.md](the-loop.md).

## How user-visible

Per-sample `⚠ … ↳` annotations. Audit trail, not alerts.

For implementation wiring see [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md).
