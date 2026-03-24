# Critique Agent

## Where It Runs

The critique agent runs **inside L1 Evaluate**, after backend evaluation and winner selection. Its output feeds forward to **L1 Generate** (next round) and **L2 Refine** (on escalation).

```
┌─ ONE ROUND ────────────────────────────────────────────────────────────┐
│                                                                        │
│  L1 GENERATE (LLM)                                                     │
│    in:  critique (5 fields), task_context, thinking_styles,            │
│         scan_context, focus_note, failure_examples                     │
│    out: N candidate SearchPoints (prompt + pipeline_params)            │
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
| **L1 Generate** | pipeline_params (query_prefix, max_sites, schema, temperature, ...) | context, meta-settings |
| **Critique** | what to focus on (suggested_axes, priority_fix) | pipeline_params values |
| **L2 Refine** | context, meta-settings (creativity, n_variants, sample_size), task_context | pipeline_params |
| **L3 Plan** | strategic plan | pipeline_params, context |

---

## Critique Output

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

---

## Positive / Negative Routing

| Path | When | Focus |
|------|------|-------|
| Positive | `accuracy >= 0.7` | Success examples + remaining failures. Extend what works. |
| Negative | `accuracy < 0.7` | Rich pipeline stats + failure examples. Diagnose root causes. |

Threshold: `critique_positive_threshold` in campaign config.

---

## Pre-Computed Stats (Negative Path)

`assemble_critique_prompt()` builds the prompt from pure functions in `critique_stats.py`:

| Section | Key metrics | Source |
|---------|------------|--------|
| **Evaluation summary** | accuracy, composite, degraded count, stalls | `CritiqueContext` |
| **Anomaly flags** | `high_degradation`, `web_search_failure`, `near_miss_pattern`, `plateau_signal` | `detect_anomalies()` |
| **Pipeline health** | `web_search_degradation_rate`, `url_yield`, timing p50/p90/max | `compute_pipeline_health()` |
| **Rank analysis** | rank buckets, top-k recall, near misses | `compute_rank_analysis()` |
| **Round evolution** | accuracy trajectory, degraded trend | `compute_round_evolution()` |
| **Query categories** | failures by termination step, blindspot terms | `compute_query_categories()` |
| **Scan context** | leaderboard, improving axes | From `CritiqueContext.scan_context` |

### Anomaly Flags

| Flag | Fires when | Severity |
|------|-----------|----------|
| `high_degradation` | `degradation_rate > 0.4` | HIGH |
| `web_search_failure` | `url_yield < 0.3` | HIGH |
| `near_miss_pattern` | GT in candidates for >30% of misses but not rank 1 | MEDIUM |
| `plateau_signal` | 2+ consecutive rounds <1% improvement | MEDIUM |

---

## Pipeline Data Flow

```
Backend /matches → diagnostics.warnings[]
    ↓
_extract_pipeline_data()                    ← prompt_eval.py
    ↓
result["pipeline_data"]["diagnostics"]["warnings"]
    ↓
compute_pipeline_health()                   ← critique_stats.py
    ↓
detect_anomalies() → ANOMALY FLAGS          ← injected into critique prompt
```

A query is **"degraded"** if it has any non-empty `warnings` list — regardless of step name.

### Backend Warning Contract

```json
{"data": {"diagnostics": {"warnings": [
  {"step": "web_search", "code": "partial_scrape", "message": "3 of 14 fetched URLs returned content"}
]}}}
```

| Field | Purpose | Example |
|-------|---------|---------|
| `step` | Pipeline node that emitted the warning | `"web_search"`, `"entity_profiling"` |
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
L2 Refine receives: critique + journal → updates context + meta-settings
    ↓
L1 Generate receives: focus_note (degradation data) + critique
    → candidates naturally target unstable step's parameters
    ↓
Retry → degradation rate drops? → continue or escalate again
```

Degradation rounds don't count toward `max_rounds` (hard cap: 100).

---

## Wiring a New Node

Reference: `web_search`. Default chain works for **any** step that emits warnings.

| Step | What | Where | Required? |
|------|------|-------|-----------|
| **1** | Emit `diagnostics.warnings[]` with `{step, code, message}` | Backend | **Yes** |
| **2** | Add routing strategy for `{step}:{code}` | `escalation.py` | No (defaults to L2) |
| **3** | Add anomaly detector | `critique_stats.py` | No |
| **4** | Set `degradation_threshold` | campaign config | **Yes** (0 = disabled) |

Example — adding `entity_profiling` error detection:

```json
{"step": "entity_profiling", "code": "schema_error", "message": "Failed to parse JSON"}
```

That's it. `DegradationCheck` counts it, critique shows `ANOMALY FLAGS`, escalation routes to L2, L1 focuses candidates on the failing step.

---

## Configuration

```python
"optimization": {
    "enable_critique": True,
    "critique_positive_threshold": 0.7,
    "degradation_threshold": 0.4,    # 0 = disabled
    "enable_l2": True,
    "enable_l3": True,
    "l2_patience": None,             # None = unlimited during degradation
    "l3_patience": None,
}
```

---

## Key Files

| File | Role |
|------|------|
| `campaign/critique.py` | `CritiqueAgent`, `format_critique_for_prompt()`, pos/neg routing |
| `campaign/critique_stats.py` | Pure stat computation, anomaly detection, prompt assembly |
| `campaign/escalation.py` | `DegradationCheck`, `DEFAULT_STRATEGIES`, `classify_warnings` |
| `campaign/feedback_cycle.py` | Orchestration, escalation journal, critique threading |
| `campaign/layer_transitions.py` | L2 (context + meta-settings), L3 (plan) |
| `prompt_eval.py` | `evaluate_prompt_batch` (per-query checks), `_extract_pipeline_data` |
| `l1_optimizer.py` | L1 generation (sole pipeline_params decider) |
