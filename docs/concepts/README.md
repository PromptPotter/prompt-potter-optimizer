# Concepts

> **Audience:** Developer reference; operators see [`../manual/`](../manual/). How the loop works — one page per topic; code anchors in [`../developer/`](../developer/README.md).

## Pages

| Page | Covers |
|------|--------|
| [The three-layer loop](the-loop.md) | L1 / L2 / L3 layers, how they communicate |
| [Self-healing](../developer/self-healing-internals.md) | LLM-to-LLM wounds (producer → detector → nurse) |
| [State record](state-record.md) | The OSP carrying CONTEXT, PLAN, prompt fields, and L2 overrides |
| [Scoring and memory](scoring-and-memory.md) | Traces are facts; scores are policy; the measurement archive |
| [Campaign tree](campaign-tree.md) | Cycles, forks, and the sweep primitive |
| [Nodes and pipelines](nodes-and-pipelines.md) | Backend pipeline node anatomy |
| [Structured output](structured-output.md) | The schema is a second prompt — name, field order, `description`; shape-determinism ≠ content-determinism |
| [Optimizer of the optimizer](optimizer-of-the-optimizer.md) | PromptPotter optimizing its own optimizer prompts (M12) |
| [Glossary](../glossary.md) | Domain vocabulary |
| [Mid-round elimination (PoBB)](../methods/candidate-elimination.md) | "Search-only-with-evidence" in detail |
| [Paired-sample PoBB](paired-sample-pobb.md) | How sample-keyed priors + leader backfill restore PoBB's iid premise under hard-sample-first ordering |
| [Hard-sample leaderboard](../methods/exploration-exploitation.md) | Sample selection in detail |

Implementation: [`../developer/`](../developer/README.md).
