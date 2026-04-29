# Self-Healing

Failures attach to the candidate that produced them, never to the round. Four loops watch a producer LLM's output, then nudge it via the nurse LLM's prompt. **Healing is gradual** — a guidance update shifts the producer's distribution; if it doesn't fully fix things, the loop retriggers next round with fresh evidence.

## The four loops

|   | Producer → Nurse | Detector | Failure record | Stored on OSP as |
|---|---|---|---|---|
| **1** | L1 → L2 (gen-time) | `L1_SCHEMA_COMPLIANCE` validator | `ValidationFailure` | `validation_failures` |
| **2** | L1 → L2 (runtime) | `DegradationCheck` (mid-eval) | `RuntimeFailure` | `runtime_failures` (accumulates) |
| **3** | L2 → L3 (stall) | `l2_patience` exhausted | (none — patience event) | `escalation.l2.stall_count` |
| **4** | L2 → L3 (post-parse) | `L2_OUTPUT_VALIDATORS` registry | `ValidatorOutcome` | `l2_output_failures` |

- **Loop 1.** L1 proposed a value outside the allowed set. Synthetic 0; no backend call. L2 next round writes guidance shifting L1 toward the allowed region.
- **Loop 2.** Candidate ran but degraded (e.g. 100% `reasoning_budget_exhausted`). Real score, candidate eliminated mid-eval, failure mirrored to outer memory. L2 reshapes its own outputs; trail accumulates across rounds.
- **Loop 3.** L2's adjustments aren't moving the metric for `l2_patience` rounds. L3 replans — pipeline composition or strategic frame.
- **Loop 4.** A deterministic validator caught L2's parsed output (cross-field duplication, verbatim self-repeat, catalogue redundancy). L3 fires *immediately*, bypassing patience.

## Healing is gradual

A loop firing once produces *one* nudge — not a guaranteed fix. The producer's distribution shifts; whether the next proposal lands depends on how clear the evidence was and how strongly the nurse encoded it. If the failure recurs:

- Loops 1 and 4 retrigger same/next round with the new evidence.
- Loop 2's failure trail accumulates across rounds; L2 sees NEW vs ACCUMULATED and must change angle if the latter survives.
- Loop 3 fires only on stall, but each L3 plan shapes both subsequent L1 and L2.

The system depends on this gradualness. Hard one-shot directives ("do NOT propose X") aren't required — softer pointers toward the right region are enough, because the loop is built to retry.

## Validators are Evaluator-shaped

`ValidatorOutcome(id, passed, score, evidence, nurse_target)` mirrors `Evaluator(name, ..., compute → float)`. The `score` field is the seam for future L4 composite scoring — outcomes can flow into the same kind of formula evaluators feed today.

## Failures attach to candidates, not rounds

One wild mutation can't waste the round; round winners are unaffected. This is what makes the gradual-retrigger property safe.

## Round-over-round feedback (separate)

`l1_critique → l1_generate` fires every round, regardless of failure. Not in the canon — it's performance-driven feedback, not failure-driven healing. See [three-layer-loop.md](three-layer-loop.md).

## How user-visible

Per-query `⚠ … ↳` annotations. Audit trail, not alerts.

For implementation wiring see [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md).
