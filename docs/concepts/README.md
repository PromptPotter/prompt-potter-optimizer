# Concepts

How PromptPotter works, explained without reference to Python classes, module paths, or internal schemas. Each page stands alone — read in any order.

## The full algorithm

PromptPotter evolves prompts and pipeline configs against a labelled dataset. The seven pages below cover the full algorithm.

| Page | What it covers |
|------|----------------|
| [Campaign lifecycle](campaign-lifecycle.md) | Outer shell: `init` → baseline → rounds → finalize |
| [The three-layer loop](three-layer-loop.md) | L1 generate-measure-critique, L2 refine on stall, L3 replan on deeper stall |
| [Mid-round elimination race (PoBB)](../methods/candidate-elimination.md) | Bayesian early-stop: a candidate is cut mid-round when its posterior probability of being the round's best drops below ε |
| [Sample selection + hard-sample leaderboard](../methods/exploration-exploitation.md) | Rasch + Knowledge Gradient — between rounds, swap understood samples for high-info ones; same posterior feeds the hard-sample leaderboard |
| [Self-healing](self-healing.md) | The four LLM-to-LLM healing loops (validation, runtime, L2-stall, L2-output validators) |
| [Scoring and traces](scoring-and-traces.md) | Traces are facts; scores are policy. Rescore-on-load, decision replay, fork |
| [The measurement archive](measurement-archive.md) | The cross-cycle / cross-session / cross-tenant database core: one row = (sample × config → outcome) |

## Strategist details (operator track for L2)

| Page | What it covers |
|------|----------------|
| [What L2 is](what-is-l2.md) | The optimizer's strategist — what it watches, what it mutates, why |
| [L2's decision tree](l2-decision-tree.md) | When L2 picks each of its five choices, with one scenario per variant |
| [L1's prompt surface](l1-generate-surface.md) | The closed catalogue of variables L1 sees, and L2's three levers over it |
| [The individual record](optsearchpoint-as-state.md) | The `OptSearchPoint` record explained — what each layer reads and writes |

## Supporting machinery

| Page | What it covers |
|------|----------------|
| [The fork tree and sweep primitive](fork-tree-and-sweep.md) | A campaign is a tree of cycles. What rides the tree, what doesn't. How sweep mints siblings |
| [Axis index](axis-index.md) | How knowledge accumulates across campaigns; parameter impact, query patterns, failure modes |
| [Prompts and individuals](prompts-and-individuals.md) | The 8-field prompt decomposition |
| [Nodes and pipelines](nodes-and-pipelines.md) | What a pipeline node is and what it can do |
| [Glossary](glossary.md) | Terms used across the docs |

Looking for implementation details? See [`../developer/`](../developer/README.md).
