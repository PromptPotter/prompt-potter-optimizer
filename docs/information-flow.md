# Information Flow

Every node in the optimization loop is an LLM call with a prompt. This document maps **where data originates**, **who consumes it**, and **why** — not implementation wiring (which changes each refactor), but the information architecture that survives milestones.

**Core principle:** Each node sees only what IT needs. Upstream analysis must not leak raw data alongside its digest.

---

## Data Origins

Every piece of information entering an LLM node is created at one of these sources:

| Origin | What it produces | Lifecycle |
|--------|-----------------|-----------|
| **Backend eval** | Per-query: hit/miss, pipeline_data (candidates, timings, diagnostics, terminated_at) | Fresh each eval call |
| **Critique (LLM)** | Structured analysis: summary, priority_fix, suggested_axes, failure_highlights | Fresh each round (from winner's eval results) |
| **L2 Refine (LLM)** | Directive (guidance text), optimizer_params, task_context refinements, action | On stall (patience exhausted) |
| **L3 Plan (LLM)** | Strategic plan text, optional pipeline_params | On deep stall (L2 exhausted) |
| **Escalation journal** | Per-escalation: round, degraded_rate, problem_step, step_config, warning_types | Appended before each L2 call |
| **Warning inventory** | Per-query: warning counts, hit/miss stats, rounds_seen | Accumulated from ALL candidate results each round |
| **Scan context** | Sensitivity ranking, tested values per axis | From human-loop scan, passed to AI loop |
| **Config** | n_variants, creativity, sample_size, model | From campaign init or L2 override |

---

## Consumer Matrix

What each node receives and why. Blank = does not receive.

```
Data                  │ Critique      │ L1 Generate       │ L2 Refine         │ L3 Plan
──────────────────────┼───────────────┼───────────────────┼───────────────────┼────────────────
Eval results (raw)    │ ✓ sole reader │                   │                   │
                      │ (intelligence │                   │                   │
                      │  bridge)      │                   │                   │
──────────────────────┼───────────────┼───────────────────┼───────────────────┼────────────────
Critique text         │               │ ✓ when no         │ ✓ always          │
                      │               │   directive (a)   │   (builds on it)  │
──────────────────────┼───────────────┼───────────────────┼───────────────────┼────────────────
L2 directive          │               │ ✓ when set        │ ✓ prev only       │
                      │               │   (replaces       │   (evolves it)    │
                      │               │    critique) (a)  │                   │
──────────────────────┼───────────────┼───────────────────┼───────────────────┼────────────────
Escalation journal    │               │ ✓ probe only (b)  │ ✓ full history    │
──────────────────────┼───────────────┼───────────────────┼───────────────────┼────────────────
Warning inventory     │               │ ✓ probe only (b)  │ ✓ when no esc.    │
                      │               │                   │   report (c)      │
──────────────────────┼───────────────┼───────────────────┼───────────────────┼────────────────
Scan context          │               │ ✓ full r0,        │                   │
                      │               │   compact after   │                   │
──────────────────────┼───────────────┼───────────────────┼───────────────────┼────────────────
Task context          │               │ ✓ read-only       │ ✓ editable        │
──────────────────────┼───────────────┼───────────────────┼───────────────────┼────────────────
Thinking styles       │               │ ✓ sampled (3)     │                   │
──────────────────────┼───────────────┼───────────────────┼───────────────────┼────────────────
Plan                  │               │ ✓ read-only       │                   │ ✓ prev (editable)
──────────────────────┼───────────────┼───────────────────┼───────────────────┼────────────────
Optimizer params      │               │ via overrides     │ ✓ editable        │
──────────────────────┼───────────────┼───────────────────┼───────────────────┼────────────────
L2 history (summary)  │               │                   │                   │ ✓ last 3 rounds
──────────────────────┼───────────────┼───────────────────┼───────────────────┼────────────────
Round history         │ ✓ full        │                   │                   │
──────────────────────┼───────────────┼───────────────────┼───────────────────┼────────────────
Pipeline schema       │               │                   │ via escalation    │ ✓ param keys
                      │               │                   │ report only (d)   │
──────────────────────┼───────────────┼───────────────────┼───────────────────┼────────────────
Rendered prompt       │               │ ✓                 │                   │ ✓
```

**Legend:**

**(a) Directive/critique mutual exclusion.** L1 sees critique text OR l2_directive, never both. When L2 fires, it reads critique and produces a directive that digests it. L1 then sees only the directive — the critique's raw signal is superseded. (`formatting.py:117`)

**(b) Probe round exception.** Probe rounds are triggered by L2 (`action="probe"`) to target queries with recurring pipeline warnings. The per-query warning detail (warning_inventory + escalation_journal) IS the actionable data for probe targeting — the directive just says "probe." Non-probe rounds with a directive skip ESCALATION entirely. (`formatting.py:60-98`)

**(c) Escalation/warning mutual exclusion in L2.** L2 receives either the full escalation stability report (which includes aggregate warning counts) OR the per-query warning breakdown — never both. The stability report already contains what L2 needs for strategic decisions. (`formatting.py:155-160`)

**(d) L2 sees pipeline schema only via escalation.** L2 does not receive the full pipeline parameter listing — that's L1's domain. When escalation fires, the stability report surfaces the problem step's available parameters and tried configs. L2's job is meta-settings and directive, not pipeline_params selection.

---

## Design Decisions

### Critique as sole intelligence bridge

L1 never sees raw eval results (failure lists, pipeline_data). Critique digests them into a compact structured analysis (summary, priority_fix, axes, highlights). This prevents L1 from being overwhelmed by per-query noise and forces the system to identify patterns.

**Trade-off:** L1 is always ≥1 LLM hop from eval data. If critique misses a pattern, L1 can never see it. M8's multi-direction injection (§1c) mitigates this by surfacing 2-3 distinct improvement directions.

### Information compression chain

```
eval results ──► Critique (LLM) ──► critique_text ──► L2 (LLM) ──► l2_directive ──► L1 (LLM)
                 1st hop                               2nd hop
```

When L2 fires, L1 is 2 LLM hops from eval data. Each hop is lossy compression. The directive/critique mutual exclusion ensures L1 gets the most processed form available.

### L3 sees only outcomes, not reasoning

L3 receives `l2_summary`: the last 3 L2 rounds condensed to `{l2_round, optimizer_params, accuracy_change}`. L3 never sees L2's directive or rationale — only what was changed and whether accuracy moved. This is intentional: L3 plans strategy from outcomes, not from L2's tactical reasoning.

### Journal written before L2

The escalation journal entry is appended BEFORE L2 runs. L2 sees the current degradation event alongside history. The entry records the config that CAUSED degradation (pre-L2 state), which is the correct attribution.

---

## M8/M9 Evolution

> Planned — not yet implemented.

### M8: Campaign Intelligence

M8 §1 introduces a proper data layer between eval results and LLM nodes:

- **Level A** (`extract_sample_diagnostics`) — per-query signal extraction derived from PipelineSchema nodes. Replaces ad-hoc `pipeline_data` reading in critique.
- **Level B** (`compile_failure_analysis`, `compile_query_difficulty`, `compile_temporal_trends`) — aggregate analysis across all samples. Replaces hardcoded failure formatting.
- **Tailored context per consumer** — each node gets a freshly compiled view from Level A/B, not accumulated `opt_sp` fields. The `format_context_sections` / `format_l2_intelligence` pattern evolves into per-consumer formatters that read from the data layer.
- **Multi-direction injection** — Level B surfaces 2-3 distinct improvement directions (not just critique's single recommendation), mitigating the sole-intelligence-bridge compression loss.
- **SearchMemory** — cross-campaign materialized view feeding both loops. Adds parameter impact, query tractability, and historical best values as new data origins.

### M9: Multi-Connector Architecture

M9 abstracts the backend eval path:

- **ConnectorProtocol** replaces `BackendClient` — eval result schema becomes connector-dependent.
- Level A's `extract_sample_diagnostics()` already uses PipelineSchema (schema-driven), so it adapts to any connector's result format.
- The "Backend eval" data origin row in the consumer matrix stays the same; the implementation behind it becomes polymorphic.

---

## Implementation Reference

All optimizer nodes follow: JSON template (`api/config/optimizer_prompts/`) → `load_optimizer_prompt()` → `compile_prompt()` → `llm_call()`. Each template decomposes into the 8-field prompt scheme (see [`prompt-scheme.md`](prompt-scheme.md)).

The consumer matrix above is the durable reference. Template `{{variable}}` wiring changes each milestone — read the template JSON files for current details.

| Node | Template | Caller | Key note |
|------|----------|--------|----------|
| L1 Generate | `meta_scan_aware.json` | `l1_optimizer.py:l1_generate()` | `format_context_sections()` assembles all L1 context |
| Eval | — | `prompt_eval.py:eval_search_point()` | `run_match()` translates to wire format |
| Critique | `critique.json` | `critique.py:CritiqueAgent.run()` | `assemble_critique_sections()` builds stat sections |
| L2 Refine | `l2_refine_context.json` | `layer_transitions.py:refine_context()` | Does NOT see rendered prompt or full pipeline params |
| L3 Plan | `l3_modify_plan.json` | `layer_transitions.py:modify_plan()` | Sees only outcomes (l2_summary), not L2 reasoning |
