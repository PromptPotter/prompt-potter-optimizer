# The Three-Layer Loop

PromptPotter's optimizer runs three layers, each at a different cadence. L1 changes every round. L2 fires only when L1 has stalled — writing onto the individual to steer the next round. L3 fires when L2's strategy stops moving the needle.

```
┌─ ONE ROUND ────────────────────────────────────────────────────────────┐
│                                                                        │
│  L1 GENERATE — evolve N individuals                                    │
│         ↓                                                              │
│  L1 EVALUATE — measure each individual's fitness against the dataset   │
│         ↓                                                              │
│  L1 CRITIQUE — analyze fitness; direct next generation                 │
│                                                                        │
│  ── ESCALATION (when L1 stalls) ────────────────────────────────────── │
│                                                                        │
│  L2 REFINE CONTEXT — rewrite the task framing fed to L1                │
│                                                                        │
│  L3 MODIFY PLAN — rewrite the strategic plan L1 works within           │
└────────────────────────────────────────────────────────────────────────┘
```

## Why three layers

Each layer fires at a different cadence: L1 every generation, L2 on consecutive-stall escalation, L3 when L2 itself stalls. Separating cadences keeps intra-generation mutation from destabilising the meta-strategy that constrains it, while still letting the meta-strategy reset when the population gets stuck.

## What each layer decides

| Layer | Fires | Decides | Does NOT decide |
|-------|-------|---------|-----------------|
| **L1 Generate** | Every round | Pipeline parameters (prompt fields, thresholds, model params, schema overrides) | Task framing, meta-settings |
| **L1 Critique** | Every round | Which failure patterns to focus on; what L1 should prioritize next | Specific parameter values |
| **L2 Refine** | On L1 stall (escalation-gated) | Any subset of: directive, optimizer params, task context, L1-surface overrides, action (normal vs probe round). Owns L1's prompt-surface state. | Pipeline parameters |
| **L3 Plan** | L2 stalls | The strategic plan — a high-level framework shaping how L1 searches | Pipeline parameters, task context |

L2 does not prescribe parameter values. It reframes *how* L1 searches — by writing onto the individual record (the `OptSearchPoint`) — and L1 still picks the specific values. Same relationship between L3 and L2. L2's full role is documented in [what-is-l2.md](what-is-l2.md), [l2-decision-tree.md](l2-decision-tree.md), [l1-generate-surface.md](l1-generate-surface.md), and [optsearchpoint-as-state.md](optsearchpoint-as-state.md).

## What L1 proposes each round

L1 Generate chooses among three kinds of knobs, all discovered from the target pipeline's active nodes:

- **Prompt fields.** persona, task intent, problem description, instruction, thinking style, answer format, few-shot examples. Only the fields exposed by the pipeline's LLM nodes are available; a pipeline with no LLM nodes has no prompt to tune.
- **Model parameters.** temperature, model name, reasoning effort — whatever the backend's LLM nodes accept.
- **Pipeline parameters.** Thresholds, budget caps, sampling settings — whatever the backend's non-LLM nodes expose.

The set of knobs isn't fixed. It's read from the backend's self-description at init time and flows into every round from there.

## The critique step — round-over-round feedback

After scoring, before the next round's generate, the critique step runs. It is the only place in the loop that reads raw per-query results — every hit, every miss, the exact outputs. It produces a structured analysis that feeds forward:

- **Into L1 Generate next round** — as the primary signal for what to try next, unless L2 has just fired (in which case L2's directive takes priority).
- **Into L2 Refine** on escalation — so L2 can build on the critique rather than re-deriving it.

The critique is the every-round intelligence hub. It's what makes L1 Generate informed rather than random.

`l1_critique → l1_generate` is **not** part of the self-healing canon — it fires every round whether anything failed or not. Self-healing is failure-driven (Loops 1–4 in [self-healing.md](self-healing.md)); this critique loop is performance-driven feedback. Different mechanism, similar plumbing: the critique writes `OptSearchPoint.l1_critique_text` and `failure_analysis`, which L1's prompt reads via the `{{failure_analysis}}` slot in `optimizer_pipeline.json::resolved_prompts['l1_generate/1']`.

## Escalation is additive, not preemptive

A stall escalates upward, but each layer continues to run in its own slot. When L3 fires, the next round still has L3, L2, and L1 all running — L3's plan shapes L2's refinement, which shapes L1's generation. Higher layers don't replace lower ones; they constrain them.

For the mechanics of each layer — what data flows in, what memory persists, what signals escalation — see [self-healing.md](self-healing.md) and [../developer/information-flow.md](../developer/information-flow.md). L2 can toggle and replace prompt-surface sections via section overrides on the individual; see [l1-generate-surface.md](l1-generate-surface.md) and [../developer/l1-generate-surface.md](../developer/l1-generate-surface.md). Add/remove of fields from the *catalogue itself* is still a code-level change.

## Inspiration and call sites

The critique-and-refine pattern is inspired by [PromptWizard](https://arxiv.org/abs/2405.18369). This separates failure analysis (critique) from individual generation (L1 generate), which keeps the two from interfering. The broader paradigm — LLM-driven program evolution — is surveyed in [`../research/related-work.md`](../research/related-work.md), where PromptPotter sits alongside AlphaEvolve, OpenEvolve, MIPROv2, GEPA, and PromptWizard as flat siblings under one umbrella.

Five LLM call sites in the loop: `restructure` (one-time prompt decomposition at init), `l1_generate`, `l1_critique`, `l2_context`, `l3_plan`.

Individual fitness comparison uses paired Wilcoxon signed-rank with Holm-Bonferroni correction — see [candidate-elimination.md](../methods/candidate-elimination.md).
