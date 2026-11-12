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

The loop exits on a **stopping rule** — `halt_at_fitness`, a budget axis spent, lives exhausted, the
controller's own exhaustion, or a fault; the set is `domain/phases.py::StopReason` — not on a fixed trial count. Potter's two efficiency mechanisms ride the measurement seam every
optimizer rides: candidate budget allocation ([`../methods/candidate-elimination.md`](../methods/candidate-elimination.md))
and hard-sample ordering ([`../methods/verdict-resolution.md`](../methods/verdict-resolution.md) § The round order).

The critique is the only step that reads raw per-sample results; **`l1_critique → l1_generate` is
performance-driven feedback, not failure-driven healing** (after [PromptWizard](https://arxiv.org/abs/2405.18369)).
**It searches a tree, not a trail**: a spent branch rewinds to the round the L2/L3 layer's `fork_proposal` names (`stand_on`), else the one a UCB rule picks (`application/bench/node_context.py::Lineage.branch_round`) —
[`../research/landscape.md`](../research/landscape.md) § Comparison to MCTS.

## Escalation

Potter's controller chooses between its manifest's `pipelines:` entries round by round; the bench decides
nothing on it. Lazy; higher layers constrain lower ones; no layer rewrites `task_context` — no L1/L2/L3 wire
schema has a field for it. **Rules route, never diagnose or stop**: pure predicates over a frozen snapshot,
first match wins, once per round. Which preemptors fire L2 before patience runs out:
[`../developer/dispatch-hub.md`](../developer/dispatch-hub.md) § Trigger — when L2 fires.

A *healthy* round is L1-critique's job; a *systemic fault* (evidence starvation) routes to L2, which self-heals
or — on a fault no prompt move can fix — emits `terminate_proposal`, the one stop a layer authors: the run halts
with a human-action request carrying the backend's reason, and the operator fixes it and `resume`s.

## Origin and check-in

**Origin = C0**, resolved to an `OptSearchPoint` — the same type every candidate is; for a fork, the point
it branches from. Round 0 *is* C0, measured. Each arm's lift is read against its **reference** (its
lineage parent): [`../methods/verdict-resolution.md`](../methods/verdict-resolution.md) § What an arm's lift is read against.

**Check-in completes the origin and gates it**, and it is the bench's, so every optimizer starts from the same
resolved origin. An upload is not an origin until the pipeline's required inputs resolve, so origin is
per-pipeline. One LLM resolver *proposes* the column map, the decomposed prompt fields and the `task_context`,
once per dataset framing.

**Two gates, because completeness ≠ scoreability.**

- **Readiness** — static, no LLM: mint is blocked until query, ground truth and framing are confirmed and every
  active LLM node owns a model. No single prompt field is gated.
- **The round-0 origin gate** — empirical: an origin graded `critical` halts before round 1, and under the
  default `strict` mode `degraded` halts too. Coverage is asked before any rate, and an under-measured origin is graded `critical`
  (cause `origin_unmeasured`, `domain/results_health.py`) and halts.

## What one round carries forward

The individuals (the configuration and nothing else) and the round's `optimizer_state`. Every round closes onto
the cycle's ledger, and resume reads the rounds that stand there; state on neither does not survive
interruption. `<cycle_dir>/rounds/round_NNNN.json` is a checkout — steering it is a fork with a seed, never an
edit to the file. The campaign config never mutates.
