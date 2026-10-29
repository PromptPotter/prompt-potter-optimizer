# The Three-Layer Loop

> **Audience:** Developer reference. Operators see [`../manual/`](../manual/) for usage docs.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-light.svg">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-light.svg" alt="The loop: a task and a labeled dataset enter an optimizer setup, then generate, score and critique repeat until a stopping rule fires. L2 and L3 escalate lazily on a stall; a measurement archive replays instead of re-measuring." width="760">
  </picture>
</p>

[One round](../methods/candidate-elimination.md) is the unit; a campaign is rounds under three layers. This page describes potter's loop — another optimizer is its own manifest and inherits none of it.

| Layer | Fires | What it does |
|---|---|---|
| 🧬&nbsp;**L1&nbsp;·&nbsp;generate, evaluate, critique** | every&nbsp;round | Mutates a generation of individuals, measures each one's fitness on your dataset, and reads the raw per-sample misses to steer the next generation. |
| 🔭&nbsp;**L2&nbsp;·&nbsp;refine&nbsp;context** | when&nbsp;L1&nbsp;stalls | Rewrites *how* L1 searches: which evidence it sees and how hard it explores. |
| 🧭&nbsp;**L3&nbsp;·&nbsp;modify&nbsp;plan** | when&nbsp;L2&nbsp;stalls | Rewrites the strategic plan L1 works within. |

One round is the selected manifest's `default` pipeline; potter's is generate → score → critique. `l1_generate` produces N candidates from the selected parent; `l1_score`, the manifest's one measurement node, walks them against the search split through the **sole scoring gateway** (`application/scoring/search_point_scorer.py`: `open_walk` → `run_walks` → `close_walk`); `l1_critique` reads the round's outcomes and writes a structured critique that feeds the next `l1_generate`. The loop exits on a **stopping rule** — goal reached (`halt_at_accuracy`), a budget axis spent, or the controller's own exhaustion — not on a fixed trial count.

Potter's two efficiency mechanisms ride the measurement's `run_walks(backfills=, blocks=, checks=)` seam that every optimizer's sampler and eliminator ride: candidate budget allocation (its `eliminator`, [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md)) and hard-sample ordering (its `sampler`, [`../methods/verdict-resolution.md`](../methods/verdict-resolution.md) § The round order — `build_round_order`, one static order per round).

L1 picks specific values. L2 reframes *how* L1 searches by writing L1's attention surface as one node overlay — `L2Memory.steer["l1_generate"]`, carrying `layout` (which panels L1 sees) and the call settings (how hard it explores). L3 writes a strategic framework to `L3Memory.plan`. Every loop-layer LLM call shares one path: per-call `InjectionBundle` → `NODE_LAYOUTS[node]` → `DispatchHub.fill`.

The critique step is the only place in the loop that reads raw per-sample results; it feeds forward to next-round L1 (primary) and to L2 (operating context on escalation). **`l1_critique → l1_generate` is performance-driven feedback, not failure-driven healing** — different mechanism from self-healing.

Five LLM call sites: `checkin` (runs around the loop and skips the dispatch hub; § Origin and check-in), `l1_generate`, `l1_critique`, `l2_context`, `l3_plan`. Critique-and-refine pattern inspired by [PromptWizard](https://arxiv.org/abs/2405.18369).

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/tree-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/tree-light.svg">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/tree-light.svg" alt="A search tree: origin, rounds 1 and 2, then a branch through rounds 3 and 4 that is spent. The search rewinds to round 2 and a second branch reaches the winner." width="880">
  </picture>
</p>

**It searches a tree, not a trail**: a spent branch rewinds to the ancestor a UCB rule picks — [`../research/related-work.md`](../research/related-work.md) § Comparison to MCTS.

## Escalation

**Escalation is potter's, not the bench's.** Potter's controller chooses between its manifest's `pipelines:` entries round by round; the bench decides nothing on it.

- **Lazy** — L2 fires on an L1 stall, L3 on an L2 stall.
- **Higher layers constrain lower ones, never replace them.**
- **No layer rewrites `task_context`**: the framing is operator-authored at check-in (`CheckinOutput.task_context`) and frozen for the run, structurally so — no L1/L2/L3 wire schema has a field for it.
- **Rules route, never diagnose or stop.** Firing lives in one function, `escalation/rules.py::decide_escalation(EscalationInputs)` — pure predicates over a frozen snapshot, priority-sorted, first match wins, once per round; adding a rule is one row there. **Which preemptor rules fire L2 before patience runs out** — owned by [`../developer/dispatch-hub.md`](../developer/dispatch-hub.md) § Trigger — when L2 fires.

The split is deliberate: a *healthy* round is L1-critique's job; a *systemic fault* (evidence starvation) routes to L2, which either self-heals or — on a fault no prompt move can fix — emits `terminate_proposal`, the one stop a layer authors: the run halts with a human-action request carrying the backend's reason, and the operator fixes it and `resume`s.

## Origin and check-in

Say "origin", never "baseline".

**Origin = the starting configuration = C0.** An individual **is** a configuration, so the origin resolves to an `OptSearchPoint` (`application/origin.py::resolve_origin_opt_search_point`) — the same type every candidate is. For a fork it is the point the fork branches *from*. Round 0 *is* C0, measured (`establish_campaign_origin`); its own level is `CycleResult.origin`.

**Origin is the parent at offset 0.** The general relation is *parent* — what a candidate was derived from (`IndividualLineage.parent_ids`: one for a mutation, several for a crossover). Each arm's lift is read against one individual, its **reference** (side `a` of its `vs_reference`): [`../methods/verdict-resolution.md`](../methods/verdict-resolution.md) § What an arm's lift is read against — `lift_reference`. **Reserve "origin" for offset 0 and the fork point; everywhere else say parent.**

**Check-in completes the origin and gates it**, and it is the bench's, not an optimizer's, so every optimizer starts from the same resolved origin. An upload is not an origin until the pipeline's **required inputs** resolve (column map, dataset binding, any node-raised dependency), so origin is **per-pipeline**. One LLM resolver (`application/datasets/origin_resolve.py`) *proposes* the column map, the decomposed prompt fields — including an `answer_format` satisfying the **scorer's** extraction contract (`formula/matchers.py::MATCHERS`) — and the `task_context`. It runs once per dataset framing: on the first mint that finds none committed (`new --task-file` / `--task-text`, `new <raw file>`, or the web check-in).

**Two gates, because completeness ≠ scoreability.**

- **Readiness** — static, no LLM (`application/datasets/origin_readiness.py`): mint is blocked until query, ground truth and framing are confirmed and every active LLM node owns a model. No single prompt field is gated, since the optimizer evolves them; a closed label set is appended to `answer_format` deterministically (`DraftCampaign.committed_prompts`).
- **The round-0 origin gate** — empirical: a floor the health signal (`domain/results_health.py`) grades `critical` halts before round 1, and under the default `strict` mode a `degraded` floor halts too (`application/runner/termination.py::origin_gate_tripped`). It asks **coverage before any rate**: cells a cut-short walk never dispatched arrive as `not_attempted`, so an under-measured origin is reported unmeasured, not graded broken.

## The state record — what one round carries forward

Every round carries two records, and they are different things:

- **The individuals** (`OptSearchPoint`, often *OSP*, `domain/opt_search_point.py`) — the configuration and nothing else: per prompt node its fields and shots (`prompts`), its resolved node config (`pipeline_params`), and its `lineage`. Its `id` is its content. **No optimizer state, campaign framing or round memory rides it** ([`../../promptpotter/domain/CLAUDE.md`](../../promptpotter/domain/CLAUDE.md)).
- **The round's `optimizer_state`** (`domain/optimizer_state.py::OptimizerState`) — the optimizer's working memory as typed `StatePart`s: `slices` carried across rounds (potter's `L2Memory`, `L3Memory`), `outputs` produced by one round (the `Critique`), the bench's `population`, and the `prompt_hashes` a resume diverges on.

Two parameter namespaces co-exist on the individual: **prompt fields** (the decomposition the optimizer mutates) and **pipeline parameters** (thresholds / model / temperature / retrieval budgets — anything the pipeline's nodes expose). Names can overlap; the namespaces are independent, and L1 mutates both in one proposal.

Both records are the working memory for two reasons:

- **Persistence.** Every round closes onto the cycle's ledger, and resume reads the rounds that stand there; state on neither record does not survive interruption. `<cycle_dir>/rounds/round_NNNN.json` is a checkout — steering it is a fork with a seed, never an edit to the file.
- **Steering.** L1 reads the parent individual, the critique and L2's steer; L2 and L3 read their own slices.

**What it is NOT** — not the trace archive (per-sample results live in `measurements/`: [`scoring-and-memory.md`](scoring-and-memory.md) § The archive); not the frozen target shape (`JobSearchPoint`); not the campaign config (max rounds, patience, `n_variants` — which L2's `l1_overrides` may raise to 3× — live on `CampaignConfig.optimization` and never mutate).

**The prompt field set, the layer-specific surfaces and the render chain** — owned by [`../developer/dispatch-hub.md`](../developer/dispatch-hub.md).
