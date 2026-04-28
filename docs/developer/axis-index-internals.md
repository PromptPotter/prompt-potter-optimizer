# Axis Index Internals

`AxisIndex` is the LLM-digest layer over the [measurement archive](../concepts/measurement-archive.md). It is a *pure derived view* — every refresh rebuilds the axis-side state from `MeasurementArchive.list_all()` in memory. There is no `axes.json`, no axis watermark; the only on-disk file in this layer is `library/samples.json`, owned by `SampleIndex` (sample side stays incremental + watermarked because it isn't a pure derivation).

Conceptual overview in [../concepts/axis-index.md](../concepts/axis-index.md); this file covers the accessor catalog, digest API, and refresh mechanics.

Each consumer (L1 generate, L1 critique, L2, L3) calls a typed `digest_for_*` method and receives a summary — never raw records. Digest names are stable across the Phase 2 rebuild.

---

## Data model

`AxisIndex` ingests every completed dataset run via the archive index and maintains three analysis pillars: Parameter Impact, Query Patterns, Failure Modes.

### Parameter Impact

**Accessors:**
- `axis_rankings()` → all axes ranked by effect size (mean pairwise `|delta|` across value means)

Classification: axes with ≥ 70% of pairwise deltas above noise threshold (0.02) are "consistently impactful"; ≥ 30% are "sometimes impactful"; below that, "dead".

### Query Patterns

Per-query hit/miss tracking across all evaluations. Lives on `SampleIndex`; `AxisIndex` composes it through `self.sample_index`.

Relevant `SampleIndex` accessors used by digests:
- `dead(min_observations=N, include_always_hit=..., include_always_miss=...)` — zero-signal queries with a minimum-observations confidence gate. Powers the zero-signal sample filter.
- `discriminating(min_variance)` — queries whose outcome varies across configs.
- `persistent_failures(min_streak)` — intractable + chronic failures.
- `degradation_count(sid)` / `degradation_rate(sid)` — degradation stats consumed by the stale-data protocol.

### Failure Modes

Per-query failure tracking: which pipeline step terminated processing (`terminated_at`), and how failures cluster. Lives on `SampleIndex`.

`SampleIndex` accessors:
- `bottleneck_distribution()` → `{step: fraction_of_failures}`
- `failure_clusters(n)` → queries grouped by dominant failure mode.

---

## Digest API

All consumers call one method with a chosen subset of digest keys. The cross-consumer matrix:

| Consumer | Keys received |
|----------|---------------|
| **L1 — generate phase** | Failure clusters, dead queries, top parameter axes + best values |
| **L1 — critique phase** | Discriminating queries, failure clusters, tractability profiles, exhausted axes, value trends, improvement attribution |
| **L2** | Axis rankings, bottleneck distribution, failure group × axis correlations, persistent failures, volatile queries |
| **L3** | Axis rankings, bottleneck distribution, failure clusters, persistent failures |

Each consumer calls its own typed method: `digest_for_l1_generate()`, `digest_for_l1_critique()`, `digest_for_l2()`, `digest_for_l3()`. Each method composes a fixed set of keys for that layer and shares the underlying private accessors (no parameterized "keys" argument).

The per-layer prompt-injection mapping lives in [information-flow.md § L1 / L2 dispatch_msg](information-flow.md). This section documents the composition: which public and private accessors each key aggregates.

| Key | Source | Consumed by |
|-----|--------|-------------|
| `failure_clusters` | `sample_index.failure_clusters(n)` | L1 generate, L1 critique, L3 |
| `dead_queries` | `sample_index.dead(include_always_hit=False)` | L1 generate |
| `top_axes`, `top_values` | `axis_rankings()[:3]` + `_compute_axis_impact(...).top_values` | L1 generate |
| `discriminating_queries` | `sample_index.discriminating()` | L1 critique |
| `tractability` | `sample_index.persistent_failures(3)` | L1 critique |
| `exhausted_axes` | `_exhausted_axes()` | L1 critique |
| `value_trends` | `_axis_value_trend()` | L1 critique |
| `improvement_attribution` | `_format_recent_attributions()` | L1 critique |
| `axis_rankings` | `axis_rankings()[:5]` | L2, L3 |
| `bottleneck_distribution` | `sample_index.bottleneck_distribution()` | L2, L3 |
| `persistent_failures` | `sample_index.persistent_failures(3)` | L2, L3 |
| `failure_group_insights` | `_axis_failure_group_deltas` (populated by `_recompute_failure_group_correlations()`) | L2 |
| `volatile_queries` | `sample_index.flips(limit=50)` | L2 |

The result is a `dict[str, str]` rendered through `format_axis_digest_block()`. The dispatch_msg registry passes the `"HISTORICAL CONTEXT:"` header at each L1/L2/L3 site; the renderer itself is header-agnostic.

---

## Refresh mechanics

`refresh(store, backend_id, scorer, scorer_id, scorer_formula) -> bool` does two things, in order:

1. **Sample side (incremental, watermarked).** Walk `archive.load_since(watermark)`; rescore items via `rescore_results`; ingest into `sample_index`; mark watermark. Returns `True` iff at least one new run was added.
2. **Axis side (full rebuild every call).** Allocate a fresh `_axis_values`, walk `archive.list_all()`, fold each entry's `(pipeline_params, scores.accuracy)` into the new dict, replace `_axis_values` atomically, clear `_cache_axis_impacts`, then call `_recompute_failure_group_correlations()` (always — no throttle).

The axis side rebuild is cheap because the archive index already carries `pipeline_params` and `scores.accuracy` per entry — no per-detail file load is needed.

Failure-group correlations are recomputed on every refresh. The previous every-5-rounds throttle is gone — at current scale, throttling adds staleness without saving meaningful work.

`on_round_complete(state, session, round_num)` calls `refresh(...)` and persists `samples.json`. No axis-side persistence.

`ensure_for(store, backend_id, ...)` loads `SampleIndex` from `library/samples.json`, builds a fresh `AxisIndex`, runs `refresh()` once, persists `samples.json` if anything changed, and returns the index. There is no axis-side load — the axis state comes entirely from the in-memory rebuild.

### Per-refresh cache

`_compute_axis_impact()` is memoized via `_cache_axis_impacts`, cleared at the end of each `refresh()`. One digest round previously recomputed axis impacts 3–4× across the L1-generate / L1-critique / L2 / L3 calls; caching collapses that to one computation per refresh cycle.
