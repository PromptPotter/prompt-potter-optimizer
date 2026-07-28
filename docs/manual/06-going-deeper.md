# Going deeper

You've run a campaign. Pointers below for the next layer.

## Concepts — how it works

| Page | Covers |
|------|--------|
| [Three-layer loop](../concepts/the-loop.md) | Why L1/L2/L3 exist and when each fires |
| [Self-healing](../developer/self-healing-internals.md) | Recovery from bad proposals + degraded runs |
| [Scoring and memory](../concepts/scoring-and-memory.md) | Traces are facts, scores are policy + the cross-campaign archive |
| [State record](../concepts/state-record.md) | The candidate's state record (`OptSearchPoint` in code) — carries task context, plan, prompt fields, L2 overrides |
| [Campaign tree](../concepts/campaign-tree.md) | Cycles, forks, and the sweep primitive |
| [Nodes and pipelines](../concepts/nodes-and-pipelines.md) | Pipeline node anatomy |
| [Glossary](../glossary.md) | Terms used across the docs |

## Operations

| Page | Covers |
|------|--------|
| [Backend integration](../operations/backend-integration.md) | The contract a backend must implement |
| [Persistence and state](../operations/persistence-and-state.md) | The `.promptpotter/` tree, `new` / `resume` flags, resume, rewind, fork, scoring steer |
| [Observability](../operations/observability.md) | Langfuse integration |

## Developer — implementation

| Page | Covers |
|------|--------|
| [Developer README](../developer/README.md) | Prompt structure, request routing, the scoring step, learning from prior campaigns + per-field reference tables |
| [L2 internals](../developer/l2-internals.md) | L2 firing, output, OSP mutations, layout edits |
| [Dispatch hub + L1 layout](../developer/dispatch-hub.md) | `INJECTIONS` registry, `L1Layout`, `DispatchHub` |
| [Self-healing internals](../developer/self-healing-internals.md) | Failure classification, escalation wiring |
| [Node standard](../developer/node-standard.md) | Wiring a new pipeline node |

## Methods — the statistics

- [Candidate elimination](../methods/candidate-elimination.md) — Bayesian Posterior-of-Being-Best (PoBB).
- [Exploration/exploitation sample selection](../methods/exploration-exploitation.md) — Rasch + Knowledge Gradient.

## Research — benchmarks and the paper

- [Benchmarks](../research/benchmarks.md) — datasets, methodology, origins.
- [Metrics](../research/metrics.md) — beyond absolute accuracy (HC, SE, R₉₀).
- [Related work](../research/related-work.md) — competitor positioning.

---

## Iterating on prompts manually

Hand-tuning `l1_generate` (or another optimizer meta-prompt) means editing
`datasets/_optimizer/pipeline.yaml` directly — it is an operator-owned file that nothing
writes. To measure whether an edit helped, run the optimizer **on itself**:
`python -m promptpotter new promptpotter-self` (L4) scores meta-prompt variants against a
cached origin on shared cells and reports a paired verdict.

A hand-driven strategist skill used to own this; L4's recursion replaced it, because a
loop that measures its own edits beats a checklist that proposes them.

Full design spec: [`../specs/roadmap.md`](../specs/roadmap.md).
