# Going deeper

You've run a campaign. Each subtree below has its own index; open the one that
matches your question.

- [Concepts](../concepts/the-loop.md) — how the loop works: the three layers, scoring and memory, the campaign tree.
- [Operations](../operations/README.md) — running it: backends, the `.promptpotter/` tree, resume/rewind/fork, observability.
- [Developer](../developer/README.md) — implementation: prompt structure, the dispatch hub, self-healing, wiring a node.
- [Methods](../methods/README.md) — the statistics: PoBB elimination and the hard-sample leaderboard.
- [Research](../research/README.md) — benchmarks, metrics, and where PromptPotter sits among peers.

---

## Iterating on prompts manually

Hand-tuning `l1_generate` (or another optimizer prompt) means editing
`promptpotter/assets/optimizer/pipeline.yaml` directly — it is an operator-owned file that nothing
writes. To measure whether an edit helped, run the optimizer **on itself**:
`python -m promptpotter new promptpotter-self` (L4) scores optimizer prompt variants against a
cached origin on shared cells and reports a paired verdict.

Full design spec: [`../specs/roadmap.md`](../specs/roadmap.md).
