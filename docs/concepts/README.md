# Concepts

How the loop works. One page per topic. Code anchors live in [`../developer/`](../developer/README.md).

## The loop

Three layers, each wraps the next like a system prompt wraps a user prompt:

- **L1** mutates the prompt template's fields (persona, task instruction, …) and pipeline params, then scores each variant.
- **L2** writes a **CONTEXT** outline that wraps L1, and modifies L1's fields when L1 stalls.
- **L3** writes a **PLAN** outline that wraps L2, and is rewritten when L2 stalls.

CONTEXT and PLAN live on disk inside each trial file — the loop's actual config, inspectable and editable. "Add this to the plan" means exactly that.

## Spend control

- **Search-only-with-evidence.** Each variant runs against ~3–5 samples by default. Only variants with statistical evidence of being promising get extended; the rest drop out before the bill grows.
- **Hard-sample dashboard.** Samples that everyone aces or everyone fails carry no signal. The dashboard surfaces samples that actually separate variants, and the loop preferentially scores on those.

## Pages

| Page | Covers |
|------|--------|
| [The three-layer loop](the-loop.md) | L1 / L2 / L3 cadence, how they communicate, and L2 in detail |
| [Self-healing](self-healing.md) | Four LLM-to-LLM healing loops |
| [State record](state-record.md) | The OSP carrying CONTEXT, PLAN, prompt fields, and L2 overrides |
| [Scoring and memory](scoring-and-memory.md) | Traces are facts; scores are policy; the measurement archive |
| [Campaign tree](campaign-tree.md) | Cycles, forks, and the sweep primitive |
| [Nodes and pipelines](nodes-and-pipelines.md) | Backend pipeline node anatomy |
| [Glossary](glossary.md) | Terms used across the docs |
| [Mid-round elimination (PoBB)](../methods/candidate-elimination.md) | "Search-only-with-evidence" in detail |
| [Hard-sample dashboard](../methods/exploration-exploitation.md) | Sample selection in detail |

Implementation: [`../developer/`](../developer/README.md).
