# Concepts

How PromptPotter works, explained without reference to Python classes, module paths, or internal schemas. Each page stands alone — read in any order.

| Page | What it covers |
|------|----------------|
| [Campaign lifecycle](campaign-lifecycle.md) | Narrative walkthrough from `init` to finish |
| [The three-layer loop](three-layer-loop.md) | L1 generate-evaluate-critique, L2 refine, L3 replan — why each exists |
| [Self-healing](self-healing.md) | The two rails (validation failures vs. degraded runs) and how the optimizer recovers |
| [Scoring and traces](scoring-and-traces.md) | Traces are facts, scores are policy. Rescore-on-load, decision replay, fork. |
| [Axis index](axis-index.md) | How knowledge accumulates across campaigns; parameter impact, query patterns, failure modes |
| [Prompts and individuals](prompts-and-individuals.md) | The 8-field prompt decomposition |
| [Nodes and pipelines](nodes-and-pipelines.md) | What a pipeline node is and what it can do |
| [Glossary](glossary.md) | Terms used across the docs |

Looking for implementation details? See [`../developer/`](../developer/README.md).
