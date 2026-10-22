# The Three-Layer Loop

> **Audience:** Developer reference. Operators see [`../manual/`](../manual/) for usage docs.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-light.svg">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-light.svg" alt="The loop: a task and a labeled dataset enter an optimizer setup, then generate, score and critique repeat until a stopping rule fires. L2 and L3 escalate lazily on a stall; a measurement archive replays instead of re-measuring." width="760">
  </picture>
</p>

[One round](../methods/candidate-elimination.md) is the unit; a campaign is rounds under three layers:

| Layer | Fires | What it does |
|---|---|---|
| 🧬&nbsp;**L1&nbsp;·&nbsp;generate, evaluate, critique** | every&nbsp;round | Mutates a generation of individuals, measures each one's fitness on your dataset, and reads the raw per-sample misses to steer the next generation. |
| 🔭&nbsp;**L2&nbsp;·&nbsp;refine&nbsp;context** | when&nbsp;L1&nbsp;stalls | Rewrites *how* L1 searches: which evidence it sees and how hard it explores. |
| 🧭&nbsp;**L3&nbsp;·&nbsp;modify&nbsp;plan** | when&nbsp;L2&nbsp;stalls | Rewrites the strategic plan L1 works within. |

The loop exits on a **stopping rule** — goal reached, budget spent, or a plateau — not on a fixed trial count.

```
┌─ ONE ROUND ────────────────────────────────────────────────────────────┐
│  L1 GENERATE — evolve N individuals                                    │
│         ↓                                                              │
│  L1 EVALUATE — measure each individual's fitness against the dataset   │
│         ↓                                                              │
│  L1 CRITIQUE — analyze fitness; direct next generation                 │
│                                                                        │
│  ── ESCALATION (rules over EscalationInputs) ───────────────────────── │
│  L2 REFINE CONTEXT — re-shape L1's attention (memory.steer)            │
│  L3 MODIFY PLAN — rewrite the strategic plan L1 works within           │
└────────────────────────────────────────────────────────────────────────┘
```

L1 picks specific values. L2 reframes *how* L1 searches by writing L1's attention surface onto the cycle's memory as one node overlay — `L2Memory.steer["l1_generate"]`, carrying `layout` (which panels L1 sees) and the call settings (how hard it explores). It does **not** write `task_context`: that framing is operator-authored at check-in (`CheckinOutput.task_context`) and frozen for the run, structurally so (no L1/L2/L3 wire schema has a field for it). L3 writes a strategic framework to `L3Memory.plan`. Higher layers don't replace lower ones — they constrain them. Every loop-layer (L1/L2/L3) LLM call shares one path: per-call `InjectionBundle` → `NODE_LAYOUTS[node]` → `DispatchHub.fill`.

The critique step is the only place in the loop that reads raw per-sample results; it feeds forward to next-round L1 (primary) and to L2 (operating context on escalation). **`l1_critique → l1_generate` is performance-driven feedback, not failure-driven healing** — different mechanism from self-healing.

Post-round transitions are decided by escalation rules over `EscalationInputs` (`application/optimizers/potter/escalation/`), not a hard-coded patience FSM — predicates are pure functions over a frozen snapshot; adding a rule is one row in `escalation/rules.py`. Several preemptor rules fire L2 *before* patience runs out — **which ones is owned by [`../developer/dispatch-hub.md`](../developer/dispatch-hub.md) § Trigger**.

Five LLM call sites: `checkin` (runs around the loop and skips the dispatch hub; it decomposes a dataset's framing once — on the first mint that finds none committed, `new --task-file` / `--task-text`, `new <raw file>`, or the web check-in), `l1_generate`, `l1_critique`, `l2_context`, `l3_plan`. Critique-and-refine pattern inspired by [PromptWizard](https://arxiv.org/abs/2405.18369).

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/tree-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/tree-light.svg">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/tree-light.svg" alt="A search tree: origin, rounds 1 and 2, then a branch through rounds 3 and 4 that is spent. The search rewinds to round 2 and a second branch reaches the winner." width="880">
  </picture>
</p>

**It searches a tree, not a trail.** Each round's result flows back to every ancestor it descends from, and when a branch is spent a UCB rule picks the ancestor to re-expand — so the search climbs a different hill instead of stalling. An ancestor's score reflects what re-expanding from it actually yielded, including in branches it never ran itself. This is AlphaZero-shaped MCTS over the lineage, with deterministic evaluation on your dataset in place of rollouts: [the four-phase mapping](../methods/candidate-elimination.md#comparison-to-mcts).

## The state record — what one round carries forward

Every round carries one record — the *individual* (`OptSearchPoint`, often *OSP*). It holds the prompt, the strategic context, the operational memory, and L2's mutations to L1's surface. Implementation: `promptpotter/domain/opt_search_point.py`; domain contract: [`../../promptpotter/domain/CLAUDE.md`](../../promptpotter/domain/CLAUDE.md).

Two parameter namespaces co-exist on it: **prompt fields** (the decomposition the optimizer mutates) and **pipeline parameters** (thresholds / model / temperature / retrieval budgets — anything the pipeline's nodes expose). Names can overlap; the namespaces are independent. L1 mutates both in one proposal, and routing happens at individual-creation time.

It is the optimizer's working memory for two independent reasons:

- **Persistence.** Every round closes onto the cycle's ledger, and resume reads the rounds that stand there. State that is not on that record does not survive interruption. `<cycle_dir>/rounds/round_NNNN.json` is a checkout of it — CONTEXT and PLAN are inspectable on disk, and steering them is a fork with a seed, never an edit to the file.
- **Steering.** Every layer reads from it to know what to do: L1 reads prompt fields + brief + surface overrides, L2 reads operational memory + surface state, L3 reads plan + runtime failures.

**What it is NOT** — not the trace archive (per-sample results live in `measurements/`, referenced by ID: [`scoring-and-memory.md`](scoring-and-memory.md)); not the frozen target shape (that is `JobSearchPoint`); not the campaign config (operator knobs — max rounds, patience, `n_variants`, which L2's `l1_overrides` may raise to 3× — live on `CampaignConfig.optimization` and never mutate).

**The prompt field set, the layer-specific surfaces and the render chain** — owned by [`../developer/dispatch-hub.md`](../developer/dispatch-hub.md). This page owns only what the record carries, and why state off it does not survive interruption.

## Pointers

- Architecture invariants: [`../architecture.md`](../architecture.md) §0
- Dispatch routing, L1 / L2 internals + four wounds: [`../developer/dispatch-hub.md`](../developer/dispatch-hub.md), [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md)
- Candidate elimination (PoBB): [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md)
- Escalation signal stream: [`../operations/observability.md`](../operations/observability.md)
