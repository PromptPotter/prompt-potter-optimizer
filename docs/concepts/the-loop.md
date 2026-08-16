# The Three-Layer Loop

> **Audience:** Developer reference. Operators see [`../manual/`](../manual/) for usage docs.

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

L1 picks specific values. L2 reframes *how* L1 searches by writing L1's attention surface onto the `OptSearchPoint` — `memory.l1_layout` (which panels L1 sees) and `memory.l1_overrides` (how hard it explores). It does **not** write `task_context`: that framing is operator-authored and frozen for the run, structurally so (`TaskDecomposition.merge` refuses it and the L2 wire schema has no field for it). L3 writes a strategic framework to `OptSearchPoint.plan`. Higher layers don't replace lower ones — they constrain them. Every optimizer LLM call shares one path: per-call `InjectionBundle` → `NODE_LAYOUTS[node]` → `DispatchHub.fill`.

The critique step is the only place in the loop that reads raw per-sample results; it feeds forward to next-round L1 (primary) and to L2 (operating context on escalation). **`l1_critique → l1_generate` is performance-driven feedback, not failure-driven healing** — different mechanism from self-healing.

Post-round transitions are decided by escalation rules over `EscalationInputs` (`application/optimization/escalation/`), not a hard-coded patience FSM — predicates are pure functions over a frozen snapshot; adding a rule is one row in `escalation/rules.py`. Several preemptor rules fire L2 *before* patience runs out — **which ones is owned by [`../developer/dispatch-hub.md`](../developer/dispatch-hub.md) § Trigger**, and a copy of that set on this page is what goes stale.

Five LLM call sites: `checkin` (one-time decomposition at init), `l1_generate`, `l1_critique`, `l2_context`, `l3_plan`. Critique-and-refine pattern inspired by [PromptWizard](https://arxiv.org/abs/2405.18369).

## The state record — what one round carries forward

Every round carries one record — the *individual* (`OptSearchPoint`, often *OSP*). It holds the prompt, the strategic context, the operational memory, and L2's mutations to L1's surface. Implementation: `promptpotter/domain/opt_search_point.py`; domain contract: [`../../promptpotter/domain/CLAUDE.md`](../../promptpotter/domain/CLAUDE.md).

Two parameter namespaces co-exist on it: **prompt fields** (the decomposition the optimizer mutates) and **pipeline parameters** (thresholds / model / temperature / retrieval budgets — anything the pipeline's nodes expose). Names can overlap; the namespaces are independent. L1 mutates both in one proposal, and routing happens at individual-creation time.

It is the optimizer's working memory for two independent reasons:

- **Persistence.** Every round's record is serialized to `<cycle_dir>/rounds/round_NNNN.json`, and resume reads from the latest trial. State that is not on the record does not survive interruption. The serialized record IS the loop's live config, not a log of it — CONTEXT and PLAN are inspectable and editable on disk, so "add this to the plan" means exactly that.
- **Steering.** Every layer reads from it to know what to do: L1 reads prompt fields + brief + surface overrides, L2 reads operational memory + surface state, L3 reads plan + runtime failures.

**What it is NOT** — not the trace archive (per-sample results live in `measurements/`, referenced by ID: [`scoring-and-memory.md`](scoring-and-memory.md)); not the frozen target shape (that is `JobSearchPoint`); not the campaign config (operator knobs — max rounds, patience, the `n_variants` ceiling — live on `CampaignConfig` and never mutate).

**The prompt field set, the layer-specific surfaces and the render chain** — owned by [`../developer/dispatch-hub.md`](../developer/dispatch-hub.md). This page owns only what the record carries, and why state off it does not survive interruption.

## Pointers

- Architecture invariants: [`../architecture.md`](../architecture.md) §0
- Dispatch routing + four wounds: [`../developer/dispatch-hub.md`](../developer/dispatch-hub.md), [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md)
- L1 / L2 internals: [`../developer/dispatch-hub.md`](../developer/dispatch-hub.md)
- Candidate elimination (PoBB): [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md)
- Escalation signal stream: [`../operations/observability.md`](../operations/observability.md)
