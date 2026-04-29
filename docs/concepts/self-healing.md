# Self-Healing

The optimizer can produce unfit individuals in two distinct ways, and it handles each on a separate rail. Failures always attach to the individual that produced them, never to the round — so one unfit individual can't disrupt the round's winner.

## The two rails

|  | **Rail 1 — Invalid at proposal time** | **Rail 2 — Degraded at runtime** |
|---|---|---|
| Detected | Before any backend call | During evaluation |
| Example | L1 proposed `model: gpt-4o` when only `gpt-oss-120b` is allowed | A configuration produces empty responses on 100% of queries |
| Who made the mistake | L1 (picked a disallowed value) | Nobody tactically — L1's value was in range, but the search's *strategic shape* didn't account for the runtime constraint |
| Score effect | Synthetic zero; no backend calls spent | Real score stands; the candidate is eliminated mid-evaluation |
| Who teaches whom | **L2 writes a directive** that names the forbidden value | **L2 adjusts its own outputs** — directive, task context, meta-settings, or L1-surface section overrides |
| Escalation | None — L2 can always articulate a constraint clearly | L3 replans when L2's adjustments keep failing |

Both rails share a rule: *detect early, pin the failure to the specific candidate, surface it in the candidate's score report, and feed the right teacher.* What differs is who the teacher is and what healing looks like.

## Rail 1 — Invalid proposals

Some candidates are dead before evaluation even starts. L1 proposed a value the backend doesn't accept — a model name outside the allowed set, a parameter value outside the allowed range. Nothing runs; the candidate gets a synthetic score of zero.

L2 next round reads the list of validation failures and writes a directive that names the forbidden value explicitly: *"do not propose gpt-4o for model."* The directive lands on the individual record and L1 reads it as primary signal for the following round. L1 picks a different value. Self-healed in one round.

Validation failures never make it to the user as a warning to resolve. They're just optimizer state — a loop participant learning its own rules.

## Rail 2 — Runtime degradation

Some candidates are valid at proposal time — every parameter is in range — but degrade at runtime. Canonical example: a reasoning model asked to emit structured output with too tight a token budget. The model exhausts its reasoning budget before emitting visible content; the backend returns the raw reasoning trace as the answer; every query produces a degradation warning. No rule was broken; the search simply didn't account for the runtime constraint.

The optimizer handles this in three steps:

1. **The candidate is eliminated mid-evaluation.** Its real score stands, but scoring stops once the degradation pattern is clear — no point spending more budget on a candidate that's already lost.
2. **The failure is pinned to that configuration** and mirrored into cumulative optimizer memory, deduplicated across rounds.
3. **L2 reads the accumulated failure trail** next round. If items survived prior L2 adjustments, L2's last angle didn't work — it must try something different. L2 writes a tighter directive that names the failing region, refines task context with the discovered constraint, or sets section overrides to gate misleading sections off so L1's search narrows.

If the pattern keeps growing across L2 rounds, L3 replans. L3 reads the trail as discovered constraints on the search space and either changes pipeline parameters (switch model, raise a floor, swap a node) or rewrites the plan to steer the whole search around the failing region.

## Why failures attach to candidates, not rounds

If a single unfit individual caused the round to abort, one wild mutation could waste an entire round's budget. By pinning failures to the individual and letting the round's other individuals run to completion, the round's winner is unaffected.

This is also what makes self-healing work at the right cadence. Rail 1 learns from its mistakes in a single round. Rail 2 learns across rounds by accumulating the trail — the *strategic* shape of the search is what's being adjusted, and that demands multi-round feedback.

## How user-visible this is

Not very. Both rails surface in the per-query annotation convention — a `⚠` line naming what was found, a `↳` line naming what the optimizer did about it — but they don't demand user intervention. The whole point of self-healing is that the loop fixes itself. The annotations are audit trail, not alerts.

For the implementation wiring — failure types, escalation signals, where each gets detected — see [../developer/self-healing-internals.md](../developer/self-healing-internals.md).
