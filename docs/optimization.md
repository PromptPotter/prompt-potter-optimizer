# Optimization

```
  INIT: baseline eval (+ scan) → bootstrap critique
    ↓
  ┌──────────────────────────────────────┐
  │  Growth/Filter ──► Eval + Critique   │
  │        ▲                       │     │
  │        └── critique + styles ◄─┘     │
  └──────────────────────────────────────┘
    │ stall?
    ├─► L1: prompt fields, model, pipeline params
    ├─► L2: refine context
    └─► L3: modify plan
```

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

Critique-guided optimization with 3-layer escalation, inspired by [PromptWizard](https://arxiv.org/abs/2405.18369)'s critique-and-refine pattern.

```
INIT: baseline eval (+ scan analytics when available) → bootstrap critique + sample thinking styles
  ↓
ROUND 0: Growth (bootstrap critique + styles) → Eval → critique₀ → winner vs current best
  ↓
ROUND 1: Growth (critique₀ + new styles)      → Eval → critique₁ → winner vs current best
  ...
```

Each round:

1. **Growth** — Generate N candidates. Meta-prompt includes **critique from previous round** + freshly sampled **thinking styles** as mutation guidance.
2. **Eval + Critique** — Evaluate candidates via the backend, compare by composite score against the current best (previous winner). Generate a **critique** of remaining failures/successes, fed forward to next round.
3. **Loop control** — Stall counter on no improvement. Patience exhausted → escalate L2 (context) → L3 (strategy). Stop on `max_rounds` or perfect accuracy.

**Init** bootstraps the first critique from baseline results. When scan data is available (leaderboard, axis sensitivity, query difficulty), it feeds into both the bootstrap critique and subsequent rounds via `prepare_scan_context()`.

### Critique Agent

Failure analysis is **separated from candidate generation** (PromptWizard pattern). The critique agent is a researcher with two specialized tools:

| Tool | When | Analyzes |
|------|------|----------|
| `negative_critique` | accuracy < threshold | Failure categories, root causes, priority fixes |
| `positive_critique` | accuracy >= threshold | Success patterns, how to extend strengths to remaining failures |

Threshold: `critique_positive_threshold` (default 0.7).

**Architecture**: Single LLM agent with two prompt templates today (like PromptWizard). Designed as an agent base class + tool registry to evolve into a multi-agent hub with additional specialized analysis tools.

Critique and thinking styles operate at the **optimizer agent level** — they guide candidate generation, not the pipeline prompt being optimized.

### Thinking Styles

Each round samples 2-3 styles from the variant library (`api/config/prompt_variants.json`, 35+ from published research) into the meta-prompt as mutation guidance. Structured diversity beyond temperature randomness.

### Scan-Aware Generation

When scan data is available, `prepare_scan_context()` enriches the meta-prompt with scan analytics and each candidate can include a `pipeline_params_override` for per-candidate pipeline param exploration. See [Sensitivity Scan](sensitivity-scan.md) for scan workflow details.

### Phase Events

The feedback cycle emits structured `PhaseEvent` objects at phase boundaries via the `on_phase` callback. The notebook renders these as ANSI-colored banners (`>>>` enter, `<<<` exit).

| Phase | Trigger | Key enter data | Key exit data |
|-------|---------|----------------|---------------|
| `init` | Cycle start | `max_rounds`, `patience`, `n_variants`, `model`, `sample_size`, `enable_l2`, `enable_l3`, `eval_data_count`, `baseline_accuracy`, `has_scan_context`, `enable_critique` | `cycle_id`, `resumed_from_round`, `baseline_accuracy`, `obs_enabled`, `sample_count`, `critique_text` (bootstrap) |
| `growth` | Candidate generation | `current_accuracy`, `prompt_preview`, `n_variants`, `creativity`, `model`, `has_scan_context`, `has_critique` | `n_candidates`, `n_eval_queries`, `loaded_from_disk`, candidates list |
| `analysis_eval` | Evaluation, winner selection & critique | `n_candidates`, `n_queries`, `current_best_accuracy`, `improvement_threshold` | `winner_label`, `winner_accuracy`, `winner_composite`, `improved`, `next_action`, `critique_text`, `critique_path` |
| `refine_context` | L2 escalation (when `enable_l2=True`) | `l2_round`, `stall_count`, `current_accuracy`, `best_accuracy` | `param_changes_count`, `context_changed`, `changes_description` |
| `modify_plan` | L3 escalation (when `enable_l3=True`) | `l3_round`, `l2_stall_count` | `new_plan_preview`, `changes_description` |

Each event: `phase` (str), `event` ("enter"/"exit"), `round` (int or None), `data` (dict), `timestamp` (ISO 8601).

---

## Configuration

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
        "enable_critique": True,              # critique-guided generation
        "critique_positive_threshold": 0.7,   # positive vs negative critique path
        "enable_l2": False,         # opt-in: refine context on L1 stall
        "l2_patience": 2,
        "enable_l3": False,         # opt-in: modify plan on L2 stall
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

**Critique produces generic advice** — The eval LLM may struggle with domain-specific failure analysis. Try a more capable model for `eval_llm.model`, or set `enable_critique: False` to fall back to direct generation with failure examples only.

**Candidates lack diversity** — Thinking styles provide structured mutation guidance but the eval LLM may ignore them at low temperatures. Increase `creativity` (meta-prompt temperature) or increase `n_variants`.

**Sensitivity scan aborted early** — Circuit breaker triggered. See [Sensitivity Scan: Circuit Breaker](sensitivity-scan.md#circuit-breaker).
