# Search Memory

Search memory is PromptPotter's cross-campaign intelligence layer. Every evaluation the backend ever runs gets stored; search memory is the materialized view over that archive. When an optimization thread stops improving, the data isn't wasted — on a later run, the optimizer discovers all stored evaluations, knows the landscape better, and fresh optimization starts from higher ground.

The data is shared across campaigns. A campaign running on a new dataset sees which queries have always been easy across all prior runs. A campaign with a new prompt sees which parameter axes have consistently moved the needle. The memory is independent of both the optimization loop and any single campaign's configuration.

---

## Three pillars

Search memory tracks three things, each answering a different question.

### Parameter impact — "which axes matter?"

For every parameter axis the optimizer has varied — thinking style, temperature, max tokens, threshold — search memory tracks the effect size of changing that axis's value. An axis with consistently large effects is worth spending search budget on. An axis whose values all produce the same score has become dead — the optimizer can stop exploring it.

Axes are classified: *consistently impactful* (big effects most of the time), *sometimes impactful* (big effects sometimes, small others), and *dead* (no meaningful effect). This classification drives which axes L1 prioritizes in future rounds.

### Query patterns — "which queries are informative?"

Every query accumulates a record of hits and misses across all configurations tried. Some queries are always easy — they hit under every configuration, so changing the configuration doesn't tell us anything. Others are always hard — they miss under every configuration, for the same reason. Still others are *discriminating*: some configurations hit them, others miss them. Discriminating queries are the ones that teach the optimizer something; the others are noise.

The zero-signal filter uses this: queries that always hit or always miss across enough observations are physically moved out of the active dataset. The exploration/exploitation sample selector (the "adaptive prefix") uses it too, in a gentler form — queries whose difficulty is already well characterized are swapped out of the scoring slice in favor of queries whose difficulty is still uncertain and informative to measure.

### Failure modes — "where does the pipeline break?"

Every failed query carries information about where in the pipeline processing terminated. Was it the retrieval step? The ranking step? The LLM? Failure modes cluster these terminations — finding that 40% of misses fail at the web search step is the kind of strategic signal L3 needs to decide *"swap the search provider"* rather than *"tweak a threshold."*

---

## How each optimizer layer uses it

Search memory publishes a different digest to each consumer. They ask for what they need — the digest doesn't push everything at everyone.

| Consumer | What it sees |
|----------|--------------|
| L1 Generate | Failure clusters, dead queries, top parameter axes, best values |
| L1 Critique | Discriminating queries, failure clusters, tractability profiles, exhausted axes, value trends |
| L2 Refine | Axis rankings, bottleneck distribution, failure-group × axis correlations, persistent failures, volatile queries |
| L3 Plan | Axis rankings, bottleneck distribution, failure clusters, persistent failures |

L1 sees the fine-grained data it needs to propose sensible candidates. L2 sees the strategic picture — is the problem that we keep failing on the same axis? L3 sees the long-view — where in the pipeline does everything bottleneck?

---

## Why it's not just a cache

A cache remembers results. Search memory remembers *patterns* — the statistical shape of the search space, extracted from those results. When a new campaign starts, it doesn't just skip queries it's seen before (that's the evaluation cache's job); it starts with a learned prior about which axes to explore, which queries are informative, and where failures cluster.

This is what makes the system *compound*. Every run leaves more structure in the memory for the next run to exploit. A team running ten campaigns this year starts each one with more knowledge than the last.

For the internal mechanics — refresh watermark, digest API, accessor catalog — see [../developer/search-memory-internals.md](../developer/search-memory-internals.md).
