# The Three-Layer Loop

Three layers, three cadences. L1 fires every round. L2 fires only when L1 stalls. L3 fires when L2's strategy stops moving the needle.

```
┌─ ONE ROUND ────────────────────────────────────────────────────────────┐
│  L1 GENERATE — evolve N individuals                                    │
│         ↓                                                              │
│  L1 EVALUATE — measure each individual's fitness against the dataset   │
│         ↓                                                              │
│  L1 CRITIQUE — analyze fitness; direct next generation                 │
│                                                                        │
│  ── ESCALATION (when L1 stalls) ────────────────────────────────────── │
│  L2 REFINE CONTEXT — rewrite the task framing fed to L1                │
│  L3 MODIFY PLAN — rewrite the strategic plan L1 works within           │
└────────────────────────────────────────────────────────────────────────┘
```

## What each layer decides

| Layer | Fires | Decides | Does NOT decide |
|-------|-------|---------|-----------------|
| **L1 Generate** | Every round | Pipeline parameters (prompt fields, thresholds, model params, schema overrides) | Task framing, meta-settings |
| **L1 Critique** | Every round | Which failure patterns to focus on; what L1 should prioritize next | Specific parameter values |
| **L2 Refine** | On L1 stall | Any subset of: brief, optimizer params, task context, L1-surface overrides, action (normal vs probe). Owns L1's prompt-surface state. | Pipeline parameters |
| **L3 Plan** | L2 stalls | The strategic plan — a high-level framework shaping how L1 searches | Pipeline parameters, task context |

L2 reframes *how* L1 searches by writing onto the `OptSearchPoint`; L1 still picks specific values. Same relationship between L3 and L2.

## How layers communicate

Layers write to a shared `OptSearchPoint`; the next layer reads from there. See [`state-record.md`](state-record.md) for the record's full surface.

- **L2 → L1.** L2 writes a 2–3 sentence `brief` to `OptSearchPoint.l2_brief`. L1-generate reads it next round as primary signal. One-round window: cleared on improvement.
- **L3 → {L1, L2}.** L3 writes a strategic framework to `OptSearchPoint.plan`. Both L1-generate and L2 read it. L1 treats it as a constraint on generation; L2 as operating context for its brief. Persistent — survives until L3 replaces it.
- **L1-generate is fan-in.** Reads `plan` (L3) and `l2_brief` (L2) the same round.

Every optimizer LLM call shares one path: per-call `DispatchState` → `LAYER_CONFIGS[layer]` → `compile_prompt_vars`. Field tables: [`../developer/README.md`](../developer/README.md).

## What L1 proposes each round

Three kinds of knobs, discovered from the target pipeline's active nodes:

- **Prompt fields** — persona, task intent, problem description, instruction, thinking style, answer format, few-shot examples. Only fields exposed by the pipeline's LLM nodes.
- **Model parameters** — temperature, model name, reasoning effort.
- **Pipeline parameters** — thresholds, budget caps, sampling settings (non-LLM nodes).

The knob set is read from the backend's self-description at init.

## The critique step

After scoring, before the next round's generate, the critique runs. The only place in the loop that reads raw per-query results. Feeds forward:

- **L1 Generate next round** — primary signal, unless L2 has just fired.
- **L2 Refine on escalation** — L2 builds on the critique rather than re-deriving it.

`l1_critique → l1_generate` is **not** self-healing — it fires every round regardless of failure. Self-healing is failure-driven; the critique is performance-driven. Different mechanism, similar plumbing: critique writes `OptSearchPoint.l1_critique_text` + `failure_analysis`; L1's prompt reads via `{{failure_analysis}}`.

## Escalation is additive

When L3 fires, the next round still has L3, L2, and L1 all running. Higher layers don't replace lower ones; they constrain them.

---

## L2 in detail

The optimizer's strategist. Doesn't write prompts — shapes what the prompt-writer (L1) sees, knows, and is allowed to do. Implementation: [`../developer/l2-internals.md`](../developer/l2-internals.md).

### When L2 fires

After every L1 round the runner checks whether best accuracy improved. If yes, L2 stays out. If no, the L1 stall counter ticks; once it hits `l1_patience`, L2 fires the next round. On healthy campaigns L2 stays dormant for many rounds.

### What L2 sees

- Round-winner accuracy.
- Failure clusters from the critique step.
- Validation failures (L1 proposed a value outside the allowed set).
- Runtime failures (pipeline raised warnings — e.g. reasoning budget exhausted).
- Axis-index digest — which knobs have been tried, what helped.
- L1's current surface — every section visible to L1 plus any prior L2 overrides.

### What L2 writes

L2's output is a flat dict. Every field is independent — write any combination, or nothing.

| Field | Effect |
|-------|--------|
| `brief` | 2–3 sentence note injected into L1's next prompt as primary signal. |
| `optimizer_params` | Tune creativity, candidate budget, variant strategy. |
| `task_context` | Refine the structured domain understanding. |
| `scheme_overrides` | `{section: bool}` — gate L1 surface sections on/off. |
| `text_overrides` | `{section: str}` — replace a section's text with hand-written content. |
| `template_override` | Replace `problem_description` body. Reserve for fundamental reframing. |
| `action` | `normal_round` (full set) or `probe_round` (warned queries only). |

The last three are L2's levers over L1's surface — see [`../developer/l1-generate-surface.md`](../developer/l1-generate-surface.md) for the closed catalogue they target.

### Decision table — what L2 typically writes

| Scenario | L2 writes |
|----------|-----------|
| **Default.** Critique flags a clear failure pattern. | `brief` (names the axis and direction). |
| **Quiet.** Failures look noisy; no axis points at one knob. | nothing. Honest non-action — a guess would churn the search. |
| **Brief + retune.** Pattern is named but search is too narrow / wide. | `brief` + `optimizer_params` (creativity, n_variants). |
| **Probe.** One narrow failure dominates; full set adds noise. | `action: probe_round` + `brief` testing the hypothesis on warned queries. |
| **Toggle off.** A section is firing on a non-issue and pulling variants away. | `scheme_overrides: {section: false}` + `brief`. Override persists until flipped back. |
| **Replace text.** A section is sparse / generic; L2 has evidence for a substitute. | `text_overrides: {section: "..."}`. Persists across rounds. |
| **Reframe.** Briefs haven't moved the needle for several L2 fires; framing is wrong. | `template_override` + `brief`. Large mutation — reserve for wrong-framing. |

---

Five LLM call sites: `restructure` (one-time decomposition at init), `l1_generate`, `l1_critique`, `l2_context`, `l3_plan`. The critique-and-refine pattern is inspired by [PromptWizard](https://arxiv.org/abs/2405.18369). Broader paradigm: [`../research/related-work.md`](../research/related-work.md). Fitness comparison uses PoBB — see [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md). Self-healing: [`self-healing.md`](self-healing.md).
