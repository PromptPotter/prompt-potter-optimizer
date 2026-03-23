# Sensitivity Scan & Grid Search

Exploration tools for understanding the parameter landscape before optimization.

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

### Circuit Breaker

The scan aborts early on cascading failures:
- **Baseline all-errors** — cancels immediately (backend likely down)
- **2 consecutive all-error variants** — aborts with diagnostic message

If the backend restarts mid-scan, `BackendClient` auto-reinitializes the session on 400 and retries transparently.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `sample_size` | 30 | Queries per variant (0 = all) |

---

## Grid Search

Explores a cartesian product of prompt field variants with distance-weighted sampling.

### Control Parameters

| Parameter | Purpose |
|-----------|---------|
| `exploration_rate` (0.0-1.0) | Biases sampling: 0.0=conservative (few changes), 1.0=aggressive (many changes) |
| `grid_budget` | Exact number of grid points to evaluate (0 = full grid) |
| `sample_size` | Queries per grid point (0 = all eval_data) |
| `shared_queries` | `False`=different random queries per point, `True`=same set for rigorous comparison |

```python
"grid_search": {
    "grid_budget": 35,
    "sample_size": 1,
    "shared_queries": False,
    "seed": 42,
}
```

### Grid Axes

Default to `load_variant_library()["prompt_fields"]` from `variant_library.yaml`. Override with `custom_axes`:

```python
grid_config = {
    "persona": ["", "You are a medical terminology expert."],
    "thinking_style": ["", "Think step by step.", "Consider semantic similarity."],
}
```

**Grid search takes too long** — Reduce `grid_budget` or `sample_size`.
