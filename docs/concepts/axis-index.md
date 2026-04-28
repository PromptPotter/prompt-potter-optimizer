# Axis Index

The axis index is a derived view over the [measurement archive](measurement-archive.md). It groups every measurement by parameter axis and surfaces the resulting digests to the optimizer at L1, L1-critique, L2, and L3. The archive is the database; the axis index is one of two derived projections — the other is `SampleIndex`, keyed by training example.

The axis index does not own data. Every refresh rebuilds the axis-side state from the archive index in memory; nothing is persisted. The peer `SampleIndex` is also a pure in-memory derivation — both digest layers sit downstream of the archive, neither owns disk state.

---

## What it answers

Each consumer pulls a different question from the same underlying state:

| Consumer | What it sees |
|----------|--------------|
| L1 Generate | Failure clusters, dead queries, top parameter axes, best values |
| L1 Critique | Discriminating queries, failure clusters, tractability profiles, exhausted axes, value trends |
| L2 Refine | Axis rankings, bottleneck distribution, failure-group × axis correlations, persistent failures, volatile queries |
| L3 Plan | Axis rankings, bottleneck distribution, failure clusters, persistent failures |

L1 sees the fine-grained data it needs to propose sensible candidates. L2 sees the strategic picture — is the problem that we keep failing on the same axis? L3 sees the long-view — where in the pipeline does everything bottleneck?

The digest methods are stable across the rebuild — `digest_for_l1_generate`, `digest_for_l1_critique`, `digest_for_l2`, `digest_for_l3` continue to be the LLM-context surface.

---

## Three pillars

The axis index tracks three things, each answering a different question.

### Parameter impact — "which axes matter?"

For every parameter axis the optimizer has varied — thinking style, temperature, max tokens, threshold — the axis index tracks the effect size of changing that axis's value. An axis with consistently large effects is worth spending search budget on. An axis whose values all produce the same score has become dead.

Axes are classified: *consistently impactful*, *sometimes impactful*, and *dead*. This drives which axes L1 prioritizes in future rounds.

### Query patterns — "which queries are informative?"

Every query accumulates a record of hits and misses across all configurations tried. Some queries are always easy; some are always hard; some are *discriminating* — some configurations hit them, others miss them. Discriminating queries teach the optimizer; the others are noise.

The zero-signal filter uses this: queries that always hit or always miss across enough observations are physically moved out of the active dataset. The scoring-set evolution swap uses it too, gentler: queries whose difficulty is already well characterized are swapped out of the active scoring set.

This pillar lives on `SampleIndex` (the per-sample derived view); the axis index composes it into its L1 / L2 / L3 digests.

### Failure modes — "where does the pipeline break?"

Every failed query carries information about where in the pipeline processing terminated. Failure modes cluster these terminations — finding that 40% of misses fail at the web search step is the strategic signal L3 needs to decide *"swap the search provider"* rather than *"tweak a threshold."*

---

## Relationship to the archive

```
MeasurementArchive   ← facts (append-only, persisted)
   │
   ├── SampleIndex   ← per-sample derived view (in-memory; rebuilt every refresh)
   └── AxisIndex     ← axis-keyed derived view (in-memory; rebuilt every refresh)
```

The archive is the source of truth. Both derived views read it; neither replaces it. When you ask "what was actually measured?" the archive answers. When you ask "across all measurements, which axes shifted fitness?" the axis index answers.

For the internal mechanics — refresh path, digest API, accessor catalog — see [../developer/axis-index-internals.md](../developer/axis-index-internals.md).
