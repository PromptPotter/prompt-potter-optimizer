# The Three-Layer Loop

PromptPotter's optimizer runs three layers, each at a different cadence. L1 changes every round. L2 fires when L1 has stalled for several rounds in a row. L3 fires when L2 also fails to help.

```
┌─ ONE ROUND ────────────────────────────────────────────────────────────┐
│                                                                        │
│  L1 GENERATE — propose N candidate configurations                      │
│         ↓                                                              │
│  L1 EVALUATE — score each candidate against the dataset                │
│         ↓                                                              │
│  L1 CRITIQUE — analyze results; pick a direction for next round        │
│                                                                        │
│  ── ESCALATION (when L1 stalls) ────────────────────────────────────── │
│                                                                        │
│  L2 REFINE CONTEXT — rewrite the task framing fed to L1                │
│                                                                        │
│  L3 MODIFY PLAN — rewrite the strategic plan L1 works within           │
└────────────────────────────────────────────────────────────────────────┘
```

## Why three layers

Each layer operates at a different speed. L1 changes every round — fast, fine-grained parameter tuning. L2 changes on stall — a slower context shift, invoked when several rounds of L1 fail to improve. L3 changes on strategic failure — rarely, when L2 itself stalls.

Keeping these cadences separate prevents a fast-moving parameter search from destabilizing the slower strategic context. If every layer changed every round, the optimizer would thrash. If only one layer existed, it would either be too fine-grained to break out of plateaus or too coarse to do useful tuning.

## What each layer decides

| Layer | Fires | Decides | Does NOT decide |
|-------|-------|---------|-----------------|
| **L1 Generate** | Every round | Pipeline parameters (prompt fields, thresholds, model params, schema overrides) | Task framing, meta-settings |
| **L1 Critique** | Every round | Which failure patterns to focus on; what L1 should prioritize next | Specific parameter values |
| **L2 Refine** | Escalation only (stall, degradation) | Task context, meta-settings (creativity, candidate budget), a directive that steers L1 | Pipeline parameters |
| **L3 Plan** | L2 stalls | The strategic plan — a high-level framework shaping how L1 searches | Pipeline parameters, task context |

L2 does not prescribe parameter values. It reframes *how* L1 searches; L1 still picks the specific values. Same relationship between L3 and L2.

## What L1 proposes each round

L1 Generate chooses among three kinds of knobs, all discovered from the target pipeline's active nodes:

- **Prompt fields.** persona, task intent, problem description, instruction, thinking style, answer format, few-shot examples. Only the fields exposed by the pipeline's LLM nodes are available; a pipeline with no LLM nodes has no prompt to tune.
- **Model parameters.** temperature, model name, reasoning effort — whatever the backend's LLM nodes accept.
- **Pipeline parameters.** Thresholds, budget caps, sampling settings — whatever the backend's non-LLM nodes expose.

The set of knobs isn't fixed. It's read from the backend's self-description at init time and flows into every round from there.

## The critique step

After scoring, before the next round's generate, the critique step runs. It is the only place in the loop that reads raw per-query results — every hit, every miss, the exact outputs. It produces a structured analysis that feeds forward:

- **Into L1 Generate next round** — as the primary signal for what to try next, unless L2 has just fired (in which case L2's directive takes priority).
- **Into L2 Refine** on escalation — so L2 can build on the critique rather than re-deriving it.

The critique is the every-round intelligence hub. It's what makes L1 Generate informed rather than random.

## Escalation is additive, not preemptive

A stall escalates upward, but each layer continues to run in its own slot. When L3 fires, the next round still has L3, L2, and L1 all running — L3's plan shapes L2's refinement, which shapes L1's generation. Higher layers don't replace lower ones; they constrain them.

## The dynamic field set

The eight prompt fields aren't a hard ceiling. L2 can add a field — say, `domain_constraints` — to widen the search space when prior fields haven't captured the right axis. It can also remove a field that's proved irrelevant. This keeps the prompt-field set matched to what the current problem actually demands.

For the mechanics of each layer — what data flows in, what memory persists, what signals escalation — see [self-healing.md](self-healing.md) and [../developer/information-flow.md](../developer/information-flow.md).

## Inspiration and call sites

The critique-and-refine pattern is inspired by [PromptWizard](https://arxiv.org/abs/2405.18369). This separates failure analysis (critique) from candidate generation (L1 generate), which keeps the two from interfering.

Five LLM call sites in the loop: `restructure` (one-time prompt decomposition at init), `l1_generate`, `l1_critique`, `l2_context`, `l3_plan`.

Candidate comparison uses confidence intervals and two-proportion significance tests. Non-parametric tests are planned.
