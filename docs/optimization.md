# Optimization

```
┌─ ONE ROUND ────────────────────────────────────────────────────────────┐
│                                                                        │
│  L1 GENERATE (LLM)                                                     │
│    in:  critique OR l2_directive (mutual exclusion),                   │
│         task_context, thinking_styles, scan_context, plan,            │
│         escalation_journal + warning_inventory (probe rounds only),   │
│         search_memory (param impact, query patterns, failure modes)   │
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
│    ┌─ CRITIQUE (LLM) — sole intelligence bridge ───────────────────┐  │
│    │  in:  pipeline_health, rank_analysis, round_evolution,        │  │
│    │       query_categories, failure_details, successes            │  │
│    │                                                                │  │
│    │  out: { summary, priority_fix, suggested_axes,                │  │
│    │         failure_highlights, positive/negative_critique }       │  │
│    │  (compact form via format_critique_for_prompt: summary,       │  │
│    │   priority_fix, axes, highlights — internal fields omitted)   │  │
│    └────────────────────────────────────────────────────────────────┘  │
│         ↓                                                              │
│  Compact critique → next L1 Generate (or L2 Refine on escalation)     │
│                                                                        │
│  ── ESCALATION (if degradation detected) ──────────────────────────── │
│                                                                        │
│  L2 REFINE CONTEXT (LLM) — meta-controller                            │
│    in:  critique, prev l2_directive, escalation report                 │
│         (OR warning_inventory when no report), task_context,           │
│         pipeline schema param keys                                     │
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

`render()` assembles prompt fields into the final rendered prompt. `derive_candidate()` creates child points forming a lineage chain.

### Dynamic Field Set (Design Vision)

The field set is currently fixed (8 fields in `PROMPT_STRING_FIELDS` + `few_shot_examples` + `plan`). The vision is to make it **open**: L2 should be able to add fields (e.g., `domain_constraints`, `negative_examples`) or remove zero-impact fields.

```
L2 REFINE ──► add_field("domain_constraints")
              remove_field("persona")
                    │
                    ▼
┌─ PROMPT SCHEME ──────────────────────────┐
│  1. task_intent                          │
│  2. problem_description                  │
│  3. instruction                          │
│  4. thinking_style                       │
│  5. answer_format                        │
│  6. domain_constraints          ← NEW    │
│                                          │
│  +/- [???]                               │
└──────────────────────────────────────────┘
```

**Why it works architecturally:** `render()` already skips empty fields (removal = set to `""`). `derive_candidate()` and `prompt_field_dict()` iterate a field list — making it dynamic is the key change. An overflow `dict[str, str]` handles additions without new Pydantic attributes.

**Benefit:** L2 field mutations widen or narrow the search space. A prompt with 4 fields searches a fundamentally different space than one with 8 fields.

**Open questions:** How does the variant library adapt? Should scan test dynamic fields? Field ordering for new fields? Persistence through `OptSearchPoint` → `JobSearchPoint` → disk?

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

Both positive and negative paths produce the same 6-field JSON:

```json
{
  "positive_critique": "what's working — patterns to extend",
  "negative_critique": "what's failing — root causes and blockers",
  "priority_fix": "single most impactful change to make",
  "suggested_axes": ["query_prefix", "max_sites"],
  "failure_highlights": ["Q→Pred→GT lines (3-5 most actionable)"],
  "summary": "2-3 sentence actionable critique"
}
```

Formatted by `format_critique_for_prompt()` (emits only actionable fields: summary, priority_fix, suggested_axes, failure_highlights — positive/negative_critique stays internal). Injected into:
- **L1 Generate** as `critique_text` — only when no `l2_directive` (mutual exclusion)
- **L2 Refine** as `critique_text` in intelligence sections (always, so L2 can build on it)

### Stat-Rich Analysis

`assemble_critique_sections()` in `critique.py` builds the stat sections from section helper functions. The critique template (`critique.json`) wraps these sections with persona, task_intent, and answer_format. Critique is the **sole reader** of raw eval results — all other nodes receive its digested output (see [`information-flow.md`](information-flow.md) consumer matrix).

| Section | Source |
|---------|--------|
| **Evaluation summary** | `CritiqueContext` + `LoopState` |
| **Anomaly flags** | Computed inline from health/rank/evolution |
| **Pipeline health** | `winner_results.pipeline_data` |
| **Rank analysis** | `winner_results` + `candidate_keys` from schema |
| **Round evolution** | `state.rounds` (`CycleRoundResult` history) |
| **Query categories** | `winner_results.terminated_at` |
| **Failure details** | `winner_results` (8 max, deduped) |
| **Successes** | `winner_results` (2 examples) |
| **Search memory** *(M8 — planned)* | `SearchMemory` atomic accessors |

### Anomaly Flags

Computed inline from the health, rank, and evolution sections:

| Flag | Fires when | Severity |
|------|-----------|----------|
| `high_degradation` | Degraded query count exceeds threshold | HIGH |
| `near_miss_pattern` | GT in candidates for >30% of misses but not rank 1 | MEDIUM |
| `plateau_signal` | 2+ consecutive rounds with <1% improvement | MEDIUM |

### Pipeline Data Flow

Backend `/matches` returns `diagnostics.warnings[]` per query. A query is **"degraded"** if it has any non-empty warnings list. Each warning carries `{step, code, message}` — classified as **`{step}:{code}`** (e.g., `web_search:partial_scrape`).

Flow: `_parse_backend_response()` (prompt_eval.py) → `_pipeline_health_section()` (critique.py) → anomaly flags in critique meta-prompt.

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
Escalation journal entry recorded BEFORE L2 (tried config, degradation rate)
    ↓
L2 Refine receives: critique + journal + escalation report
    → updates task_context + meta-settings + produces l2_directive
    ↓
L1 Generate receives: l2_directive (replaces critique)
    → candidates naturally target unstable node's parameters
    (probe rounds also receive warning_inventory for per-query targeting)
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

### SearchMemory Integration (M8 — Planned)

Cross-campaign intelligence feeding the optimization loop. See [architecture.md § SearchMemory](architecture.md#searchmemory-m8-wave-3) for the full data model, consumer matrix, and bottleneck attribution.

Loaded at init, refreshed incrementally each round via watermark. Each node queries the subset it needs: L1 gets failure clusters + historically-best values, L2 gets axis rankings + bottleneck distribution, Critique gets discriminating queries. Round 3+ uses `adapt_eval_set()` to swap always-hit/always-miss queries for discriminating ones.

## Optimizer Nodes

See [architecture.md § Optimizer Pipeline](architecture.md#optimizer-pipeline) for the node table and flow diagram. Nodes declared in [`api/config/optimizer_pipeline.json`](../api/config/optimizer_pipeline.json), LLM calls via `llm_call()` primitive.

**Critique and thinking styles are tools of `l1_evaluate`, not separate nodes.** Critique runs *within* evaluation; its output feeds the *next* round's `l1_generate`. `sample_thinking_styles()` similarly prepares mutation guidance at evaluation end.

---

## Phase Events

The feedback cycle emits `PhaseEvent` objects at phase boundaries via `on_phase`. The notebook renders these as ANSI-colored banners.

| Phase | Trigger |
|-------|---------|
| `init` | Cycle start |
| `l1_generate` | Candidate generation |
| `l1_evaluate` | Evaluation, winner selection & critique |
| `refine_context` | L2 escalation |
| `modify_plan` | L3 escalation |
| `escalation` | `EscalationCheck` fires mid-eval |

Each event: `phase`, `event` ("enter"/"exit"), `round`, `data` (dict), `timestamp` (ISO 8601). See `CycleCallbacks` for the callback interface.

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
| `prompt_eval.py` | `_run_eval_batch` (per-query checks), `_parse_backend_response` |
| `l1_optimizer.py` | L1 generation (sole pipeline_params decider) |
| `search/search_memory.py` | Cross-campaign intelligence (M8 Wave 3): parameter impact, query patterns, failure modes |
