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

### SearchMemory Integration *(M8)*

When historical data exists, SearchMemory enriches the scan workflow:

- **Scan advisor** receives axis impact rankings and top-5 historically-best values per axis — prioritizes consistently impactful axes, skips dead ones
- **Diagnostic set** can be stratified using query tractability data (which queries are discriminating vs always-hit/always-miss)
- **Bottleneck distribution** tells the advisor which pipeline stage accounts for most failures, focusing scan effort on relevant parameters

SearchMemory is a materialized view refreshed lazily — no extra computation during the scan itself.

### Circuit Breaker

The scan aborts early on cascading failures:
- **Baseline all-errors** — cancels immediately (backend likely down)
- **2 consecutive all-error variants** — aborts with diagnostic message

If the backend restarts mid-scan, `BackendClient` auto-reinitializes the session on 400 and retries transparently.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `sample_size` | 30 | Queries per variant (0 = all) |

