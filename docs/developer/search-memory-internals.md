# Search Memory Internals

> **Phase 2 plan — rename to `AxisIndex`, rebuild on `MeasurementArchive`.** `SearchMemory` is the LLM-digest layer over the measurement archive. It maintains `_axis_values` and `_axis_failure_group_deltas` via its own ingestion path (`refresh()` → `_ingest_run()`); both are derivable from `measurements_for_config(...)` queries against the archive. Phase 2 will rename this class to `AxisIndex` and rebuild the digests as views over the archive rather than a parallel store. The `digest_for_*` surface stays — only the storage layer underneath collapses. See [`../concepts/measurement-archive.md`](../concepts/measurement-archive.md) for the database-core framing this rebuild aligns to.

`SearchMemory` is a materialized view over all historical evaluation data, persisted at `{tenant_id}/library/search_memory.json`. Conceptual overview in [../concepts/search-memory.md](../concepts/search-memory.md); this file covers the accessor catalog, digest API, and refresh mechanics.

Each consumer (L1 generate, L1 critique, L2, L3) calls a typed `digest_for_*` method and receives a summary — never raw records. The view is incremental: each refresh folds in only the new measurement-archive batches since the last watermark, so refresh cost is proportional to new evaluations, not total history.

---

## Data model

`SearchMemory` ingests every completed dataset run and maintains three analysis pillars: Parameter Impact, Query Patterns, Failure Modes.

### Parameter Impact

**Accessors:**
- `axis_rankings()` → all axes ranked by effect size (mean pairwise `|delta|` across value means)
- `top_k_values(axis)` → best-performing values for an axis

Classification: axes with ≥ 70% of pairwise deltas above noise threshold (0.02) are "consistently impactful"; ≥ 30% are "sometimes impactful"; below that, "dead".

### Query Patterns

Per-query hit/miss tracking across all evaluations. Every query accumulates a Bernoulli sequence of hits across configs.

**Accessors:**
- `dead_queries(min_observations=N, include_always_hit=..., include_always_miss=...)` → zero-signal queries (always-hit and/or always-miss) with a minimum-observations confidence gate. Powers the zero-signal sample filter.
- `query_tractability()` → all queries with hit rate and variance.
- `query_degradation_rate(query)` / `query_degradation_count(query)` → degradation stats consumed per-sample by the stale-data protocol.

Digest-internal: `SampleIndex.discriminating(min_variance)`, `SampleIndex.persistent_failures(min_streak)` — composed by the per-layer `digest_for_*` methods; not part of `SearchMemory`'s direct accessor surface.

### Failure Modes

Per-query failure tracking: which pipeline step terminated processing (`terminated_at`), and how failures cluster.

**Accessors:**
- `bottleneck_distribution()` → `{step: fraction_of_failures}`
- `failure_clusters(n)` → queries grouped by dominant failure mode, with counts and example queries

---

## Digest API

All consumers call one method with a chosen subset of intelligence keys. The cross-consumer matrix:

| Consumer | Intelligence received |
|----------|-----------------------|
| **L1 — generate phase** | Failure clusters, dead queries, top parameter axes + best values |
| **L1 — critique phase** | Discriminating queries, failure clusters, tractability profiles, exhausted axes, value trends, improvement attribution |
| **L2** | Axis rankings, bottleneck distribution, failure group × axis correlations, persistent failures, volatile queries |
| **L3** | Axis rankings, bottleneck distribution, failure clusters, persistent failures |

Each consumer calls its own typed method: `digest_for_l1_generate()`, `digest_for_l1_critique()`, `digest_for_l2()`, `digest_for_l3()`. Each method composes a fixed set of keys for that layer and shares the underlying private accessors (no parameterized "keys" argument). The L2 method always includes correlations; the L3 method always includes top-3 failure clusters with counts.

The per-layer prompt-injection mapping lives in [information-flow.md § L1 / L2 inbox](information-flow.md). This section documents the composition: which public and private accessors each key aggregates.

| Key (frozenset member) | Public accessors | Private accessors | Consumed by |
|------------------------|------------------|-------------------|-------------|
| `failure_clusters` | `failure_clusters(3)` | — | L1 generate, L1 critique (no counts), L3 via `include_clusters=True` |
| `dead_queries` | `dead_queries(include_always_hit=False)` | — | L1 generate |
| `top_axes`, `top_values` | `axis_rankings()[:3]`, `top_k_values(...)` | — | L1 generate |
| `discriminating_queries` | — | `discriminating()` | L1 critique |
| `tractability` | — | `persistent_failures(3)` | L1 critique |
| `exhausted_axes` | — | `_exhausted_axes()` | L1 critique |
| `value_trends` | — | `_axis_value_trend()` | L1 critique |
| `improvement_attribution` | — | `_format_recent_attributions()` | L1 critique |
| `axis_rankings` | `axis_rankings()[:5]` | — | L2, L3 |
| `bottleneck_distribution` | `bottleneck_distribution()` | — | L2, L3 |
| `persistent_failures` | — | `persistent_failures(3)` | L2, L3 |
| `failure_group_insights` | — | `_axis_failure_group_deltas` (populated by `_recompute_failure_group_correlations()`) | L2 |
| `volatile_queries` | — | `flips(limit=50)` | L2 |

Callers (selected):
- L1 generate — `digest_for_l1_generate()` → `failure_clusters` (top-2 with counts), `dead_queries`, `top_axes`, `top_values`
- L1 critique — `digest_for_l1_critique()` → `discriminating_queries`, `failure_clusters` (top-2 no counts), `tractability`, `exhausted_axes`, `value_trends`, `improvement_attribution`
- L2 — `digest_for_l2()` → `axis_rankings`, `bottleneck_distribution`, `failure_group_insights`, `persistent_failures`, `volatile_queries`
- L3 — `digest_for_l3()` → `failure_clusters` (top-3 with counts), `axis_rankings`, `bottleneck_distribution`, `persistent_failures`

The result is a `dict[str, str]` rendered through `format_search_memory_block()` for a consistent "HISTORICAL INTELLIGENCE" block shape. The L1 critique phase additionally receives `round_history` — per-round accuracy / composite / pipeline_params / degraded-count dicts built inline during round execution.

---

## Materialized view mechanics

### Watermark + incremental update

Each pillar tracks a watermark — the set of `run_id`s already folded into its statistics. On refresh, `SearchMemory` compares current run IDs against the watermark, loads only the **new** measurement batches' per-query data, and updates the rolling statistics in place. Persisted view + new watermark are written back atomically. No full recomputation, no re-reading the entire `library/measurements/` archive — the cost of one refresh is proportional to new evaluations since the last refresh, not to total history.

### Design constraint

`SearchMemory` exposes granular data accessors returning structured data (`AxisImpact`, `ValueRecord`, `QueryRecord`, `FailureCluster`). It never produces LLM-ready text directly — instead, four typed digest methods (`digest_for_l1_generate`, `digest_for_l1_critique`, `digest_for_l2`, `digest_for_l3`) compose a fixed key set per consumer. Module-private helpers (`_exhausted_axes`, `_axis_value_trend`, `_format_recent_attributions`, `_recompute_failure_group_correlations`) are used only by digests; consumers call the per-layer digest method and a handful of public aggregate accessors (`axis_rankings`, `top_k_values`, `failure_clusters`, `bottleneck_distribution`, `query_tractability`, `dead_queries`, `query_degradation_*`, `record_flips_from_rounds`). This keeps `SearchMemory` from growing into a god object that knows about every prompt template.

### Per-refresh cache

`_build_query_records()`, `_compute_axis_impact()`, and `failure_clusters()` are pure functions of the ingested state — they change only when `refresh()` folds in new dataset runs. Each is memoized on the instance and the three caches (`_cache_query_records`, `_cache_axis_impacts`, `_cache_failure_clusters`) are cleared at the end of `refresh()` when `added > 0`. One digest round previously recomputed these derived views 3–4× each; caching collapses that to one computation per refresh cycle.
