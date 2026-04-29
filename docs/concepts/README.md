# Concepts

How PromptPotter works, explained without reference to Python classes, module paths, or internal schemas. Each page stands alone — read in any order.

| Page | What it covers |
|------|----------------|
| [Campaign lifecycle](campaign-lifecycle.md) | Narrative walkthrough from `init` to finish |
| [The three-layer loop](three-layer-loop.md) | L1 generate-evaluate-critique, L2 refine, L3 replan — why each exists |
| [What L2 is](what-is-l2.md) | The optimizer's strategist — what it watches, what it mutates, why |
| [L2's decision tree](l2-decision-tree.md) | When L2 picks each of its five choices, with one scenario per variant |
| [L1's prompt surface](l1-generate-surface.md) | The closed catalogue of variables L1 sees, and L2's three levers over it |
| [The individual record](optsearchpoint-as-state.md) | The `OptSearchPoint` record explained — what each layer reads and writes |
| [Self-healing](self-healing.md) | The two rails (validation failures vs. degraded runs) and how the optimizer recovers |
| [Scoring and traces](scoring-and-traces.md) | Traces are facts, scores are policy. Rescore-on-load, decision replay, fork. |
| [Axis index](axis-index.md) | How knowledge accumulates across campaigns; parameter impact, query patterns, failure modes |
| [Prompts and individuals](prompts-and-individuals.md) | The 8-field prompt decomposition |
| [Nodes and pipelines](nodes-and-pipelines.md) | What a pipeline node is and what it can do |
| [Glossary](glossary.md) | Terms used across the docs |

**Understanding L2 (operator track):**
1. [what-is-l2.md](what-is-l2.md)
2. [l1-generate-surface.md](l1-generate-surface.md)
3. [l2-decision-tree.md](l2-decision-tree.md)
4. [optsearchpoint-as-state.md](optsearchpoint-as-state.md)

Looking for implementation details? See [`../developer/`](../developer/README.md).
