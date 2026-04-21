# Methods: SearchMemory as Intelligence Feed

SearchMemory is a materialized view over all historical evaluation data,
persisted at `{tenant_id}/library/search_memory.json`. It is refreshed
incrementally before each optimization round and provides read-only
intelligence to L1 generate, L2 refine, and the critique agent.[^impl]

This document describes what data is collected, how it flows into LLM
prompts, and where the gaps are.

**The key insight: every evaluation is saved.** When an optimization
thread stops improving, its data isn't wasted — on a later run, the
optimizer (or the optional scan) discovers all stored evaluations,
knows the landscape better, and a fresh optimization starts from
higher ground. This shared memory lives in the `intelligence/` package
and is independent of both the optimization loop and the scan.

[^impl]: `promptpotter/application/intelligence/search_memory.py`.

## 1. Data Model

SearchMemory ingests every completed dataset run and maintains three
analysis pillars:

### Parameter Impact

Per-axis (node.param) value → accuracy tracking. For each pipeline
parameter axis, records which concrete values were observed and their
associated accuracies across all historical runs.[^ingest]

**Accessors:**
- `axis_rankings()` → all axes ranked by effect size (mean pairwise
  |delta| across value means)
- `top_k_values(axis)` → best-performing values for an axis

Classification: axes with >= 70% of pairwise deltas above noise threshold
(0.02) are "consistently_impactful"; >= 30% are "sometimes_impactful";
below that, "dead".

[^ingest]: `SearchMemory._ingest_run()` — processes pipeline_params from
each dataset run detail.

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

## 2. Composition of the Unified `digest()` Method

All consumers call one method: `SearchMemory.digest(keys, *, include_correlations=False, include_clusters=False)`. The caller passes a `frozenset` of keys — each layer owns its own key selector. The same computation backs every consumer; only the key subset (and the two boolean flags) differ.

The cross-consumer matrix — which key each LLM node asks for — lives in
[`information-flow.md § L1 / L2 inbox`](information-flow.md#l1-l2-inbox-from-registry).
This section documents the composition: which public and private
accessors each key aggregates.

| Key (frozenset member) | Public accessors | Private accessors | Consumed by |
|------------------------|------------------|-------------------|-------------|
| `failure_clusters` | `failure_clusters(3)` | — | L1, Critique (no counts), L3 via `include_clusters=True` |
| `dead_queries` | `dead_queries(include_always_hit=False)` | — | L1 |
| `top_axes`, `top_values` | `axis_rankings()[:3]`, `top_k_values(...)` | — | L1 |
| `discriminating_queries` | — | `discriminating()` | Critique |
| `tractability` | — | `persistent_failures(3)` | Critique |
| `exhausted_axes` | — | `_exhausted_axes()` | Critique |
| `value_trends` | — | `_axis_value_trend()` | Critique |
| `improvement_attribution` | — | `_format_recent_attributions()` | Critique |
| `axis_rankings` | `axis_rankings()[:5]` | — | L2, L3 |
| `bottleneck_distribution` | `bottleneck_distribution()` | — | L2, L3 |
| `persistent_failures` | — | `persistent_failures(3)` | L2, L3 |
| `failure_group_insights` | — | `_parameter_failure_correlation()` (via `include_correlations=True`) | L2 |
| `volatile_queries` | — | `flips(limit=50)` (via `include_correlations=True`) | L2 |

Callers (selected):
- L1 — `digest(frozenset({"failure_clusters", "dead_queries", "top_axes", "top_values"}))`
- Critique — `digest(frozenset({"discriminating_queries", "failure_clusters", "tractability", "exhausted_axes", "value_trends", "improvement_attribution"}))`
- L2 — `digest(frozenset({"axis_rankings", "bottleneck_distribution", "failure_group_insights", "persistent_failures", "volatile_queries"}), include_correlations=True)`
- L3 — `digest(frozenset({"axis_rankings", "bottleneck_distribution", "failure_clusters", "persistent_failures"}), include_clusters=True)`

The result is a `dict[str, str]` rendered through `format_search_memory_block()` for a
consistent "HISTORICAL INTELLIGENCE" block shape.[^blockfmt] The
critique agent additionally receives `round_history` — per-round
accuracy / composite / pipeline_params / degraded-count dicts built
inline in `round_execution.py`.[^crit]

[^blockfmt]: `promptpotter/application/optimization/nodes/formatting.py:format_search_memory_block()`.
[^crit]: `promptpotter/application/optimization/nodes/round_execution.py` — builds `round_history` from `state.rounds`.

## 3. Three Tiers of Intelligence

L1 focuses on generating diverse candidates. Everything else is one of three tiers, each with a distinct owner, trigger, and signal type. This is the single framing used throughout the rest of this doc and the rest of the codebase.

| Tier | Handled by | Fires when | What | Example |
|------|-----------|------------|------|---------|
| **Tier 1 — Deterministic** | Code (statistics) | Every round | Per-query triage without LLM reasoning | Zero-signal sample filtering (§ 5) |
| **Tier 2 — Every-round critique hub** | Critique (LLM) | Every round | Frame this-round analysis with historical context | Tractability profiles, axis exhaustion, value trends |
| **Tier 3 — Strategic** | L2 Refine + L3 Plan (LLM) | Escalation only | Meta-reasoning about why optimization is stuck | Round trajectory, candidate comparison, failure group × axis |

L1 continues to receive: critique text, scan context, failure analysis
patterns, and SearchMemory summaries (failure clusters, top axes, dead
queries). These are sufficient for candidate generation. L3 receives
the aggregate SearchMemory picture (axis rankings, bottleneck
distribution, failure clusters, persistent failures) for strategic
plan pivots.

## 4. Materialized View Mechanics

### Watermark + Incremental Update

Each pillar tracks a watermark — the set of `dataset_run` IDs already
folded into its statistics. On refresh, SearchMemory compares current
run IDs against the watermark, loads only the **new** runs' per-query
data, and updates the rolling statistics in place. Persisted view +
new watermark are written back atomically. No full recomputation, no
re-reading the entire `dataset_runs/` archive — the cost of one
refresh is proportional to new evaluations since the last refresh,
not to total history.

### Atomic API (Design Constraint)

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

## 5. How the Tiers Operate on Sample Data

### Tier 1 in action — Deterministic Sample Triage (Code)

Per-query failure streak detection via `_query_hits` Bernoulli sequences. Three severity levels:

- **Zero-signal** (always-hit or always-miss with ≥ `min_observations` samples) → `dead_queries(min_observations=N)` drives the zero-signal sample filter (see next section). Physically removed from the active dataset.
- **Chronically failing** (recent streak) → `_persistent_failures(min_streak)` surfaces these via the `tractability` / `persistent_failures` keys of `digest()`, flagging them to Critique and L2/L3.
- **Intermittent** (variable) → kept in eval set. High discrimination value.

### Zero-Signal Sample Filtering

`application/scoring/zero_signal_filter.py::apply_zero_signal_exclusions()` sweeps the active dataset at round boundaries. Called from `campaign/runner.py::_post_round` right after `SearchMemory.on_round_complete()`, gated on `CampaignConfig.optimization.zero_signal_filter_enabled` (**on by default** — the `min_observations=5` gate prevents premature exclusion on a fresh campaign).

**Criteria.** A query is zero-signal when its Bernoulli hit sequence satisfies *both*:
1. `len(hits) >= min_observations` (default 5) — confidence gate so a single observation doesn't exclude the world on a fresh campaign.
2. `hit_rate ∈ {0.0, 1.0}` — variance exactly 0. Treated symmetrically; always-hit and always-miss are both excluded.

**Persistence — "exchange from the default set".** Excluded queries are **physically moved** inside `{tenant_id}/library/backends/{backend_id}/datasets/{name}.json` from `items` into a new `excluded` sidelist via `BackendStore.exclude_dataset_items()`. The sidelist entry captures the full original item plus `{reason, hit_rate, observations, campaign_id, excluded_at}`. A fresh campaign launched tomorrow sees the shrunken `items` list — not transient state, actually pruned. Recovery is `BackendStore.restore_dataset_items(queries=None)` which moves entries back.

```json
{
  "name": "train",
  "items": [...],
  "excluded": [
    {
      "item": {"query": "...", "ground_truth": "..."},
      "reason": "zero_signal",
      "hit_rate": 0.0,
      "observations": 7,
      "campaign_id": "cyc_...",
      "excluded_at": "2026-04-14T..."
    }
  ]
}
```

**In-round effect.** `apply_zero_signal_exclusions()` also mutates the in-memory `dataset` list passed into `_run_round_loop`, and prunes `env.scoring_dataset` — so the *current* run's next round immediately sees the smaller set, not just future runs.

**User surfacing.** When the filter fires, `emit_phase("zero_signal_filter", "applied", count=N, always_miss=..., always_hit=..., examples=[...])` is dispatched via `RunListener.on_phase`. Notebook/CLI presenters render the message.

**Config.** Two `CampaignConfig.optimization` fields:
- `zero_signal_filter_enabled: bool = True` — master switch (on by default).
- `zero_signal_filter_min_observations: int = 5` — confidence gate.

**Known limitations (to be refined in M10).** This first implementation is deliberately rudimentary: excluded queries are dropped outright rather than tiered (no "probation" / "rotate back in" / "cold storage queryable by future critique"), and there is no guard preventing the active dataset from shrinking below `sp_budget_ttest`. See M10 spec.

**Why it's not a fallback.** The filter is *not* a "default value when the real one fails" — it's deterministic dataset shrinking driven entirely by observed data. Zero backend calls. No retry. No hidden recovery.

### Tier 3 in action — L2 Strategic Intelligence (LLM)

Meta-reasoning injected into L2 Refine only:

- **Round trajectory** — `build_round_trajectory()`: accuracy trend, stall count, direction. Built from `state.rounds`.
- **Failure group × axis** — `_parameter_failure_correlation()`: cross-tabulates failure clusters with per-axis deltas. Producer runs after scan via `failure_group_sensitivity()`.
- **Candidate comparison** — `build_candidate_comparison()`: how all candidates performed, preventing L2 from repeating tested approaches.

## 6. Status

All core items implemented: failure streak triage, round trajectory, failure group × axis, candidate comparison, critique tractability/exhaustion/trends, L3 intelligence.

## 7. Future Work

Items below have been sketched but not built. Listed here so the current-state tables above stay honest.

| Item | Tier | Target |
|------|------|--------|
| **Diminishing returns detector** | Both | Critique (anomaly flag) + L2 (strategic context) |
| **Candidate diversity monitor** | Strategic | L2 — detect mode collapse in candidate generation |
| **Query improvement attribution** | Both | Critique (this-round) + L2 (cross-round patterns) |
| **Cross-candidate failure diff** | Every-round | Critique — missed opportunities from non-winner candidates |
| **Failure group refresh in loop** | Strategic | L2 — periodic recomputation during optimization |
