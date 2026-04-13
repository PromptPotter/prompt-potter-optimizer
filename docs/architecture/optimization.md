# Optimization

```
┌─ ONE ROUND ────────────────────────────────────────────────────────────┐
│                                                                        │
│  L1 GENERATE (LLM)                                                     │
│    in:  critique OR l2_directive (mutual exclusion),                   │
│         task_context, thinking_styles, recon_brief, plan,            │
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

**Breadth over depth.** Failure analysis surfaces 2-3 distinct improvement directions ranked by failure count, not a single dominant fix. L1 receives all directions and generates candidates across them rather than N variations on one theme — this is the primary guard against mode collapse. The single `priority_fix` in critique output is one signal among several; `failure_highlights` and `suggested_axes` carry the breadth.

**Init** configures the pipeline; baseline evaluation is deferred and runs automatically when `optimize` starts. The first critique is bootstrapped from baseline results at that point. When scan data is available (leaderboard, axis sensitivity, query difficulty), it feeds into both the bootstrap critique and subsequent rounds via `prepare_recon_brief()`.

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

`_assemble_critique_sections()` in `critique.py` builds the stat sections from section helper functions. The critique template (`critique.json`) wraps these sections with persona, task_intent, and answer_format. Critique is the **sole reader** of raw eval results — all other nodes receive its digested output (see [`information-flow.md`](information-flow.md) consumer matrix).

Per-query diagnostics are derived from `PipelineSchema.nodes` via `NODE_TYPE_METRICS` (`pipeline_schema.py`) — a registry mapping node type → metrics → `pipeline_data_key`. Schema-driven, not hardcoded: when the pipeline gains a new node or node type, diagnostics extend automatically without code changes.

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

Backend `/matches` returns `diagnostics.warnings[]` per query. A query is **"degraded"** if it has any non-empty warnings list. Each warning carries `{step, code, message}` — classified as **`{step}:{code}`** (e.g., `web_search:partial_scrape`). Flow: `measure_sample()` projects `diagnostics` into `pipeline_data` → `_pipeline_health_section()` → anomaly flags in critique meta-prompt.

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

## OptimizationMemory state

`OptSearchPoint.memory` (an `OptimizationMemory` Pydantic submodel in `domain/opt_search_point.py`) bundles the cross-round optimizer state that travels with each candidate. Every field is persisted with the round trial JSON; lifecycles vary:

| Field | Lifecycle | Purpose |
|---|---|---|
| `critique_text` | per-round, cleared on improvement | Critique node's narrative for next L1 |
| `thinking_styles` | per-round | 2-3 strategy hints sampled into the meta-prompt |
| `escalation_journal` | cross-round, append-only | History of degradation events with outcomes |
| `warning_inventory` | cross-round | Per-query warning aggregation |
| `l2_directive` | one-round window, cleared on improvement | L2's diagnostic + action guidance for L1 |
| `degradation_reset_count` | cross-round | How many times L2/L3 patience exhausted |
| `backend_warning_emitted` | one-shot | Backend-warning emission flag |
| `validation_failures` | per-candidate (set at L1 parse time) | Parse-time invariant violations — see below |

---

## Self-healing optimization

When the optimizer proposes a structurally invalid candidate, the failure is recorded as a property of the outer-layer `OptSearchPoint` (**not** the `JobSearchPoint`), absorbed by L2 on the next round, and never spends a backend call. Structural mistakes are optimizer-layer state; L2 is the layer that already has the context to repair them.

Every self-healing mechanism follows one rail:

```
detect → trace on OptSearchPoint → score (synthetic 0) → surface → feed L2 → L2 directive → next L1
```

The rail is the contract. New mechanisms plug in by adding a `detect` step and reusing the rest — **do not invent a sidecar, do not silently drop, do not just log.**

### Validation failures as OptSearchPoint properties (first instance)

Some L1-generated candidates are invalid before evaluation starts — the optimizer LLM hallucinated a value outside the user-declared allowed set. Canonical example: `pipeline_params_override.llm_only.model = "gpt-4o"` when the backend only has `openai/gpt-oss-120b` per `PipelineSchema.available_models`.

`validate_overrides()` in `application/optimization/nodes/generate.py` attaches a `ValidationFailure(axis, value, allowed, reason)` (from `domain/analysis.py`) to `OptSearchPoint.memory.validation_failures` at L1 parse time. This is **outer-layer optimizer state** — it lives on the optimizer trace alongside critique_text, l2_directive, and escalation_journal. The target-layer `JobSearchPoint` is untouched, which is why none of the scoring-layer machinery needs to know about validation failures: `_score_candidates` shortcuts to a synthetic 0 report, the existing accuracy comparator naturally deprioritizes the candidate in `_select_round_winner`, and the round checkpoint persists the failure with the rest of the optimizer memory.

**L2 owns the repair.** L2 `refine_strategy` already reads critique, escalation reports, and the previous directive; validation failures slot into that same context as a new "L1 VALIDATION FAILURES" section. L2 produces a directive that names the disallowed value by name (e.g. *"do not propose gpt-4o for model"*), which replaces critique for next round's L1 via the normal directive/critique mutual exclusion. The outer-layer trace carries the evidence; L2 turns it into explicit forward guidance. Alternatives like silent-drop, deadlist sidecars, or post-hoc prompt injection all either lose the signal or require new machinery parallel to the trace.

For the prompt-injection routing (who reads `validation_failures`, when it overrides critique), see [`information-flow.md`](information-flow.md). For how the failure is rendered to the user, see [`display-conventions.md`](display-conventions.md).

### Relationship to the other per-evaluation checks

| Check | Fires | Action | Target enum |
|---|---|---|---|
| **Validation failure** | L1 parse time, before evaluation | Synthetic 0; skip backend | (no signal — handled in `_score_candidates` via `OptSearchPoint.memory.validation_failures`) |
| `EliminationCheck` | Mid-evaluation, after `n_min` queries | Stop scoring this candidate; continue with the next | `EscalationTarget.ELIMINATE_CANDIDATE` |
| `EmptyOutputCheck` | Mid-evaluation, after 3 queries | Stop scoring this candidate; continue with the next | `EscalationTarget.ELIMINATE_CANDIDATE` |
| `DegradationCheck` | Mid-evaluation, after 3 queries | Abort the round; escalate to L2 | `EscalationTarget.L2` |

Validation is the only one that fires *before* evaluation — it needs nothing but the candidate dict and the schema, which is why it can short-circuit the backend entirely.

### Planned future self-healing mechanisms

The same rail generalizes:

- **Empty-output candidates** — verbose prompts that blow `max_tokens` mid-reasoning. Detection shipped via `EmptyOutputCheck`; the L2-feedback half is the gap to close.
- **Monotonic per-axis degradation with fault attribution** — routes to L2 today, but without per-candidate fault attribution on the OptSearchPoint trace.
- **Backend errors naming a parameter and reason** — e.g. `temperature out of range`. Needs a structured-error parser at the backend client.
- **Schema/format violations on structured outputs** — valid JSON, wrong shape.

Each lands by following the rail, not by inventing parallel machinery. Today `validate_overrides()` only checks `model` against `PipelineSchema.available_models`; future enum params plug in by extending the validator — the `ValidationFailure` dataclass is intentionally axis-agnostic.

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

When scan data is available, `prepare_recon_brief()` enriches the meta-prompt with `recon_brief` analytics and each candidate can include a `pipeline_params_override` for per-candidate exploration. Keys matching `PROMPT_STRING_FIELDS` are auto-routed to `derive_candidate()` (updating prompt scheme fields), all other keys stay as node-level pipeline overrides. See [Sensitivity Scan](../specs/archive/sensitivity-scan.md) for scan workflow details.

**Per-axis pruning rule.** After each scan variant evaluates, Wilson CIs are computed against baseline. If CIs **fully overlap** for every variant tested on an axis, the axis is marked noise and remaining values are skipped — early *pruning*, not early stopping. Always test ≥2 values per axis before any axis can be pruned, otherwise a single anomalous reading would silence a real effect.

**Diagnostic-stratified sample selection.** `build_diagnostic_set()` stratifies misses by diagnostic pattern (`extract_sample_diagnostics()` output) so each distinct failure pattern is represented in the scan sample. Patterns are discovered from data, not hardcoded categories, so the stratification adapts to whatever pipeline is in front of it.

### SearchMemory Intelligence Feed

Cross-campaign intelligence loaded at cycle init, refreshed before each round. Each consumer receives a tailored subset via builder functions. See [`search-memory-intelligence.md`](search-memory-intelligence.md) for the full design, consumer matrix, and two-tier intelligence architecture.

### Stale Data Load Protocol

When a cached eval result is degraded (non-empty `diagnostics.warnings`), the protocol walks a 3-step ladder:

| Step | Action | Resolves when |
|------|--------|---------------|
| **rerun** | Re-evaluate after `rerun_trigger_count` observations | Fresh result not degraded |
| **samplescan** | Re-evaluate with default params | Default-config not degraded |
| **sampleswitch** | Check SearchMemory degradation rate | Rate exceeds threshold → exclude |

If all steps fail, result is marked `persistently_degraded` and passed through. Observation counts come from `SearchMemory.query_degradation_count`, which is populated at round boundaries by ingesting `dataset_runs/`.

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
