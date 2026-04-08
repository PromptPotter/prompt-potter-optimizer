# Methods: SearchMemory as Intelligence Feed

SearchMemory is a materialized view over all historical evaluation data,
persisted at `{backend_id}/search_memory.json`. It is refreshed
incrementally before each optimization round and provides read-only
intelligence to L1 generate, L2 refine, and the critique agent.[^impl]

This document describes what data is collected, how it flows into LLM
prompts, and where the gaps are.

[^impl]: `services/search/search_memory.py`.

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
- `axis_impact(axis)` → effect size + consistency for one axis

Classification: axes with >= 70% of pairwise deltas above noise threshold
(0.02) are "consistently_impactful"; >= 30% are "sometimes_impactful";
below that, "dead".

[^ingest]: `SearchMemory._ingest_run()` — processes pipeline_params from
each dataset run detail.

### Query Patterns

Per-query hit/miss tracking across all evaluations. Every query
accumulates a Bernoulli sequence of hits across configs.

**Accessors:**
- `discriminating_queries(min_variance)` → queries whose outcome varies
  across configurations (high variance = informative for comparison)
- `dead_queries()` → queries that never hit (or always hit)
- `query_tractability()` → all queries with hit rate and variance
- `query_degradation_rate(query)` → fraction of evaluations with pipeline
  warnings

### Failure Modes

Per-query failure tracking: which pipeline step terminated processing
(`terminated_at`), and how failures cluster.

**Accessors:**
- `bottleneck_distribution()` → {step: fraction_of_failures}
- `failure_clusters(n)` → queries grouped by dominant failure mode, with
  counts and example queries

## 2. What Is Injected into LLM Prompts Today

### L1 Generate (meta-prompt)

Built by `build_l1_search_memory_context()`.[^l1fmt] Injected as a
"HISTORICAL INTELLIGENCE" section in the L1 meta-prompt via
`format_context_sections()`.

| Signal | Source accessor | Prompt text |
|--------|----------------|-------------|
| Failure patterns | `failure_clusters(3)` | "Common failure patterns: web_search (45%), token_matching (30%)" |
| Dead queries | `dead_queries()` | "Dead queries (never hit): 12 queries" |
| High-impact axes | `axis_rankings()[:3]` | "High-impact axes: web_search.max_sites (effect=0.082, consistently_impactful)" |
| Best values | `top_k_values(top_axis, 3)` | "Best-performing values: 7 (acc=72.0%), 5 (acc=68.0%)" |

[^l1fmt]: `services/campaign/formatting.py:build_l1_search_memory_context()`.

### L2 Refine (intelligence bundle)

Built by `build_l2_search_memory_context()`.[^l2fmt] Injected via
`format_l2_intelligence()`.

| Signal | Source accessor | Prompt text |
|--------|----------------|-------------|
| Axis rankings | `axis_rankings()[:5]` | "Axis impact rankings: ..." |
| Bottleneck distribution | `bottleneck_distribution()` | "Bottleneck distribution: web_search: 45%, token_matching: 30%" |

[^l2fmt]: `services/campaign/formatting.py:build_l2_search_memory_context()`.

### Critique Agent

Built inline in `round_execution.py:244-259`.[^crit] Passed as
`search_memory_context` on `CritiqueContext`.

| Signal | Source accessor | Prompt text |
|--------|----------------|-------------|
| Discriminating queries | `discriminating_queries()` | "12 queries vary across configs" |
| Failure clusters | `failure_clusters(3)` | "web_search (45%); token_matching (30%)" |

The critique agent also receives `round_history` — a list of
per-round dicts with accuracy, composite, pipeline_params, degraded
count, and candidate count.[^rh] This is the only round-over-round
trajectory data that reaches any LLM.

[^crit]: `services/campaign/round_execution.py`, lines 244–259.
[^rh]: `round_execution.py`, lines 265–274 — built from `state.rounds`.

## 3. Design Principle: L1 Stays Clean

L1 generate is a candidate generator — it should focus on producing
diverse, high-quality configurations, not reasoning about sample-level
diagnostics. Detailed failure analysis and strategic intelligence feed
into **L2 Refine** (the meta-controller) and into **deterministic code
triage** (no LLM needed).

**Two-tier architecture for sample intelligence:**

| Tier | Handled by | What | Example |
|------|-----------|------|---------|
| **Deterministic** | Code (statistics) | Sample triage that can be resolved without LLM reasoning | Exclude intractable queries (never hit across N configs), deprioritize chronically degraded samples |
| **Strategic** | L2 Refine (LLM) | Failure group insights that require meta-reasoning about optimization strategy | "45% of failures are web_search bottlenecks — adjust exploration toward max_sites and content_char_limit" |

L1 continues to receive: critique text, scan context, failure analysis
patterns, and SearchMemory summaries (failure clusters, top axes, dead
queries). These are sufficient for candidate generation. The deeper
per-query and failure-group intelligence goes to L2.

## 4. Foundation: Two-Tier Sample Intelligence

### Tier 1 — Deterministic Sample Triage (Code)

Problematic samples detected statistically, classified by severity,
handled without LLM involvement.

#### B: Per-query failure streak detection

**Current:** `_query_hits` stores full Bernoulli sequence per query.
`dead_queries()` returns never-hit queries.
**Missing:** A `persistent_failures(min_streak)` accessor that returns
queries failing in N consecutive recent evaluations across different
configs. These are classified into:

- **Intractable** (failed with every config ever tried) → exclude from
  eval set automatically. No signal for the optimizer.
- **Chronically failing** (failed in last N rounds but not always) →
  deprioritize in eval sampling. Flag to L2 for strategic review.
- **Intermittent** (variable hit/miss) → keep in eval set. High
  discrimination value for candidate comparison.

**Building blocks:** `_query_hits[query]` has the raw data.
`_query_failure_modes[query]` has the `terminated_at` sequence.

#### Eval set refinement

The existing `adapt_eval_set()` (Wave 3d) already swaps dead queries for
discriminating ones. Tier 1 extends this with severity-aware triage:
intractable queries are excluded before evaluation begins, saving budget
for informative samples.

### Tier 2 — L2 Strategic Intelligence (LLM)

Insights that require meta-reasoning about optimization direction. Injected
into L2 Refine only.

#### A: Round trajectory

**Current:** Critique sees `round_history`. L2 sees critique text.
**Target:** L2 receives a compact structured summary: accuracy trend,
stall count, best-ever vs current, direction (improving / oscillating /
degrading). Built from `state.rounds` — no new data collection needed.

#### C: Failure group → axis cross-tabulation

**Current:** Sensitivity scan produces per-axis deltas (overall).
Failure clusters group queries by bottleneck step. Both exist
independently.
**Target:** Cross-tabulate — "for queries that fail at web_search,
which axes produce the largest accuracy lift?" Injected into L2 so it
can direct L1 toward high-impact axes for the dominant failure group.

**Building blocks:** `ingest_cohort_analysis()`,
`query_sensitive_axes()`, `parameter_failure_correlation()` on
SearchMemory. Producer (`cohort_sensitivity()` in `cohort_analysis.py`)
was implemented then removed. Needs to be rewired from scan results.

#### D: Candidate comparison

**Current:** L2 only sees the winner's critique.
**Target:** L2 receives a compact summary of how all candidates
performed — which were close, what approaches they tried. Prevents L2
from directing L1 toward approaches already tested and found wanting.

**Building blocks:** `L1EvalResult.candidate_scores` and
`L1EvalResult.all_candidate_results` carry this data.

## 5. Priority Order

| Item | Tier | Target | Value | Effort | Dependencies |
|------|------|--------|-------|--------|--------------|
| B — Failure streak triage | Deterministic | Code | High | Low | `_query_hits` already tracked |
| A — Round trajectory | Strategic | L2 | High | Low | `state.rounds` already available |
| C — Failure group × axis | Strategic | L2 | Medium | Medium | Needs producer rewired from scan |
| D — Candidate comparison | Strategic | L2 | Medium | Low | `candidate_scores` already available |
