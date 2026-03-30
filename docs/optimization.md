# Optimization

```
┌─ ONE ROUND ────────────────────────────────────────────────────────────┐
│                                                                        │
│  L1 GENERATE (LLM)                                                     │
│    in:  critique (5 fields), task_context, thinking_styles,            │
│         scan_context, focus_note, failure_examples,                    │
│         search_memory (param impact, query patterns, failures) (M8)   │
│    out: N candidate OptSearchPoints (prompt + pipeline_params)         │
│         ↓                                                              │
│  L1 EVALUATE                                                           │
│    ┌─ Backend /matches ──── per candidate × per query ──────────────┐  │
│    │  in:  query + pipeline_params (per-node overrides)             │  │
│    │  out: ranked_candidates + diagnostics.warnings                 │  │
│    │                                                                │  │
│    │  DegradationCheck (per-query):                                 │  │
│    │    degraded_rate >= 0.4? → ABORT + EscalationSignal            │  │
│    └────────────────────────────────────────────────────────────────┘  │
│    Winner selection: best accuracy >= baseline + threshold             │
│         ↓                                                              │
│    ┌─ CRITIQUE (LLM) ──────────────────────────────────────────────┐  │
│    │  in:  pipeline_health, rank_analysis, round_evolution,        │  │
│    │       query_categories, anomaly_flags, scan_context           │  │
│    │                                                                │  │
│    │  acc >= 0.7 → positive path (what's working, extend)          │  │
│    │  acc < 0.7  → negative path (what's failing, fix)             │  │
│    │                                                                │  │
│    │  out: { positive_critique, negative_critique,                  │  │
│    │         priority_fix, suggested_axes, summary }                │  │
│    └────────────────────────────────────────────────────────────────┘  │
│         ↓                                                              │
│  ALL 5 fields → next L1 Generate + L2 Refine (on escalation)          │
│                                                                        │
│  ── ESCALATION (if degradation detected) ──────────────────────────── │
│                                                                        │
│  L2 REFINE CONTEXT (LLM) — meta-controller                            │
│    in:  critique (5 fields), task_context, escalation_journal,         │
│         round_summary                                                  │
│    out: updated task_context + meta-settings (creativity,              │
│         n_variants, sample_size)                                       │
│    L2 does NOT set pipeline_params — that's L1's job.                  │
│                                                                        │
│  L3 MODIFY PLAN (LLM) — if L2 stalls                                  │
│    out: new strategic plan                                             │
└────────────────────────────────────────────────────────────────────────┘
```

## Responsibility Matrix

| Agent | Decides | Does NOT decide |
|-------|---------|-----------------|
| **L1 Generate** | pipeline_params (query_prefix, max_sites, schema, temperature, ...) | `task_context`, meta-settings |
| **Critique** | what to focus on (suggested_axes, priority_fix) | pipeline_params values |
| **L2 Refine** | `task_context`, meta-settings (creativity, n_variants, sample_size), `l2_directive` | pipeline_params |
| **L3 Plan** | strategic plan | pipeline_params, `task_context` |

---

## 3-Layer Optimization Model

Parameters are organized into three layers with different optimization cadences. The target pipeline snapshot (`show_pipeline_snapshot(svc)`) determines which parameters are available.

### Layer 1: Generate (innermost loop)

Tunable parameters discovered from the target pipeline's active nodes. Changed every round.

| Category | Source | Examples |
|----------|--------|----------|
| Prompt fields | LLM nodes (`llm_ranking`, `entity_profiling`) | `prompt`, `persona`, `task_intent`, `instruction`, `thinking_style`, `answer_format` |
| Model params | Any LLM node | `temperature`, `model`, `max_tokens` |
| Output schema | LLM nodes with structured output | `output_schema` field overrides |
| Pipeline params | Non-LLM nodes (`fuzzy_matching`, `token_matching`) | thresholds, weights, `sample_size` |

Which parameters are Layer 1 depends on the target pipeline config -- not a fixed list. The scan advisor reads the full pipeline snapshot to recommend which axes to optimize.

### Layer 2: Refine Context

Adjusted when Layer 1 improvements stall:

| Field | Purpose |
|-------|---------|
| `optimizer_params` | Meta-settings (creativity, n_variants, sample_size, variant_strategy) |
| `task_context` | Structured domain context (domain, pipeline_purpose, data_characteristics, optimization_goals, key_challenges, raw_description). Decomposed from `TASK_DESCRIPTION` at init. L2 can refine individual fields. |

### Layer 3: Modify Plan

Optimization strategy -- rarely changed:

| Field | Purpose |
|-------|---------|
| `plan` | High-level optimization strategy |

`render_prompt()` assembles prompt fields into the final rendered prompt. `derive_candidate()` creates child points forming a lineage chain.

> **L4 (meta-optimization):** The escalation hierarchy extends naturally -- when L3 stalls, L4 optimizes the optimizer itself (meta-prompts, critique templates, optimizer parameters). See [M7 spec](specs/m7-optimizer-pipeline.md#l4-meta-optimization).

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

1. **Growth** -- Generate N candidates. The meta-prompt includes **critique from previous round** + freshly sampled **thinking styles** as mutation guidance.
2. **Eval + Critique** -- Evaluate candidates via the backend, compare by composite score against the current best (previous winner). Generate a **critique** of remaining failures/successes, fed forward to next round.
3. **Loop control** -- Stall counter on no improvement. Patience exhausted: escalate L2 (`task_context`) then L3 (strategy). Pluggable `EscalationCheck`s can also trigger L2/L3/abort mid-round (e.g., `DegradationCheck` on target pipeline regression). Stop on `max_rounds` or perfect accuracy.

**Init** bootstraps the first critique from baseline results. When scan data is available (leaderboard, axis sensitivity, query difficulty), it feeds into both the bootstrap critique and subsequent rounds via `prepare_scan_context()`.

---

## Critique Agent

Failure analysis is **separated from candidate generation** (PromptWizard pattern). The critique agent runs **inside L1 Evaluate**, after backend evaluation and winner selection. Its output feeds forward to **L1 Generate** (next round) and **L2 Refine** (on escalation).

### Critique Output

Both positive and negative paths produce the same 5-field JSON:

```json
{
  "positive_critique": "what's working — patterns to extend",
  "negative_critique": "what's failing — root causes and blockers",
  "priority_fix": "single most impactful change to make",
  "suggested_axes": ["query_prefix", "max_sites"],
  "summary": "2-3 sentence actionable critique"
}
```

Formatted by `format_critique_for_prompt()` and injected into:
- **L1 Generate** as `critique_text` (next round's meta-prompt)
- **L2 Refine** as `state.critique` dict (when escalation fires)

### Positive / Negative Routing

| Path | When | Focus |
|------|------|-------|
| Positive | `accuracy >= 0.7` | Success examples + remaining failures. Extend what works. |
| Negative | `accuracy < 0.7` | Rich target pipeline stats + failure examples. Diagnose root causes. |

Threshold: hardcoded at 0.7.

### Pre-Computed Stats (Negative Path)

`assemble_critique_prompt()` builds the meta-prompt from pure functions in `critique_stats.py`:

| Section | Key metrics | Source |
|---------|------------|--------|
| **Evaluation summary** | accuracy, composite, degraded count, stalls | `CritiqueContext` |
| **Anomaly flags** | `high_degradation`, `web_search_failure`, `near_miss_pattern`, `plateau_signal` | `detect_anomalies()` |
| **Pipeline health** | `web_search_degradation_rate`, `url_yield`, timing p50/p90/max | `compute_pipeline_health()` |
| **Rank analysis** | rank buckets, top-k recall, near misses | `compute_rank_analysis()` |
| **Round evolution** | accuracy trajectory, degraded trend | `compute_round_evolution()` |
| **Query categories** | failures by termination node, blindspot terms | `compute_query_categories()` |
| **Scan context** | leaderboard, improving axes | From `CritiqueContext.scan_context` |
| **Search memory** *(M8)* | axis impact rankings, top-5 values, bottleneck distribution, dead queries | `SearchMemory` atomic accessors |

### Anomaly Flags

| Flag | Fires when | Severity |
|------|-----------|----------|
| `high_degradation` | `degradation_rate > 0.4` | HIGH |
| `web_search_failure` | `url_yield < 0.3` | HIGH |
| `near_miss_pattern` | GT in candidates for >30% of misses but not rank 1 | MEDIUM |
| `plateau_signal` | 2+ consecutive rounds <1% improvement | MEDIUM |

### Pipeline Data Flow

```
Backend /matches → diagnostics.warnings[]
    ↓
_extract_pipeline_data()                    ← prompt_eval.py
    ↓
result["pipeline_data"]["diagnostics"]["warnings"]
    ↓
compute_pipeline_health()                   ← critique_stats.py
    ↓
detect_anomalies() → ANOMALY FLAGS          ← injected into critique meta-prompt
```

A query is **"degraded"** if it has any non-empty `warnings` list -- regardless of node name.

**Backend warning contract:**

```json
{"data": {"diagnostics": {"warnings": [
  {"step": "web_search", "code": "partial_scrape", "message": "3 of 14 fetched URLs returned content"}
]}}}
```

| Field | Purpose | Example |
|-------|---------|---------|
| `step` | Target pipeline node that emitted the warning | `"web_search"`, `"entity_profiling"` |
| `code` | Error classification | `"partial_scrape"`, `"timeout"` |
| `message` | Human-readable detail | `"3 of 14 fetched URLs returned content"` |

Classified as **`{step}:{code}`** (e.g., `web_search:partial_scrape`).

---

## Escalation Chain

When degradation persists across rounds:

```
degraded_rate >= threshold
    ↓
DegradationCheck fires mid-eval → ABORT remaining queries + candidates
    ↓
EscalationSignal(target="l2")    ← DEFAULT_STRATEGIES routes by {step}:{code}
    ↓
Escalation journal entry recorded (tried config, degradation rate)
    ↓
L2 Refine receives: critique + journal → updates task_context + meta-settings
    ↓
L1 Generate receives: focus_note (degradation data) + critique
    → candidates naturally target unstable node's parameters
    ↓
Retry → degradation rate drops? → continue or escalate again
```

Degradation rounds don't count toward `max_rounds` (hard cap: 100).

---

## Wiring a New Node

Reference: `web_search`. Default chain works for **any** target pipeline node that emits warnings.

| Step | What | Where | Required? |
|------|------|-------|-----------|
| **1** | Emit `diagnostics.warnings[]` with `{step, code, message}` | Backend | **Yes** |
| **2** | Add routing strategy for `{step}:{code}` | `escalation.py` | No (defaults to L2) |
| **3** | Add anomaly detector | `critique_stats.py` | No |
| **4** | Set `degradation_threshold` | campaign config | **Yes** (0 = disabled) |

Example -- adding `entity_profiling` error detection:

```json
{"step": "entity_profiling", "code": "schema_error", "message": "Failed to parse JSON"}
```

That's it. `DegradationCheck` counts it, critique shows `ANOMALY FLAGS`, escalation routes to L2, L1 focuses candidates on the failing node.

---

## Thinking Styles

Each round samples 2-3 styles from the variant library (`api/config/prompt_variants.json`, 35+ from published research) into the meta-prompt as mutation guidance. Structured diversity beyond temperature randomness.

## Scan-Aware Generation

When scan data is available, `prepare_scan_context()` enriches the meta-prompt with `scan_context` analytics and each candidate can include a `pipeline_params_override` for per-candidate target pipeline param exploration. See [Sensitivity Scan](sensitivity-scan.md) for scan workflow details.

SearchMemory *(M8 Wave 5)* provides historical parameter impact data so the scan advisor prioritizes axes that historically produced signal and suggests text values that historically worked. Each optimizer node (L1, L2, critique) queries SearchMemory's atomic accessors for the subset relevant to its decision.

## Optimizer Nodes (M7)

See [architecture.md](architecture.md#the-optimizer-pipeline) for the optimizer pipeline table.

The optimizer nodes are declared in [`api/config/optimizer_pipeline.json`](../api/config/optimizer_pipeline.json): `l1_generate` (`llm/meta`), `l1_evaluate` (`evaluation`), `critique` (`agent`), `l2_refine_context` (`llm/meta`), `l3_modify_plan` (`llm/meta`). Each node's config (temperature, prompt_family, context_sources) is loaded via `get_node_config()` and LLM calls use the shared `llm_call()` primitive (`api/config/optimizer_pipeline.py`). Node tracing uses `observed_node()`. `OptSearchPoint` checkpoints optimizer state (critique, thinking_styles, plan, `task_context`) per round.

**Critique and thinking styles are tools of `l1_evaluate`, not separate nodes.** The critique agent runs *within* the evaluation node -- its output (`critique_text`) feeds the *next* round's `l1_generate`. Similarly, `sample_thinking_styles()` runs at the end of evaluation to prepare mutation guidance for the next round. Neither has an independent parameter surface or routing decision that would warrant a separate optimizer pipeline node.

---

## Phase Events

The feedback cycle emits structured `PhaseEvent` objects at phase boundaries via the `on_phase` callback. The notebook renders these as ANSI-colored banners (`>>>` enter, `<<<` exit).

| Phase | Trigger | Key enter data | Key exit data |
|-------|---------|----------------|---------------|
| `init` | Cycle start | `max_rounds`, `patience`, `n_variants`, `model`, `sample_size`, `enable_l2`, `enable_l3`, `eval_data_count`, `baseline_accuracy`, `has_scan_context`, `enable_critique` | `cycle_id`, `resumed_from_round`, `baseline_accuracy`, `obs_enabled`, `sample_count`, `critique_text` (bootstrap) |
| `l1_generate` | Candidate generation | `current_accuracy`, `prompt_preview`, `n_variants`, `creativity`, `model`, `has_scan_context`, `has_critique` | `n_candidates`, `n_eval_queries`, `loaded_from_disk`, candidates list |
| `l1_evaluate` | Evaluation, winner selection & critique | `n_candidates`, `n_queries`, `current_best_accuracy`, `improvement_threshold` | `winner_label`, `winner_accuracy`, `winner_composite`, `improved`, `next_action`, `critique_text`, `critique_path` |
| `refine_context` | L2 escalation (when `enable_l2=True`) | `l2_round`, `stall_count`, `current_accuracy`, `best_accuracy` | `param_changes_count`, `context_changed`, `changes_description` |
| `modify_plan` | L3 escalation (when `enable_l3=True`) | `l3_round`, `l2_stall_count` | `new_plan_preview`, `changes_description` |
| `escalation` | `EscalationCheck` fires mid-eval | `check_name`, `target`, `context`, `candidate_idx` | (routed to L2/L3/abort) |

Each event: `phase` (str), `event` ("enter"/"exit"), `round` (int or None), `data` (dict), `timestamp` (ISO 8601).

---

## Configuration

```python
campaign_config = {
    "sample_size": 35,                 # queries per eval (0 = all)
    "exclude_nodes": ["llm_ranking"],  # target pipeline nodes to skip
    "optimization": {
        "n_variants": 5,
        "creativity": 0.7,
        "improvement_threshold": 0.01,
        "patience": 3,
        "max_rounds": 10,
        "enable_critique": True,               # critique-guided generation
        "degradation_threshold": 0.4,          # 0 = disabled
        "enable_l2": False,            # opt-in: refine task_context on L1 stall
        "l2_patience": 2,             # None = unlimited during degradation
        "enable_l3": False,            # opt-in: modify plan on L2 stall
        "l3_patience": 1,             # None = unlimited during degradation
        "escalation_checks": [         # pluggable mid-eval checks
            {"name": "degradation", "threshold": 0.3, "target": "l3"},
        ],
        "plan": None,                 # override optimizer strategy (str)
        "context": None,              # override domain task_context (str)
        "critique": None,             # override bootstrap critique (str)
    },
    "eval_llm": { ... },
}
```

---

## Troubleshooting

**Feedback cycle stalls at low accuracy** -- Lower `improvement_threshold`, increase `n_variants`, or manually escalate to Layer 2/3.

**Critique produces generic advice** -- The eval LLM may struggle with domain-specific failure analysis. Try a more capable model for `eval_llm.model`, or set `enable_critique: False` to fall back to direct generation with failure examples only.

**Candidates lack diversity** -- Thinking styles provide structured mutation guidance but the eval LLM may ignore them at low temperatures. Increase `creativity` (meta-prompt temperature) or increase `n_variants`.

**Sensitivity scan aborted early** -- Circuit breaker triggered. See [Sensitivity Scan: Circuit Breaker](sensitivity-scan.md#circuit-breaker).

---

## Key Files

| File | Role |
|------|------|
| `campaign/critique.py` | `CritiqueAgent`, `format_critique_for_prompt()`, pos/neg routing |
| `campaign/critique_stats.py` | Pure stat computation, anomaly detection, meta-prompt assembly |
| `campaign/escalation.py` | `DegradationCheck`, `DEFAULT_STRATEGIES`, `classify_warnings` |
| `campaign/optimization_loop.py` | Orchestration, escalation journal, critique threading |
| `campaign/layer_transitions.py` | L2 (`task_context` + meta-settings), L3 (plan) |
| `prompt_eval.py` | `_run_eval_batch` (per-query checks), `_extract_pipeline_data` |
| `l1_optimizer.py` | L1 generation (sole pipeline_params decider) |
| `search/search_memory.py` | Cross-campaign intelligence: parameter impact, query patterns, failure modes *(M8)* |
