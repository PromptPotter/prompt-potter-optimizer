# Concepts

PromptPotter tunes prompts and pipeline configs against a labelled dataset. Every measurement costs money — the design is **maximize fitness, minimize spend**.

## The loop

Three layers, each wraps the next like a system prompt wraps a user prompt:

- **L1** mutates the prompt template's fields (persona, task instruction, …) and the pipeline params, then scores each variant.
- **L2** writes a **CONTEXT** outline that wraps L1, and tweaks L1's fields when L1 stalls.
- **L3** writes a **PLAN** outline that wraps L2, and is rewritten when L2 stalls.

CONTEXT and PLAN live on disk inside each trial file — the loop's actual config, inspectable and editable. "Add this to the plan" means exactly that.

## Spend control

- **Search-only-with-evidence.** Each variant runs against ~3–5 samples by default. Only variants with statistical evidence of being promising get extended; the rest drop out before the bill grows.
- **Hard-sample dashboard.** Samples that everyone aces or everyone fails carry no signal. The dashboard surfaces samples that actually separate variants, and the loop preferentially scores on those.

## Register

| Page | What it covers |
|------|----------------|
| [Campaign lifecycle](campaign-lifecycle.md) | `init` → baseline → rounds → finalize |
| [Three-layer loop](three-layer-loop.md) | L1 / L2 / L3 cadence and what each writes |
| [What L2 is](what-is-l2.md) | L2's role and the CONTEXT outline it owns |
| [L2's decision tree](l2-decision-tree.md) | L2's five choices, with one scenario per variant |
| [L1's prompt surface](l1-generate-surface.md) | What L1 sees and which parts L2 can override |
| [The individual record](optsearchpoint-as-state.md) | The record carrying CONTEXT, PLAN, and L1 overrides |
| [Mid-round elimination](../methods/candidate-elimination.md) | "Search-only-with-evidence" in detail |
| [Hard-sample dashboard](../methods/exploration-exploitation.md) | Sample selection in detail |
| [Self-healing](self-healing.md) | Four LLM-to-LLM healing loops |
| [Scoring and traces](scoring-and-traces.md) | Traces are facts; scores are policy |
| [Measurement archive](measurement-archive.md) | The cross-run database core |
| [Fork tree and sweep](fork-tree-and-sweep.md) | Campaigns as cycle trees |
| [Axis index](axis-index.md) | Knowledge accumulation across campaigns |
| [Prompts and individuals](prompts-and-individuals.md) | The 8-field prompt decomposition |
| [Nodes and pipelines](nodes-and-pipelines.md) | Pipeline node anatomy |
| [Glossary](glossary.md) | Terms used across the docs |

Looking for implementation? See [`../developer/`](../developer/README.md).
