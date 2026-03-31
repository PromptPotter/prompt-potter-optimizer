# Information Flow

Every node in the optimization loop is an LLM call with a prompt. This document maps what goes **into** and **out of** each prompt, and what the target backend sees.

**Design principle:** Each node receives only the data it needs. Critique digests raw eval data into structured findings; downstream nodes (L1, L2) consume critique's output rather than re-analyzing raw data.

---

## The Loop — All Nodes

```
┌─ ONE ROUND ─────────────────────────────────────────────────────────────────────┐
│                                                                                 │
│  ┌─ L1_GENERATE (LLM) ──────────────────────────────────────────────────────┐  │
│  │  prompt template: meta_scan_aware.json                                    │  │
│  │                                                                           │  │
│  │  PROMPT INPUTS:                                                           │  │
│  │    {{rendered_prompt}}    ◄── opt_sp.render() (6 fields assembled)        │  │
│  │    {{failure_examples}}   ◄── Q/Pred/GT lines + warning annotations      │  │
│  │    {{context_sections}}   ◄── intelligence bundle (formatting.py):       │  │
│  │                               SCAN ANALYTICS — tested values + sensitivity│  │
│  │                               TASK CONTEXT — domain fields (if non-empty) │  │
│  │                               ESCALATION — journal + warnings (if any)    │  │
│  │                               L2 DIRECTIVE — from L2 (if L2 ran)          │  │
│  │                               CRITIQUE — only when l2_directive absent    │  │
│  │                               THINKING STYLES — sampled (if available)    │  │
│  │                               STRATEGIC GUIDANCE — plan (if L3 ran)       │  │
│  │    {{accuracy_pct}}       ◄── "85.3%"                                    │  │
│  │    {{n_variants}}         ◄── how many candidates to generate            │  │
│  │    {{n_queries}}          ◄── eval dataset size                          │  │
│  │    {{instruction_spec}}   ◄── JSON field spec for "instruction"          │  │
│  │                                                                           │  │
│  │  OUTPUT:                                                                  │  │
│  │    variants[]:                                                            │  │
│  │      .instruction              → new prompt template text                │  │
│  │      .changes_description      → rationale                               │  │
│  │      .pipeline_params_override → node param overrides (optional)         │  │
│  │      .target_axis              → primary axis being explored             │  │
│  │      .reasoning                → why this variant is promising            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│         │                                                                       │
│         │  per candidate:                                                       │
│         │    derive_candidate(**fields) ──► child OptSearchPoint               │
│         │    to_job_search_point()      ──► frozen JobSearchPoint              │
│         ▼                                                                       │
│  ┌─ EVAL — POST /matches (per candidate × per query) ───────────────────────┐  │
│  │                                                                           │  │
│  │  REQUEST (JobSearchPoint → wire):                                        │  │
│  │    {                                                                      │  │
│  │      "query": "...",                                                      │  │
│  │      "steps": [...],                          ◄── pipeline_params        │  │
│  │      "node_config": {                                                     │  │
│  │        "<prompt_node>": {                                                │  │
│  │          "prompt": "<<< rendered prompt >>>"  ◄── render()               │  │
│  │        },                                                                 │  │
│  │        "<node>": { ... }                      ◄── overrides              │  │
│  │      }                                                                    │  │
│  │    }                                                                      │  │
│  │                                                                           │  │
│  │  RESPONSE:                                                                │  │
│  │    ranked_candidates[]   → .candidate, .score, .key_match_factors        │  │
│  │    step_timings          → per-node latency                              │  │
│  │    diagnostics.warnings  → pipeline warnings                             │  │
│  │    terminated_at         → which node ended the pipeline                 │  │
│  │                                                                           │  │
│  │  EVAL OUTPUT (per query):                                                │  │
│  │    hit:    ranked[0].candidate == ground_truth?                           │  │
│  │    score:  evaluator confidence                                          │  │
│  │                                                                           │  │
│  │  AGGREGATED:                                                              │  │
│  │    accuracy      = hits / total                                          │  │
│  │    composite     = compute_composite_score(results, pipeline_schema)      │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│         │                                                                       │
│         │  _select_round_winner()                                              │
│         │    best composite >= current + improvement_threshold                 │
│         ▼                                                                       │
│  ┌─ CRITIQUE (LLM) ─────────────────────────────────────────────────────────┐  │
│  │  prompt: assembled in critique.py (not a template file)                   │  │
│  │                                                                           │  │
│  │  PROMPT INPUTS:                                                           │  │
│  │    EVALUATION SUMMARY     ◄── accuracy, composite, degraded_count,       │  │
│  │                               round_num, stall_count, best_accuracy      │  │
│  │    ANOMALY FLAGS          ◄── [HIGH] / [MEDIUM] flags                    │  │
│  │    PIPELINE HEALTH        ◄── termination distribution, step             │  │
│  │                               degradation%, timing p50/p90, error rate   │  │
│  │    RANK ANALYSIS          ◄── rank buckets (1, 2-5, 6-10, 11-20,        │  │
│  │                               not_found), top-k recall, near-miss        │  │
│  │    ROUND EVOLUTION        ◄── accuracy trajectory, param changes         │  │
│  │    QUERY CATEGORIES       ◄── failures grouped by termination step       │  │
│  │    FAILURE DETAILS        ◄── per-query: Q/Pred/GT + rank + degradation  │  │
│  │    SUCCESS DETAILS        ◄── per-query: what worked                     │  │
│  │                                                                           │  │
│  │  OUTPUT:                                                                  │  │
│  │    positive_critique  → patterns to extend                               │  │
│  │    negative_critique  → root causes and blockers                         │  │
│  │    priority_fix       → highest-impact change                            │  │
│  │    suggested_axes     → ["param or field to try next", ...]              │  │
│  │    summary            → 2-3 sentence actionable for next L1              │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│         │                                                                       │
│         │  update_round_state()                                                │
│         │    winner prompt fields ──► OptSearchPoint (setattr per field)       │
│         │    critique output     ──► opt_sp.critique_text                      │
│         │    rebuild JobSearchPoint via to_job_search_point()                  │
│         ▼                                                                       │
│  ── NEXT ROUND (or escalation if stalled) ──────────────────────────────────── │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Escalation Nodes (on stall)

When L1 stalls (patience rounds without improvement), L2/L3 fire before the next L1 round. They **never modify prompt fields** — they adjust context and meta-settings that influence L1's generation.

### L2 REFINE CONTEXT

```
prompt template: l2_refine_context.json

PROMPT INPUTS:
  {{rendered_prompt}}            ◄── opt_sp.render()
  {{current_params}}             ◄── optimizer_params JSON (creativity, n_variants, ...)
  {{task_context_section}}       ◄── domain fields (optional)
  {{pipeline_section}}           ◄── available params per node from schema
  {{intelligence_sections}}      ◄── intelligence bundle (formatting.py):
                                     ESCALATION — stability report + warnings
                                     CRITIQUE — previous critique_text
                                     PREV DIRECTIVE — previous l2_directive
  {{response_schema_suffix}}     ◄── expected JSON format

OUTPUT:
  optimizer_params   → {creativity, n_variants, sample_size}  (merged)
  task_context       → {domain, pipeline_purpose, goals, ...} (merged)
  action             → "continue" | "probe"
  directive          → 2-3 sentence guidance ──► next L1's {{context_sections}}
  rationale          → explanation
```

### L3 MODIFY PLAN

```
prompt template: l3_modify_plan.json

PROMPT INPUTS:
  {{current_plan}}               ◄── opt_sp.plan or "(none)"
  {{l2_summary}}                 ◄── last 3 L2 entries: params + acc_change
  {{rendered_prompt}}            ◄── opt_sp.render()
  {{pipeline_section}}           ◄── available params per node
  {{response_schema_suffix}}     ◄── expected JSON format

OUTPUT:
  plan               → new strategy text ──► next L1's {{context_sections}}
  pipeline_params    → node param overrides (optional)
  rationale          → explanation
```

---

## Key Files

| Node | File | Line |
|------|------|------|
| L1 Generate | `api/services/l1_optimizer.py` | `l1_generate():68` |
| Eval gateway | `api/services/prompt_eval.py` | `eval_search_point():452` |
| Backend wire | `api/services/backend_client.py` | `run_match():229` |
| Critique | `api/services/campaign/critique.py` | `CritiqueAgent.run():528` |
| L2 Refine | `api/services/campaign/layer_transitions.py` | `refine_context():174` |
| L3 Plan | `api/services/campaign/layer_transitions.py` | `modify_plan():205` |
| Prompt templates | `api/config/optimizer_prompts/` | `meta_scan_aware.json`, `l2_*.json`, `l3_*.json` |
