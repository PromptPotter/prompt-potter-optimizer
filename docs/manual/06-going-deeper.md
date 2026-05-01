# Going deeper

You've run a campaign. Here's where to look when you want to understand more.

## Concepts — how it works

The `concepts/` folder explains the ideas behind PromptPotter in plain language. No Python classes, no module paths.

- [Campaign lifecycle](../concepts/campaign-lifecycle.md) — the full narrative of what happens from init to finish
- [The three-layer loop](../concepts/three-layer-loop.md) — why L1, L2, and L3 exist and when each fires
- [Self-healing](../concepts/self-healing.md) — how the optimizer recovers from bad proposals and degraded runs
- [Scoring and traces](../concepts/scoring-and-traces.md) — why traces are facts and scores are policy; when to fork
- [Axis index](../concepts/axis-index.md) — how PromptPotter accumulates knowledge across campaigns
- [Prompts and individuals](../concepts/prompts-and-individuals.md) — the 8-field prompt decomposition
- [Nodes and pipelines](../concepts/nodes-and-pipelines.md) — what a pipeline node is and what it can do
- [Glossary](../concepts/glossary.md) — terms used across the docs

## Operations — running it in production

- [CLI reference](../operations/cli-reference.md) — every subcommand and flag
- [Backend integration](../operations/backend-integration.md) — the contract a backend must implement
- [Persistence and state](../operations/persistence-and-state.md) — the `.promptpotter/` tree, active session, resume
- [Rewind and fork](../operations/rewind-and-fork.md) — recovering from bad trajectories or scorer changes
- [Observability](../operations/observability.md) — Langfuse integration
- [Environment](../operations/environment.md) — env variables, optional extras, Docker

## Developer — implementation

- [Code layout](../developer/code-layout.md) — package structure, hexagonal layers
- [Information flow](../developer/information-flow.md) — what each optimizer layer reads and writes
- [Node standard](../developer/node-standard.md) — how to wire a new pipeline node
- [Code map](../developer/code-map.md) — Python symbol index, where everything lives

## Methods — the statistics

- [Candidate elimination](../methods/candidate-elimination.md) — Bayesian Posterior-of-Being-Best (PoBB): we estimate the probability that each candidate will win the round; once a candidate's win-probability falls below 5%, we stop measuring it
- [Exploration / exploitation sample selection](../methods/exploration-exploitation.md) — Rasch + Knowledge Gradient

## Research — benchmarks and the paper

- [Benchmarks](../research/benchmarks.md) — datasets, methodology, baselines
- [Metrics](../research/metrics.md) — beyond absolute accuracy (HC, SE, R₉₀)
- [Related work](../research/related-work.md) — competitor positioning, AutoML lineage
