# SearchMemory — Intelligence Feed

SearchMemory is a materialized view over all historical evaluation data, persisted at `{tenant_id}/library/search_memory.json`. It is refreshed incrementally before each optimization round and provides read-only intelligence to L1 generate, L2 refine, and the L1 critique agent.

This document describes what data is collected, how it flows into LLM prompts, and where the gaps are.

**The key insight: every evaluation is saved.** When an optimization thread stops improving, its data isn't wasted — on a later run, the optimizer discovers all stored evaluations, knows the landscape better, and a fresh optimization starts from higher ground. This shared memory is independent of both the optimization loop and the scan.

Each consumer (L1, L1 Critique, L2, L3) asks typed questions via `digest()` and receives a summary — never raw records. SearchMemory's job is to convert a growing archive of dataset runs into actionable intelligence that fits in a prompt.

The materialized view is incremental: each refresh folds in only the new dataset runs since the last watermark, then persists the updated view. The cost of one refresh is proportional to new evaluations since the last refresh, not to total history.

---

## 1. Data Model

SearchMemory ingests every completed dataset run and maintains three analysis pillars: Parameter Impact, Query Patterns, Failure modes

### Parameter Impact

**Accessors:**
- `axis_rankings()` → all axes ranked by effect size (mean pairwise
  |delta| across value means)
- `top_k_values(axis)` → best-performing values for an axis

Classification: axes with ≥ 70% of pairwise deltas above noise threshold (0.02) are "consistently impactful"; ≥ 30% are "sometimes impactful"; below that, "dead".

### Query Patterns

Per-query hit/miss tracking across all evaluations. Every query
accumulates a Bernoulli sequence of hits across configs.

**Accessors:**
- `dead_queries(min_observations=N, include_always_hit=..., include_always_miss=...)` → zero-signal queries (always-hit and/or always-miss) with a minimum-observations confidence gate. Powers the zero-signal sample filter (§ Zero-Signal Sample Filtering).
- `query_tractability()` → all queries with hit rate and variance.
- `query_degradation_rate(query)` / `query_degradation_count(query)` → degradation stats consumed per-sample by the stale-data protocol.

Digest-internal (private): `_discriminating_queries(min_variance)`, `_persistent_failures(min_streak)` — composed by `digest()`; not part of the external contract.

### Failure Modes

Per-query failure tracking: which pipeline step terminated processing
(`terminated_at`), and how failures cluster.

**Accessors:**
- `bottleneck_distribution()` → {step: fraction_of_failures}
- `failure_clusters(n)` → queries grouped by dominant failure mode, with
  counts and example queries

## 2. What Information Can Be asked from SearchMemory

All consumers call one method with a chosen subset of intelligence keys. The cross-consumer matrix:

| Consumer | Intelligence received |
|----------|-----------------------|
| **L1 — generate phase** | Failure clusters, dead queries, top parameter axes + best values |
| **L1 — critique phase** | Discriminating queries, failure clusters, tractability profiles, exhausted axes, value trends, improvement attribution |
| **L2** | Axis rankings, bottleneck distribution, failure group × axis correlations, persistent failures, volatile queries |
| **L3** | Axis rankings, bottleneck distribution, failure clusters, persistent failures |


All consumers call one method: `SearchMemory.digest(keys, *, include_correlations=False, include_clusters=False)`. The caller passes a `frozenset` of keys — each layer owns its own key selector. The same computation backs every consumer; only the key subset (and the two boolean flags) differ.

The cross-consumer matrix — which key each LLM node asks for — lives in
[`information-flow.md § L1 / L2 inbox`](information-flow.md#l1-l2-inbox-from-registry).
This section documents the composition: which public and private
accessors each key aggregates.

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
| `failure_group_insights` | — | `_parameter_failure_correlation()` (via `include_correlations=True`) | L2 |
| `volatile_queries` | — | `flips(limit=50)` (via `include_correlations=True`) | L2 |

Callers (selected):
- L1 generate — `digest(frozenset({"failure_clusters", "dead_queries", "top_axes", "top_values"}))`
- L1 critique — `digest(frozenset({"discriminating_queries", "failure_clusters", "tractability", "exhausted_axes", "value_trends", "improvement_attribution"}))`
- L2 — `digest(frozenset({"axis_rankings", "bottleneck_distribution", "failure_group_insights", "persistent_failures", "volatile_queries"}), include_correlations=True)`
- L3 — `digest(frozenset({"axis_rankings", "bottleneck_distribution", "failure_clusters", "persistent_failures"}), include_clusters=True)`

The result is a `dict[str, str]` rendered through `format_search_memory_block()` for a
consistent "HISTORICAL INTELLIGENCE" block shape.[^blockfmt] The
L1 critique phase additionally receives `round_history` — per-round
accuracy / composite / pipeline_params / degraded-count dicts built
inline in `round_execution.py`.[^crit]

[^blockfmt]: `promptpotter/application/optimization/nodes/formatting.py:format_search_memory_block()`.
[^crit]: `promptpotter/application/optimization/nodes/l1_critique_payload.py` — the critique phase `round_history` payload is built from `state.rounds`; the same field name on `opt_sp.memory.round_history` (persisted summaries) is a distinct, narrower record.

## 3. Materialized View Mechanics

### Watermark + Incremental Update

Each pillar tracks a watermark — the set of `dataset_run` IDs already
folded into its statistics. On refresh, SearchMemory compares current
run IDs against the watermark, loads only the **new** runs' per-query
data, and updates the rolling statistics in place. Persisted view +
new watermark are written back atomically. No full recomputation, no
re-reading the entire `dataset_runs/` archive — the cost of one
refresh is proportional to new evaluations since the last refresh,
not to total history.

### Design Constraint

SearchMemory exposes **granular data accessors** returning structured
data (`AxisImpact`, `ValueRecord`, `QueryRecord`, `FailureCluster`).
It never produces LLM-ready text. A single digest method —
`digest(keys, *, include_correlations=False, include_clusters=False)` —
composes a caller-chosen subset of keys for each consumer. Accessors
used only by a digest (`_discriminating_queries`, `_persistent_failures`,
`_exhausted_axes`, `_axis_value_trend`,
`_parameter_failure_correlation`, `_query_flip_history`,
`_format_recent_attributions`, `_record_query_flips`,
`_recompute_failure_group_correlations`) are module-private — consumers
call only `digest()` and a handful of public aggregate accessors
(`axis_rankings`, `top_k_values`, `failure_clusters`,
`bottleneck_distribution`, `query_tractability`, `dead_queries`,
`query_degradation_*`). This keeps SearchMemory from growing into a
god object that knows about every prompt template.

### Per-Refresh Cache

`_build_query_records()`, `_compute_axis_impact()`, and
`failure_clusters()` are pure functions of the ingested state — they
change only when `refresh()` folds in new dataset runs. Each is
memoized on the instance and the three caches
(`_cache_query_records`, `_cache_axis_impacts`,
`_cache_failure_clusters`) are cleared at the end of `refresh()` when
`added > 0`. One digest round previously recomputed these derived views
3–4× each; caching collapses that to one computation per refresh cycle.
