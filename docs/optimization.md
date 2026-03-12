# Optimization

The feedback cycle and the 3-layer optimization model.

---

## 3-Layer Optimization Model

Parameters are organized into three layers with different optimization cadences. The pipeline snapshot (`show_pipeline_snapshot(svc)`) determines which parameters are available.

### Layer 1: Generate (innermost loop)

Tunable parameters discovered from the pipeline's active nodes. Changed every round.

| Category | Source | Examples |
|----------|--------|----------|
| Prompt fields | LLM nodes (`llm_ranking`, `entity_profiling`) | `prompt`, `persona`, `task_intent`, `instruction`, `thinking_style`, `answer_format` |
| Model params | Any LLM node | `temperature`, `model`, `max_tokens` |
| Output schema | LLM nodes with structured output | `output_schema` field overrides |
| Pipeline params | Non-LLM nodes (`fuzzy_matching`, `token_matching`) | thresholds, weights, `sample_size` |

Which parameters are Layer 1 depends on the pipeline config — not a fixed list. The scan advisor reads the full pipeline snapshot to recommend which axes to optimize.

### Layer 2: Refine Context

Adjusted when Layer 1 improvements stall:

| Field | Purpose |
|-------|---------|
| `context` | Additional optimization context |
| `parameters` | Hypervariables (family, version, template_variables) |

### Layer 3: Modify Plan

Optimization strategy — rarely changed:

| Field | Purpose |
|-------|---------|
| `plan` | High-level optimization strategy |

`render()` assembles prompt fields into the final string. `derive()` creates child states forming a lineage chain.

---

## Feedback Cycle

Automated optimization with 3-layer escalation:

1. Each round generates N candidates via LLM, evaluates against the backend, selects the winner
2. If Layer 1 stalls, escalates to Layer 2 (context refinement), then Layer 3 (strategy)
3. Stops on: `patience` consecutive non-improving rounds, `max_rounds`, or perfect accuracy

### Scan-Aware Generation

When scan data is available (from sensitivity scan), the feedback cycle operates in **scan-aware mode**:

- `prepare_scan_context()` formats scan analytics (leaderboard, axis sensitivity, query difficulty, tested values) into structured text
- The LLM meta-prompt is enriched with this context so it can reason about which pipeline_param combinations to try
- Each candidate can include a `pipeline_params_override` — individual pipeline params per candidate instead of one shared config
- The notebook's optimize cell automatically detects scan variables (`scan_df`, `axis_profiles`, `scan_variants`, `difficulty_df`) and passes them through

This means the feedback cycle can optimize **pipeline parameters** (not just prompt text), which is critical when `exclude_steps: ["llm_ranking"]` makes all optimization axes pipeline params.

### Configuration

```python
"optimization": {
    "patience": 3,
    "improvement_threshold": 0.01,
    "n_variants": 5,
    "creativity": 0.7,       # meta-prompt temperature
    "max_rounds": 10,
    "enable_l2": True,        # refine context on diminishing returns
    "l2_patience": 2,         # L1 stalls before L2 escalation
    "enable_l3": True,        # modify plan on L2 stall
    "l3_patience": 1,         # L2 stalls before L3 escalation
}
```

---

## Progress Tracking

```
Round  Accuracy  Rolling Avg (8)  Trend
  0    62.9%     62.9%            -
  G    71.4%     67.1%            +8.6%
  1    74.3%     69.5%            +2.9%
  2    74.3%     70.7%            +0.0%  <-- plateau
```

---

## Configuration Reference

```python
campaign_config = {
    "sample_size": 35,              # queries per eval step (0 = all)
    "exploration_rate": 0.5,        # grid search: 0.0=conservative, 1.0=aggressive
    "exclude_steps": ["llm_ranking"],  # pipeline steps to skip
    "optimization": {
        "n_variants": 5,
        "creativity": 0.7,
        "improvement_threshold": 0.01,
        "patience": 3,
        "max_rounds": 10,
        "enable_l2": True,          # refine context on L1 stall
        "l2_patience": 2,
        "enable_l3": True,          # modify plan on L2 stall
        "l3_patience": 1,
    },
    "eval_llm": { ... },
    "grid_search": {
        "grid_budget": 35,
        "sample_size": 1,
        "shared_queries": False,
        "seed": 42,
        "top_k": 5,
        "use_defaults": True,
    },
}
```

## Troubleshooting

**Feedback cycle stalls at low accuracy** — Lower `improvement_threshold`, increase `n_variants`, or manually escalate to Layer 2/3.

**Sensitivity scan aborted early** — Circuit breaker triggered. See [Sensitivity Scan: Circuit Breaker](sensitivity-scan.md#circuit-breaker).
