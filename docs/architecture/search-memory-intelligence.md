# Methods: SearchMemory as Intelligence Feed

SearchMemory is a materialized view over all historical evaluation data,
persisted at `{backend_id}/search_memory.json`. It is refreshed
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

Built by `build_l1_search_memory_digest()`.[^l1fmt] Injected as a
"HISTORICAL INTELLIGENCE" section in the L1 meta-prompt via
`format_context_sections()`.

| Signal | Source accessor | Prompt text |
|--------|----------------|-------------|
| Failure patterns | `failure_clusters(3)` | "Common failure patterns: web_search (45%), token_matching (30%)" |
| Dead queries | `dead_queries()` | "Dead queries (never hit): 12 queries" |
| High-impact axes | `axis_rankings()[:3]` | "High-impact axes: web_search.max_sites (effect=0.082, consistently_impactful)" |
| Best values | `top_k_values(top_axis, 3)` | "Best-performing values: 7 (acc=72.0%), 5 (acc=68.0%)" |

[^l1fmt]: `services/campaign/formatting.py:build_l1_search_memory_digest()`.

### L2 Refine (intelligence bundle)

Built by `build_strategic_search_memory_digest()`.[^l2fmt] Injected via
`format_l2_intelligence()`.

| Signal | Source accessor | Prompt text |
|--------|----------------|-------------|
| Axis rankings | `axis_rankings()[:5]` | "Axis impact rankings: ..." |
| Bottleneck distribution | `bottleneck_distribution()` | "Bottleneck distribution: web_search: 45%, token_matching: 30%" |

[^l2fmt]: `services/campaign/formatting.py:build_strategic_search_memory_digest()`.

### Critique Agent (Every-Round Intelligence Hub)

Built inline in `round_execution.py`.[^crit] Passed as
`search_memory_digest` on `CritiqueContext`. Critique is the
**every-round intelligence hub** — it runs every round, is the sole
reader of raw eval results, AND receives SearchMemory intelligence to
frame its analysis.

| Signal | Source accessor | Prompt text |
|--------|----------------|-------------|
| Discriminating queries | `discriminating_queries()` | "12 queries vary across configs" |
| Failure clusters | `failure_clusters(3)` | "web_search (45%); token_matching (30%)" |
| Tractability profiles | `persistent_failures(3)` | "5 intractable (never hit); 3 chronic" |
| Axis exhaustion | `exhausted_axes()` | "web_search.max_sites (5 values tested, effect=0.008)" |
| Value trends | `axis_value_trend()` | "web_search.max_sites: increasing" |

The critique agent also receives `round_history` — a list of
per-round dicts with accuracy, composite, pipeline_params, degraded
count, and candidate count.[^rh]

[^crit]: `services/campaign/round_execution.py`.
[^rh]: `round_execution.py` — built from `state.rounds`.

## 3. Design Principle: L1 Stays Clean

L1 focuses on generating diverse candidates. Critique is the every-round hub (raw eval + SearchMemory). L2 fires on escalation only (trajectory, candidate comparison, failure group × axis). Deterministic code handles per-query triage.

**Three-tier intelligence architecture:**

| Tier | Handled by | Fires when | What | Example |
|------|-----------|------------|------|---------|
| **Deterministic** | Code (statistics) | Every round | Per-query triage without LLM reasoning | CI-gated intractable exclusion, eval set adaptation |
| **Every-round** | Critique (LLM) | Every round | Frame this-round analysis with historical context | Tractability profiles, axis exhaustion, value trends |
| **Strategic** | L2 Refine (LLM) | Escalation only | Meta-reasoning about why optimization is stuck | Round trajectory, candidate comparison, failure group × axis |

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
It never produces LLM-ready text. Each consumer (scan_advisor, L1,
L2, critique) composes and formats its own digest from the accessors
it needs. This is a deliberate constraint: it keeps SearchMemory from
growing into a god object that knows about every prompt template, and
lets new consumers add tailored context without changing the shared
state.

## 5. Foundation: Two-Tier Sample Intelligence

### Tier 1 — Deterministic Sample Triage (Code)

Per-query failure streak detection via `_query_hits` Bernoulli sequences. Three severity levels:

- **Intractable** (never hit) → `intractable_queries_ci()` excludes via Wilson CI. No optimizer signal.
- **Chronically failing** (recent streak) → `persistent_failures(min_streak)` deprioritizes. Flagged to L2.
- **Intermittent** (variable) → kept in eval set. High discrimination value.

`adapt_eval_set()` swaps dead queries for discriminating ones. Intractable queries excluded before evaluation begins.

### Tier 2 — L2 Strategic Intelligence (LLM)

Meta-reasoning injected into L2 Refine only:

- **Round trajectory** — `build_round_trajectory()`: accuracy trend, stall count, direction. Built from `state.rounds`.
- **Failure group × axis** — `parameter_failure_correlation()`: cross-tabulates failure clusters with per-axis deltas. Producer runs after scan via `failure_group_sensitivity()`.
- **Candidate comparison** — `build_candidate_comparison()`: how all candidates performed, preventing L2 from repeating tested approaches.

## 6. Status

All core items implemented: failure streak triage + CI gating, round trajectory, failure group × axis, candidate comparison, critique tractability/exhaustion/trends, L3 intelligence.

**Planned:** Diminishing returns detector, candidate diversity monitor, query improvement attribution, cross-candidate failure diff, failure group refresh during optimization.
