# Sensitivity Scan

Exploration tool for understanding the parameter landscape before optimization.

---

## Sensitivity Scan

Perturbs one axis at a time (OAT) and measures accuracy deltas against your baseline.

**When to use:** Before optimization (identify which axes matter), after optimization (find remaining room), or after backend changes (verify which sensitivities shifted).

### Workflow (7 Cells)

1. **Scan advisor** — LLM recommends axes and variant values from pipeline config
2. **Edit variants** — review and adjust the `scan_variants` dict
3. **Prepare scan baseline** — restructure backend prompt into internal fields for independent perturbation
4. **Sensitivity scan** — evaluate each axis independently against `sample_size` queries
5. **Variant leaderboard** — display all scan combos ranked by accuracy with per-axis statistics
6. **Query difficulty** — classify queries as easy/discriminating/hard/error from historical scan runs
7. **Select winner & seed** — pick the best starting point for the feedback cycle

### Scan Variants

Flat dict, auto-classified by the service layer:

```python
scan_variants = {
    "persona": ["", "You are a domain expert.", "You are a careful analyst."],
    "thinking_style": ["", "Think step by step.", "Consider semantic similarity."],
    "temperature": [0.0, 0.3, 0.7],  # pipeline params auto-detected
}
```

### Scan Baseline & Coverage

Before evaluation, `prepare_scan_baseline()` reports per-axis coverage from historical data — which values are cached vs new. Uses the `dataset_runs` index for instant lookups.

**Prompt alias groups** link restructured prompts to their originals so historical pipeline-parameter results are discoverable. Resolution is transitive.

### SearchMemory Integration (M8 — Planned)

When historical data exists, SearchMemory enriches the scan workflow:

- **Scan advisor** receives `axis_rankings()` (effect size + consistency classification), `top_k_values()` (historically-best values per axis), and `bottleneck_distribution()` (failure fraction per pipeline step). It prioritizes consistently impactful axes, skips dead ones, and suggests values with historical evidence.
- **Diagnostic set** can be stratified using query tractability data (`discriminating_queries()` returns queries whose outcome varies across configurations, vs always-hit/always-miss).
- **Bottleneck attribution** (`attribute_bottleneck()` in `metrics.py`) maps each `terminated_at` step to the `param_keys` of that node plus all upstream nodes. The scan advisor uses this to order axes by causal pipeline depth -- parameters that feed into the dominant bottleneck are scanned first.

SearchMemory is a materialized view refreshed lazily via watermark -- no extra computation during the scan itself.

### Cohort Sensitivity (M8 — Planned)

Standard sensitivity scan measures per-axis accuracy deltas over the full query set. Cohort sensitivity (`cohort_analysis.py`) slices those results by failure mode to answer: "Which axes matter most for which failure types?"

**How it works:**

1. Failure clusters from SearchMemory group queries by `terminated_at` step (e.g., `web_search`, `token_matching`).
2. Per-query hit/miss results from scan rows are aggregated per cohort.
3. For each (axis, cohort) pair, the delta between baseline hit rate and best-value hit rate is computed.
4. Results are ranked by absolute delta and ingested into SearchMemory via `ingest_cohort_analysis()`.

**What it enables:**
- The scan advisor can recommend different axes for different failure modes (e.g., `query_prefix` matters for `web_search` failures but not `token_matching` failures).
- `query_sensitive_axes(query)` returns the axes most relevant to a specific query based on its cohort membership.
- `parameter_failure_correlation(axis)` returns per-failure-mode deltas for an axis.

Implementation: `api/services/search/cohort_analysis.py`. Data models: `CohortSensitivity`, `CohortAnalysisResult`.

### Circuit Breaker

The scan aborts early on cascading failures:
- **Baseline all-errors** — cancels immediately (backend likely down)
- **2 consecutive all-error variants** — aborts with diagnostic message

If the backend restarts mid-scan, `BackendClient` auto-reinitializes the session on 400 and retries transparently.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `sample_size` | 30 | Queries per variant (0 = all) |

