# Optimization

```
┌─ ONE ROUND ────────────────────────────────────────────────────────────┐
│                                                                        │
│  L1 GENERATE (LLM)                                                     │
│    in:  critique OR l2_directive (mutual exclusion),                   │
│         task_context, thinking_styles, scan_brief, plan,            │
│         escalation_journal + warning_inventory (probe rounds only),   │
│         failure_analysis (clustered failure patterns + signals),      │
│         search_memory (failure clusters, top axes, dead queries)      │
│    out: N candidate OptSearchPoints (prompt + pipeline_params)         │
│         ↓                                                              │
│  L1 EVALUATE                                                           │
│    ┌─ Backend /matches ──── per candidate × per query ──────────────┐  │
│    │  in:  query + pipeline_params (per-node overrides)             │  │
│    │  out: ranked_candidates + diagnostics.warnings                 │  │
│    │                                                                │  │
│    │  Stale data protocol (cached degraded queries):                │  │
│    │    rerun → samplescan → sampleswitch (3-step ladder)           │  │
│    │                                                                │  │
│    │  DegradationCheck (per-query):                                 │  │
│    │    degraded_rate >= 0.4? → ABORT + EscalationSignal            │  │
│    └────────────────────────────────────────────────────────────────┘  │
│    Winner selection: best accuracy >= baseline + threshold             │
│         ↓                                                              │
│    ┌─ CRITIQUE (LLM) — every-round intelligence hub ───────────────┐  │
│    │  in:  pipeline_health, rank_analysis, round_evolution,        │  │
│    │       query_categories, failure_details, successes,           │  │
│    │       search_memory (failure clusters, discriminating queries, │  │
│    │         tractability profiles, axis exhaustion, value trends)  │  │
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
│  L2 REFINE CONTEXT (LLM) — escalation-only meta-controller             │
│    in:  critique, prev l2_directive, escalation report                 │
│         (OR warning_inventory when no report), task_context,           │
│         pipeline schema param keys,                                    │
│         round trajectory, failure group × axis insights,              │
│         candidate comparison summary                                   │
│    out: updated task_context + meta-settings (creativity,              │
│         n_variants, sp_budget_ttest)                                  │
│    L2 does NOT set pipeline_params — that's L1's job.                  │
│                                                                        │
│  L3 MODIFY PLAN (LLM) — if L2 stalls                                  │
│    in:  current plan, L2 history, rendered prompt, pipeline section,   │
│         search_memory (axis rankings, bottleneck dist, failure         │
│         clusters, persistent failures)                                 │
│    out: new strategic plan                                             │
└────────────────────────────────────────────────────────────────────────┘
```

## Responsibility Matrix

| Agent | Fires when | Decides | Does NOT decide |
|-------|-----------|---------|-----------------|
| **L1 Generate** | Every round | pipeline_params (query_prefix, max_sites, schema, temperature, ...) | `task_context`, meta-settings |
| **Critique** | Every round | what to focus on (suggested_axes, priority_fix) | pipeline_params values |
| **L2 Refine** | Escalation only (stall, degradation) | `task_context`, meta-settings (creativity, n_variants, sp_budget_ttest), `l2_directive` | pipeline_params |
| **L3 Plan** | L2 stalls | strategic plan | pipeline_params, `task_context` |

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
| Pipeline params | Non-LLM nodes (`fuzzy_matching`, `token_matching`) | thresholds, weights, `sp_budget_ttest` |

Which parameters are Layer 1 depends on the target pipeline config — not a fixed list. Prompt fields only affect nodes with a prompt template referencing them (see CLAUDE.md Known Issues for backend-specific constraints). The scan advisor reads the full pipeline snapshot to recommend which axes to optimize.

### Layer 2: Refine Context

Adjusted when Layer 1 improvements stall:

| Field | Purpose |
|-------|---------|
| `optimizer_params` | Meta-settings (creativity, n_variants, sp_budget_ttest, variant_strategy) |
| `task_context` | Structured domain context (domain, pipeline_purpose, data_characteristics, optimization_goals, key_challenges, raw_description). Decomposed from `TASK_DESCRIPTION` at init. L2 can refine individual fields. |

### Layer 3: Modify Plan

Optimization strategy -- rarely changed:

| Field | Purpose |
|-------|---------|
| `plan` | High-level optimization strategy |

`render()` assembles prompt fields into the final rendered prompt. `derive_candidate()` creates child points forming a lineage chain.

### Dynamic Field Set (Design Vision)

Currently fixed (8 fields in `PROMPT_STRING_FIELDS` + `few_shot_examples` + `plan`). Vision: make it open — L2 adds/removes fields to widen or narrow the search space.

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

Architecturally feasible: `render()` already skips empty fields, `derive_candidate()` iterates a field list. An overflow `dict[str, str]` handles additions without new Pydantic attributes.

---

## Feedback Cycle

Critique-guided optimization with 3-layer escalation, inspired by [PromptWizard](https://arxiv.org/abs/2405.18369)'s critique-and-refine pattern.

```
INIT: configure pipeline (baseline deferred to first optimize round) → bootstrap critique + sample thinking styles
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

**Init** configures the pipeline; baseline evaluation is deferred and runs automatically when `optimize` starts. The first critique is bootstrapped from baseline results at that point. When scan data is available (leaderboard, axis sensitivity, query difficulty), it feeds into both the bootstrap critique and subsequent rounds via `prepare_scan_brief()`.

---

## Critique Agent

Failure analysis is **separated from candidate generation** (PromptWizard pattern). Critique is the **every-round intelligence hub** — it runs every round inside L1 Evaluate after backend evaluation and winner selection. It is the **sole reader** of raw eval results AND receives SearchMemory intelligence (failure clusters, discriminating queries, tractability profiles, axis exhaustion, value trends) to frame its analysis. Its output feeds forward to **L1 Generate** (next round) and **L2 Refine** (on escalation). L2 only fires on escalation — critique is the normal-flow intelligence bridge.

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
| **Round evolution** | `state.rounds` (`RoundResult` history) |
| **Query categories** | `winner_results.terminated_at` |
| **Failure details** | `winner_results` (8 max, deduped) |
| **Successes** | `winner_results` (2 examples) |
| **Search memory** *(M8)* | `SearchMemory` atomic accessors: failure clusters, discriminating queries, tractability profiles, axis exhaustion, value trends |

### Anomaly Flags

Computed inline from the health, rank, and evolution sections:

| Flag | Fires when | Severity |
|------|-----------|----------|
| `high_degradation` | Degraded query count exceeds threshold | HIGH |
| `near_miss_pattern` | GT in candidates for >30% of misses but not rank 1 | MEDIUM |
| `plateau_signal` | 2+ consecutive rounds with <1% improvement | MEDIUM |

### Pipeline Data Flow

Backend `/matches` returns `diagnostics.warnings[]` per query. A query is **"degraded"** if it has any non-empty warnings list. Each warning carries `{step, code, message}` — classified as **`{step}:{code}`** (e.g., `web_search:partial_scrape`). Flow: `_parse_backend_response()` → `_pipeline_health_section()` → anomaly flags in critique meta-prompt.

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
| **3** | Add anomaly detector | `critique.py` | No |
| **4** | Set `degradation_threshold` | campaign config | **Yes** (0 = disabled) |

Example -- adding `entity_profiling` error detection:

```json
{"step": "entity_profiling", "code": "schema_error", "message": "Failed to parse JSON"}
```

That's it. `DegradationCheck` counts it, critique shows `ANOMALY FLAGS`, escalation routes to L2, L1 focuses candidates on the failing node.

---

## Thinking Styles

Each round samples 2-3 styles from the variant library (`promptpotter/config/prompt_variants.json`, 35+ from published research) into the meta-prompt as mutation guidance. Structured diversity beyond temperature randomness.

## Scan-Aware Generation

When scan data is available, `prepare_scan_brief()` enriches the meta-prompt with `scan_brief` analytics and each candidate can include a `pipeline_params_override` for per-candidate exploration. Keys matching `PROMPT_STRING_FIELDS` are auto-routed to `derive_candidate()` (updating prompt scheme fields), all other keys stay as node-level pipeline overrides. See [Sensitivity Scan](../specs/archive/sensitivity-scan.md) for scan workflow details.

### SearchMemory Intelligence Feed

Cross-campaign intelligence loaded at cycle init, refreshed before each round. Each consumer receives a tailored subset via builder functions. See [`docs/research/search-memory-intelligence.md`](../research/search-memory-intelligence.md) for the full design, consumer matrix, and two-tier intelligence architecture.

### Stale Data Load Protocol

When a cached eval result is degraded (non-empty `diagnostics.warnings`), the protocol walks a 3-step ladder:

| Step | Action | Resolves when |
|------|--------|---------------|
| **rerun** | Re-evaluate after `rerun_trigger_count` observations | Fresh result not degraded |
| **samplescan** | Re-evaluate with default params | Default-config not degraded |
| **sampleswitch** | Check SearchMemory degradation rate | Rate exceeds threshold → exclude |

If all steps fail, result is marked `persistently_degraded` and passed through. Observation counts persisted on `OptSearchPoint.stale_data_observations`.

---

## Phase Events

The feedback cycle emits `PhaseEvent` objects at phase boundaries via `on_phase`. The notebook renders these as ANSI-colored banners.

| Phase | Trigger |
|-------|---------|
| `init` | Cycle start |
| `l1_generate` | Candidate generation |
| `l1_evaluate` | Evaluation, winner selection & critique |
| `refine_strategy` | L2 escalation |
| `modify_plan` | L3 escalation |
| `escalation` | `EscalationCheck` fires mid-eval |

Each event: `phase`, `event` ("enter"/"exit"), `round`, `data` (dict), `timestamp` (ISO 8601). See `RunCallbacks` for the callback interface.

---

## Configuration

```python
campaign_config = {
    "sp_budget_ttest": 35,                    # queries per eval (must be > 0)
    "exclude_nodes": ["llm_ranking"],          # target pipeline nodes to skip
    "optimization": {
        "n_variants": 5,
        "creativity": 0.7,
        "improvement_threshold": 0.01,
        "l1_patience": 3,
        "max_rounds": 10,
        "hard_cap": 100,                       # absolute round limit (safety)
        "max_consecutive_errors": 3,           # abort eval after N backend errors
        "enable_critique": True,               # critique-guided generation
        "degradation_threshold": 0.4,          # 0 = disabled
        "critique_degradation_threshold": 0.4, # critique anomaly flag threshold
        "critique_near_miss_ratio": 0.3,       # GT-in-candidates ratio for near-miss flag
        "enable_l2": True,                     # refine task_context on L1 stall
        "l2_patience": 2,                      # None = unlimited during degradation
        "enable_l3": True,                     # modify plan on L2 stall
        "l3_patience": 1,                      # None = unlimited during degradation
        "stale_data_load_protocol": ["rerun", "samplescan", "sampleswitch"],
        "plan": None,                          # override optimizer strategy (str)
        "context": None,                       # override domain task_context (str)
        "critique": None,                      # override bootstrap critique (str)
    },
    "eval_llm": { ... },
}
```

---

## Troubleshooting

- **Stalls at low accuracy** — Lower `improvement_threshold`, increase `n_variants`, or manually escalate to L2/L3.
- **Generic critique** — Try a more capable `eval_llm.model`, or `enable_critique: False` for direct generation.
- **Low diversity** — Increase `creativity` or `n_variants`.
- **Scan aborted early** — Circuit breaker. See [sensitivity-scan.md](../specs/archive/sensitivity-scan.md#circuit-breaker).

---

## Key Files

| File | Role |
|------|------|
| `campaign/nodes/critique.py` | `CritiqueAgent`, `format_critique_for_prompt()`, pos/neg routing, stat computation |
| `campaign/nodes/escalation.py` | `DegradationCheck`, `DEFAULT_STRATEGIES`, `classify_warnings` |
| `campaign/runner.py` | Orchestration, escalation journal, critique threading |
| `campaign/nodes/layer_transitions.py` | L2 (`task_context` + meta-settings), L3 (plan) |
| `campaign/nodes/generate.py` | L1 generation (sole pipeline_params decider) |
| `campaign/nodes/score.py` | L1 scoring, winner selection, composite score |
| `scoring/search_point_scorer.py` | `score_search_point()` gateway, batch orchestration |
| `scoring/sample_measurement.py` | Per-query measurement, backend response parsing |
| `search/search_memory.py` | Cross-campaign intelligence (M8 Wave 3): parameter impact, query patterns, failure modes |
