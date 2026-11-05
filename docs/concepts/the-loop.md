# The Three-Layer Loop

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-light.svg">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-light.svg" alt="The loop: a task and a labeled dataset enter an optimizer setup, then generate, score and critique repeat until a stopping rule fires. L2 and L3 escalate lazily on a stall; a measurement archive replays instead of re-measuring." width="760">
  </picture>
</p>

[One round](../methods/candidate-elimination.md) is the unit; a campaign is rounds under three layers. This is
potter's loop — another optimizer is its own manifest and inherits none of it.

| Layer | Fires | What it does |
|---|---|---|
| **L1 · generate, evaluate, critique** | every round | Mutates a generation of individuals, measures each one's fitness on the search split, reads the per-sample misses to steer the next generation. |
| **L2 · refine context** | when L1 stalls | Rewrites *how* L1 searches: which evidence it sees and how hard it explores. |
| **L3 · modify plan** | when L2 stalls | Rewrites the strategic plan L1 works within. |

`l1_generate` produces N candidates from the selected parent; `l1_score`, the manifest's one measurement
node, walks them through the sole scoring gateway (`application/scoring/search_point_scorer.py`: `open_walk` →
`run_walks` → `close_walk`); `l1_critique` reads the round's outcomes and writes a structured critique that
feeds the next `l1_generate`. The loop exits on a **stopping rule** — `halt_at_accuracy`, a budget axis spent,
or the controller's own exhaustion — not on a fixed trial count.

Potter's two efficiency mechanisms ride the measurement's `run_walks(backfills=, blocks=, checks=)` seam that
every optimizer's sampler and eliminator ride: candidate budget allocation (its `eliminator`,
[`../methods/candidate-elimination.md`](../methods/candidate-elimination.md)) and hard-sample ordering (its
`sampler`, [`../methods/verdict-resolution.md`](../methods/verdict-resolution.md) § The round order).

L2 writes L1's attention surface as one node overlay — `L2Memory.steer["l1_generate"]`, carrying `layout`
(which panels L1 sees) and the call settings. L3 writes a strategic framework to `L3Memory.plan`. Every
loop-layer LLM call shares one path: per-call `InjectionBundle` → `NODE_LAYOUTS[node]` → `DispatchHub.fill`
([`../developer/dispatch-hub.md`](../developer/dispatch-hub.md)). The critique is the only step that reads raw
per-sample results; **`l1_critique → l1_generate` is performance-driven feedback, not failure-driven healing.**
Five LLM call sites: `checkin` (around the loop, skipping the dispatch hub), `l1_generate`, `l1_critique`,
`l2_context`, `l3_plan`. Critique-and-refine after [PromptWizard](https://arxiv.org/abs/2405.18369).

**It searches a tree, not a trail**: a spent branch rewinds to the ancestor a UCB rule picks —
[`../research/landscape.md`](../research/landscape.md) § Comparison to MCTS.

## Escalation

Potter's controller chooses between its manifest's `pipelines:` entries round by round; the bench decides
nothing on it. Lazy; higher layers constrain lower ones; no layer rewrites `task_context` — no L1/L2/L3 wire
schema has a field for it. **Rules route, never diagnose or stop**: `escalation/rules.py::decide_escalation(EscalationInputs)`
— pure predicates over a frozen snapshot, priority-sorted, first match wins, once per round; adding a rule is
one row there. Which preemptors fire L2 before patience runs out — owned by
[`../developer/dispatch-hub.md`](../developer/dispatch-hub.md) § Trigger — when L2 fires.

A *healthy* round is L1-critique's job; a *systemic fault* (evidence starvation) routes to L2, which self-heals
or — on a fault no prompt move can fix — emits `terminate_proposal`, the one stop a layer authors: the run halts
with a human-action request carrying the backend's reason, and the operator fixes it and `resume`s.

## Origin and check-in

**Origin = C0**, resolved to an `OptSearchPoint` (`application/origin.py::resolve_origin_opt_search_point`) —
the same type every candidate is; for a fork, the point it branches from. Round 0 *is* C0, measured
(`establish_campaign_origin`); its level is `CycleResult.origin`. Each arm's lift is read against its
**reference** (`IndividualLineage.parent_ids`; side `a` of its `vs_reference`):
[`../methods/verdict-resolution.md`](../methods/verdict-resolution.md) § What an arm's lift is read against.

**Check-in completes the origin and gates it**, and it is the bench's, so every optimizer starts from the same
resolved origin. An upload is not an origin until the pipeline's required inputs resolve (column map, dataset
binding, any node-raised dependency), so origin is per-pipeline. One LLM resolver
(`application/datasets/origin_resolve.py`) *proposes* the column map, the decomposed prompt fields — including
an `answer_format` satisfying the scorer's extraction contract (`formula/matchers.py::MATCHERS`) — and the
`task_context`, once per dataset framing.

**Two gates, because completeness ≠ scoreability.**

- **Readiness** — static, no LLM (`application/datasets/origin_readiness.py`): mint is blocked until query,
  ground truth and framing are confirmed and every active LLM node owns a model. No single prompt field is
  gated; a closed label set is appended to `answer_format` deterministically (`DraftFile.committed_prompts`).
- **The round-0 origin gate** — empirical: a floor the health signal (`domain/results_health.py`) grades
  `critical` halts before round 1, and under the default `strict` mode `degraded` halts too
  (`application/runner/termination.py::origin_gate_tripped`). It asks coverage before any rate: cells a
  cut-short walk never dispatched arrive as `not_attempted`, so an under-measured origin is reported
  unmeasured, not graded broken.

## The state record — what one round carries forward

- **The individuals** (`OptSearchPoint`, `domain/opt_search_point.py`) — the configuration and nothing else:
  per prompt node its fields and shots (`prompts`), its `pipeline_params`, its `lineage`; its `id` is its
  content. Two independent namespaces ride it — prompt fields and pipeline parameters — and L1 mutates both in
  one proposal.
- **The round's `optimizer_state`** (`domain/optimizer_state.py::OptimizerState`) — typed `StatePart`s:
  `slices` carried across rounds (potter's `L2Memory`, `L3Memory`), `outputs` of one round (the `Critique`),
  the bench's `population`, and the `prompt_hashes` a resume diverges on.

Every round closes onto the cycle's ledger, and resume reads the rounds that stand there; state on neither
record does not survive interruption. `<cycle_dir>/rounds/round_NNNN.json` is a checkout — steering it is a
fork with a seed, never an edit to the file. Not here: the trace archive
([`scoring-and-memory.md`](scoring-and-memory.md) § The archive), the frozen target shape (`JobSearchPoint`),
the campaign config (`CampaignConfig.optimization`, which never mutates; L2's `l1_overrides` may raise
`n_variants` to 3×).
