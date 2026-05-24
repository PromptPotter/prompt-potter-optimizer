# The Three-Layer Loop

Three layers, repeating every round. L1 fires every round. L2 fires only when L1 stalls. L3 fires when L2's strategy stops moving the needle.

```
┌─ ONE ROUND ────────────────────────────────────────────────────────────┐
│  L1 GENERATE — evolve N individuals                                    │
│         ↓                                                              │
│  L1 EVALUATE — measure each individual's fitness against the dataset   │
│         ↓                                                              │
│  L1 CRITIQUE — analyze fitness; direct next generation                 │
│                                                                        │
│  ── ESCALATION (rules over EscalationInputs) ───────────────────────── │
│  L2 REFINE CONTEXT — rewrite the task framing fed to L1                │
│  L3 MODIFY PLAN — rewrite the strategic plan L1 works within           │
└────────────────────────────────────────────────────────────────────────┘
```

L1 picks specific values. L2 reframes *how* L1 searches by writing onto the `OptSearchPoint` (`task_context` is the broadcast "what is this task" signal; persistent + accumulative). L3 writes a strategic framework to `OptSearchPoint.plan`. Higher layers don't replace lower ones — they constrain them. Every optimizer LLM call shares one path: per-call `DispatchState` → `LAYER_CONFIGS[layer]` → `compile_prompt_vars`.

The critique step is the only place in the loop that reads raw per-sample results; it feeds forward to next-round L1 (primary) and to L2 (operating context on escalation). **`l1_critique → l1_generate` is performance-driven feedback, not failure-driven healing** — different mechanism from self-healing.

Post-round transitions are decided by escalation rules over `EscalationInputs` (`application/optimization/escalation/`), not a hard-coded patience FSM — predicates are pure functions over a frozen snapshot; adding a rule is one row in `escalation/rules.py`. Yield-drought preempt fires L2 early when AxisIndex shows zero productive axes with at least one stall round on the clock.

Five LLM call sites: `checkin` (one-time decomposition at init), `l1_generate`, `l1_critique`, `l2_context`, `l3_plan`. Critique-and-refine pattern inspired by [PromptWizard](https://arxiv.org/abs/2405.18369).

## Pointers

- Architecture invariants: [`../architecture.md`](../architecture.md) §0
- Dispatch routing + four wounds: [`../developer/dispatch-hub.md`](../developer/dispatch-hub.md), [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md)
- L1 / L2 internals: [`../developer/l1-generate-surface.md`](../developer/l1-generate-surface.md), [`../developer/l2-internals.md`](../developer/l2-internals.md)
- Candidate elimination (PoBB): [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md)
- Escalation signal stream: [`../operations/observability.md`](../operations/observability.md#escalation-rule-signal-stream-signalsjsonl)
