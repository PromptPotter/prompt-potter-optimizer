# Milestone 8: Campaign Intelligence

**Version:** 0.3.0
**Date:** 2026-03-30
**Status:** Planned
**Depends on:** [M7 Optimizer-as-Pipeline](archive/m7-optimizer-pipeline.md)

---

## Context

The core optimization loop works end-to-end, but campaigns don't yet produce the desired accuracy gains. The root cause is **depth-first exploitation**: the L1/L2 loop follows the critique's single suggestion each round, drilling into one direction instead of exploring broadly. A data scientist would look at failures, identify multiple improvement directions, and pursue the most promising ones in parallel. The system should do the same.

Three compounding weaknesses prevent this:

1. **Context-poor generation** — Rich per-query `pipeline_data` (candidate lists, entity profiles, step timings, diagnostics) flows through every evaluation but gets reduced to a single accuracy number. L1/L2/advisor don't see *why* queries fail, so they can't identify multiple directions. The system gets more data each round but doesn't get smarter.

2. **Uninformed scanning** — The sensitivity scan probes all axes uniformly, including dead ones. Sample sizes are set manually with no statistical foundation — 3 queries per variant produces 4 possible accuracy values (0%, 33%, 67%, 100%), which is pure noise. No early pruning, no adaptive allocation.

3. **Redundant computation** — Every prompt variant re-runs the entire backend pipeline, even though upstream nodes (web_search, token_matching) produce identical output when only the prompt changes. A 20-variant x 100-query campaign wastes ~1,900 upstream calls.

**Priority order:** Data-guided exploration (1) > enriched failure context (1) > smarter scan (2) > caching (3) > cross-campaign memory (4).

---

## 1. Data-Informed Intelligence

**Problem:** Each round accumulates more evaluation data (per-query hits/misses, pipeline diagnostics, axis sensitivity profiles, query difficulty), but this analysis doesn't flow into the LLM prompts that generate new candidates or recommend scan parameters. Worse, the L1 loop depth-first follows one critique direction per round. The system should identify multiple improvement directions from failure data and explore them in parallel.

**Design principles:**

- **Breadth over depth.** Failure analysis should surface MULTIPLE distinct improvement directions (e.g., "60% of failures are upstream retrieval gaps" AND "20% are ranking errors on long entity names"). L1 should generate candidates targeting different directions, not N variations on the same theme.
- **Schema-driven diagnostics.** Per-sample signal extraction must be derived from `PipelineSchema` nodes and `ROLE_METRIC_REGISTRY`, not hardcoded failure categories. If the pipeline changes (new node, new role), the diagnostics change automatically.
- **Two levels of analysis.** *Sample diagnostics* (Level A) extract signals from each individual query result. *Evaluation profiles* (Level B) aggregate sample diagnostics across all samples for one SearchPoint evaluation into structured failure analysis. Level A feeds Level B; they are implementation layers of one feature, not separate concerns.
- **Tailored context per consumer.** Each decision-making step (L1/L2/L3/advisor) needs a tailored analysis context — the right information for that step's decision, not the same dump for everyone.
- **Freshly compiled each round.** Every time L1, L2, L3, or the scan advisor runs, the data context must be freshly compiled from all accumulated rounds. The system gets more data each round — the prompts must reflect that.

**Approach:**

### 1a. Sample diagnostics (Level A)

New function `extract_sample_diagnostics(result, pipeline_schema) -> dict[str, float|bool]`:

- Walks `pipeline_schema.nodes`; for each node with `node_role` in `ROLE_METRIC_REGISTRY`, reads the corresponding `pipeline_data_key` from the result dict and computes **per-query** signals (the per-query complement to `derive_metrics()` which only does aggregates):
  - `candidate_source` role: `gt_in_source` (bool), `n_source_candidates` (int), `gt_source_rank` (int|None)
  - `ranker` role: `gt_in_ranked` (bool), `n_ranked_candidates` (int), `gt_rank` (int|None), `top_score_gap` (float)
  - `enricher` role: `n_enriched_fields` (int) from `output_schema.fields` count vs populated count
  - `cache` role: `cache_hit` (bool)
- Infrastructure diagnostics (always extracted, any pipeline): `terminated_at` (str), `total_time_ms` (float), `degraded` (bool), `error` (bool)
- Output: flat dict of named diagnostic values. Names are deterministic: `{node_name}_{metric}` when namespacing needed, `{metric}` when unique.

**Extends existing pattern:**
- `ROLE_METRIC_REGISTRY` (`pipeline_schema.py:141`) already maps role -> metrics -> `pipeline_data_key`
- `_extract_pipeline_data()` (`prompt_eval.py:130`) already collects everything the schema describes
- Same registry, same keys, per-result instead of aggregate

**Location:** `api/services/metrics.py` (alongside existing `derive_metrics()`)

### 1b. Evaluation profiles (Level B) — failure analysis compilation

Deterministic analysis functions that compile sample diagnostics into structured evaluation profiles:

- `compile_failure_analysis(results, pipeline_schema) -> FailureAnalysis` — groups query failures by diagnostic pattern (e.g., "12 queries: GT not in source candidates" vs "5 queries: GT ranked 7-15"). Patterns discovered from diagnostic vectors, not hardcoded categories. Each pattern becomes a named **improvement direction**.
- `compile_query_difficulty(historical_results) -> QueryDifficulty` — per-query hit rate across configurations, classifying queries as easy/discriminating/hard/dead.
- `compile_temporal_trends(campaign_rounds) -> TrendAnalysis` — which queries improved/regressed over rounds, which diagnostic values shifted.

Each function is pure computation (no I/O, no LLM calls).

### 1c. Enriched failure context in LLM prompts

- Failure examples currently formatted as `Q -> predicted -> GT` (via `format_failure_examples()`)
- Enrich with sample diagnostics: `Q -> predicted -> GT | gt_in_source=False, n_candidates=20, terminated_at=token_matching`
- Add failure analysis summary as a structured section: "Dominant failure pattern: GT not retrieved by candidate_source (60% of failures) — focus on web_search/entity_profiling params"
- **Multi-direction injection:** surface 2-3 distinct improvement directions ranked by failure count, not just the dominant one. L1 should see all directions and generate candidates across them.
- Inject via existing `{{failure_examples}}` and `{{focus_note}}` template slots in meta-prompts
- Location: `api/services/campaign/formatting.py` (modify `format_failure_examples()`)

### 1d. Adaptive sampling in optimizer feedback cycle

The feedback cycle should pick queries that maximise information about which prompt changes actually help — not random samples.

- After each round, analyze per-query diagnostic variance across rounds using `extract_sample_diagnostics()`
- Drop "dead" queries: queries that always hit or always miss regardless of prompt variant carry zero discriminative power
- Replace with "discriminating" queries from the full eval pool: queries whose outcome changes depending on configuration
- Implementation in `optimization_loop.py` or a new `adaptive_eval.py` module called by the cycle
- Uses `LoopState.current_results` (already tracked per round) as input
- If the method works, it can later be offered to the sensitivity scan as an option

---

## 2. Smarter Sensitivity Scan

**Problem:** The OAT scan probes every axis variant uniformly. Some axes never produce signal at the current search space location, wasting eval budget on uninteresting regions. Sample sizes are set manually with no statistical foundation.

**Design principles:**
- The sensitivity scan is **maximal exploration** — its job is to map the search space, not optimize. The optimizer (feedback cycle) carries the main load and balances exploitation/exploration.
- Sample selection must be **location-aware**: which variants are worth probing depends on where we currently are in the search-point space. An axis that's dead at one baseline may be alive at another.
- Think of it as combined exploration of suitable samples *given* the current search-point location — not blind enumeration, but informed coverage of the regions that matter from where we stand.
- Architecture must remain simple — add branch points to the algorithm where data justifies them, but avoid over-engineering.

**Approach:**

### 2a. Statistically grounded sample sizing

- `build_diagnostic_set()` uses `min_detectable_effect(n)` (already in `_stats.py`) to compute minimum n for a target MDE
- If user-specified `scan_sample_size` is below minimum for detecting a 15% effect, auto-adjust upward with a warning
- This alone would have prevented the "3 queries = no signal" problem

### 2b. Per-axis early pruning

- After evaluating a variant, compute Wilson CI for that variant vs baseline
- If CIs **fully overlap** for all variants tested so far on an axis, mark axis as "noise" and skip remaining values (early pruning, not early stopping — we always test at least 2 values per axis)
- Use `proportion_test()` to annotate significant vs non-significant deltas in scan results
- Budget savings: skip 3-5 remaining values on dead axes -> ~30% fewer eval calls in typical scans

### 2c. Diagnostic-aware sample stratification

- Current `build_diagnostic_set()` does random 75/25 hit/miss split
- Use `extract_sample_diagnostics()` (1a) to stratify misses by diagnostic pattern — ensure each distinct failure pattern is represented (e.g., "GT not in candidates" vs "GT ranked low" vs "infrastructure error")
- Diagnostic patterns are discovered from data, not hardcoded categories

---

## 3. SearchMemory — Cross-Campaign Intelligence

**Problem:** Each campaign starts from scratch. The system collects rich per-query data every evaluation but this intelligence doesn't persist or compound across campaigns. The scan advisor sees pipeline *structure* but not pipeline *performance*. Initial values for text axes (prompt fragments, output schemas) are arbitrary guesses — the system should know what kinds of values historically worked.

**What it is:** A **materialized view** — a persistent, incrementally-updated statistical index over ALL historical search points and their results. Persisted to disk, updated lazily (when an LLM needs it and the watermark is stale), queryable by any optimizer node via atomic data accessors.

**Location:** `api/services/search/search_memory.py`
**Disk:** `.promptpotter/projects/{backend_id}/search_memory.json`

### Three analysis pillars

**Parameter Impact** — for each search space dimension (prompt field, pipeline param, model, temperature):
- Effect size (mean accuracy delta when this dimension changes, across all evaluations)
- Consistency (fraction of comparisons where delta > noise threshold)
- Top-5 values (best-performing concrete values with mean accuracy and sample count — for text axes, this shows what language/patterns/structures historically worked)
- Classification: `"consistently_impactful"` / `"sometimes_impactful"` / `"dead"`

**Query Patterns** — for each query:
- Tractability (hit rate across all evaluations)
- Discriminative power (variance in hit/miss across configurations)
- Sensitive dimensions (which axes most affect this query's outcome)
- Dominant failure mode (most common `terminated_at` step)

**Failure Modes** — cross-cutting:
- Bottleneck distribution (`terminated_at` step -> failure fraction)
- Failure clusters (queries grouped by same failure reason)
- Parameter-failure correlation (which dimensions correlate with failure modes)
- Trend (is the dominant bottleneck shifting across campaigns?)

All analysis is **schema-driven** via `PipelineSchema.nodes` and `node_role`. Statistical method is behind a **swappable strategy** (start with mean-delta, easily replaced with marginal effect estimation).

### Incremental update

Each pillar tracks a **watermark** (set of dataset_run IDs already processed). On refresh:
- Compare current run IDs against watermark
- Load only new runs' per-query data
- Update rolling statistics — no full recomputation
- Persist updated view + new watermark

### Atomic API

SearchMemory exposes **granular data accessors** returning structured data. Each consumer (scan_advisor, L1, L2, critique) composes and formats what it needs. SearchMemory never generates LLM-ready text.

```python
class SearchMemory:
    # --- Parameter Impact ---
    def axis_rankings(self) -> list[AxisImpact]
    def top_k_values(self, axis: str, k: int = 5) -> list[ValueRecord]
    def axis_impact(self, axis: str) -> AxisImpact | None

    # --- Query Patterns ---
    def query_tractability(self) -> list[QueryRecord]
    def discriminating_queries(self, min_variance: float = 0.1) -> list[QueryRecord]
    def dead_queries(self, max_hit_rate: float = 0.0) -> list[QueryRecord]
    def query_sensitive_axes(self, query: str) -> list[str]

    # --- Failure Modes ---
    def bottleneck_distribution(self) -> dict[str, float]
    def failure_clusters(self) -> list[FailureCluster]
    def parameter_failure_correlation(self, axis: str) -> dict[str, float]

    # --- Lifecycle ---
    def refresh(self, store, backend_id) -> bool
```

### Consumer composition

| Node | Accessors used | Purpose |
|------|---------------|---------|
| **Scan advisor** | `axis_rankings()`, `top_k_values()`, `bottleneck_distribution()` | Prioritize axes, suggest text values, focus on bottleneck |
| **L1 generate** | `top_k_values()`, `dead_queries()`, `failure_clusters()` | Historically-best prompt patterns, what to fix |
| **L2 refine** | `axis_rankings()`, `bottleneck_distribution()` | Unexplored dimensions, shifting bottleneck |
| **Critique** | `discriminating_queries()`, `failure_clusters()` | Focus analysis, retrieval vs ranking attribution |

### Supporting additions

**Bottleneck attribution** — `attribute_bottleneck(results, pipeline_schema) -> dict` in `metrics.py`. Maps `terminated_at` + `PipelineSchema.nodes` ordering -> relevant `param_keys`. Quick win: inject into advisor before full SearchMemory.

**Causal scan ordering** — order axes by pipeline depth (from `PipelineSchema.nodes`). After upstream improvement in adaptive search, re-check downstream sensitivity. In `adaptive_search.py`.

**Query cohort sensitivity** — persist per-query scan results (minor `sensitivity_scan.py` change), slice by failure mode cohort for free cohort-level sensitivity. New `cohort_analysis.py`.

---

## 4. Partial Pipeline Caching

**Problem:** During optimization, only `llm_ranking` config changes between prompt variants. Steps before the first ranker node produce identical output for the same query.

**Approach:**
- Compute `upstream_config_hash` from pipeline_params excluding ranker nodes (using `PipelineSchema.split_at_ranker()`)
- Store per-node intermediate outputs (`node_outputs`) returned by TermNorm in a disk cache keyed by `(upstream_hash, query)`
- On subsequent evals with matching upstream config, send cached intermediates via new `precomputed` field in `/matches` request; TermNorm skips those nodes

**Wire protocol (TermNorm additions, backward-compatible):**
- Response: `data.node_outputs: {node_name: opaque_output_dict}`
- Request: `precomputed: {node_name: opaque_output_dict}` — TermNorm injects into pipeline data flow, executes only remaining steps

**PromptPotter changes:**
- `upstream_config_hash()` in `hashing.py`, `split_at_ranker()` on `PipelineSchema`
- New `IntermediateCache` store in `stores/intermediate_cache.py` (disk-backed, `{backend_id}/intermediate_cache/{upstream_hash}.json`)
- `eval_query_via_backend()`: check cache -> build `precomputed` -> call `run_match()` -> store `node_outputs`
- `run_match()`: thread `precomputed` to wire payload

---

## Waves

| Wave | Scope | Depends on | Side |
|------|-------|------------|------|
| 1a | Sample diagnostics: `extract_sample_diagnostics()` in `metrics.py` | — | PP |
| 1b | Failure analysis compilation: `compile_failure_analysis()`, `compile_query_difficulty()`, `compile_temporal_trends()` | 1a | PP |
| 1c | Enriched failure context + multi-direction injection in L1/L2/advisor prompts | 1b | PP |
| 1d | Adaptive sampling: drop dead queries, replace with discriminating ones | 1a | PP |
| 2a | Statistical sample sizing in `build_diagnostic_set()` | — | PP |
| 2b | Per-axis early pruning via Wilson CI overlap | 2a | PP |
| 2c | Diagnostic-aware sample stratification | 1a, 2a | PP |
| 3a | SearchMemory core: data model, incremental update, watermark, persistence | — | PP |
| 3b | Three analysis pillars: parameter_impact (with top-5), query_patterns, failure_modes | 3a | PP |
| 3c | Context injection: scan_advisor, L1, L2, critique consume SearchMemory atomically | 3b | PP |
| 3d | Bottleneck attribution + causal scan ordering | 1a | PP |
| 3e | Query cohort sensitivity (per-query scan result persistence + cohort slicing) | 1a | PP |
| 3f | Docs: SearchMemory in architecture.md, sensitivity-scan.md, optimization.md | 3c | PP |
| 4a | Upstream hash + `split_at_ranker()` + `IntermediateCache` store | — | PP |
| 4b | TermNorm: `node_outputs` response + `precomputed` request | 4a | TN |
| 4c | Eval gateway integration (cache lookup/populate in `eval_query_via_backend`) | 4a, 4b | PP |

### Dependency graph

```
Wave 1 (Data-Informed Intelligence):
  1a ──┬── 1b ── 1c
       └── 1d

Wave 2 (Smarter Scan):
  2a ──── 2b
  1a + 2a ── 2c

Wave 3 (SearchMemory):
  3a ── 3b ── 3c ── 3f
  1a ──┬── 3d
       └── 3e

Wave 4 (Caching):
  4a ── 4b ── 4c

All four wave groups are independent at their roots.
1a is the shared foundation for Waves 1, 2c, 3d, and 3e.
```

---

## Key Existing Code

| Code | Role | Location |
|------|------|----------|
| `ROLE_METRIC_REGISTRY` | Maps node_role -> metrics -> pipeline_data_key | `api/models/pipeline_schema.py:141` |
| `IntermediateMetric` | Per-role metric definition | `api/models/pipeline_schema.py` |
| `derive_metrics()` | Aggregate metrics from node roles | `api/services/metrics.py:99` |
| `_extract_pipeline_data()` | Assembles per-query pipeline_data from schema | `api/services/prompt_eval.py:130` |
| `obs_extraction_map()` | Schema -> observation mapping | `api/models/pipeline_schema.py` |
| `wilson_ci()`, `proportion_test()`, `min_detectable_effect()` | Statistical tools (exist, unused in decisions) | `notebooks/campaign_lib/stats.py` |
| `build_diagnostic_set()` | Current sample selection (random 75/25 stratification) | `api/services/search/smart_search.py:169` |
| `LoopState` | Feedback cycle state (has `current_results` per round) | `api/services/campaign/state.py:38` |
| `format_failure_examples()` | Current failure formatting for LLM prompts | `api/services/campaign/formatting.py:48` |
| `DatasetRunStore` | All historical evaluations (per-query results with pipeline_data) | `api/services/stores/dataset_run_store.py` |
| `CampaignStore` | Campaign trials, lineage, configs | `api/services/stores/campaign_store.py` |
| `PlanStore` | Smart search plans with axis_profiles | `api/services/stores/plan_store.py` |
| `build_pipeline_overview()` | Advisor context layer (pipeline structure) | `api/services/search/scan_advisor.py` |

---

## Entry Criteria

- M7 exit gate passed
- `PipelineSchema` with `node_role` populated (M6 Wave 6)

## Exit Criteria

- `extract_sample_diagnostics()` produces per-query diagnostic dict from PipelineSchema without hardcoded pipeline knowledge
- L1 generate prompt includes multi-direction failure analysis with diagnostic data from accumulated rounds
- Optimizer feedback cycle drops dead queries and replaces with discriminating ones between rounds
- Sensitivity scan auto-adjusts sample size below MDE threshold; skips dead axes via CI overlap
- SearchMemory produces non-trivial parameter impact rankings with top-5 values after 2+ campaigns
- Scan advisor, L1, L2, critique all consume SearchMemory via atomic accessors
- Incremental update: one new eval run triggers only delta recomputation, not full rebuild
- Second eval of same query with same upstream config skips upstream nodes (verified via step_timings)
