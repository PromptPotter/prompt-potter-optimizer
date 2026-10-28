# Going deeper

You've run a campaign. Here's where to look when you want to understand more.

## Concepts — how it works

- [The loop](../concepts/the-loop.md) — the round, the three layers, and when each fires
- [Scoring and memory](../concepts/scoring-and-memory.md) — how candidates are scored and what carries across campaigns
- [Structured output](../concepts/structured-output.md) — why a node's output schema is part of the search

## Operations — running it

- [Persistence and state](../operations/persistence-and-state.md) — the `.promptpotter/` tree, resume, rewind and fork
- [Backend integration](../operations/backend-integration.md) — the contract a backend must implement
- [Observability](../operations/observability.md) — tracing, Langfuse and MLflow
- [Access model](../operations/access-model.md) — accounts, spend ceilings and security when hosted

## Methods — the statistics

- [Verdict resolution](../methods/verdict-resolution.md) — the ability (θ) model and the hard-sample leaderboard
- [Candidate elimination](../methods/candidate-elimination.md) — when a candidate stops being scored

## Developer — implementation

- [Developer guide](../developer/README.md) — prompt structure, dispatch, the scoring node
- [Node standard](../developer/node-standard.md) — how to wire a new pipeline node

## Research — benchmarks and peers

- [Benchmarks](../research/benchmarks.md) — datasets, methodology, results
- [Related work](../research/related-work.md) — where PromptPotter sits among other optimizers
