# Information Flow — Optimization Loop

What gets injected into each LLM node's prompt, where it originates, and how it gets there.

**Reading the tables:** Each cell describes the path `origin → retention → access mode`. Blank = not injected into that node's prompt. Retention is how data survives between production and consumption:

| Retention | Meaning |
|-----------|---------|
| `opt_sp.*` | Persisted on `OptSearchPoint` — survives across rounds in campaign trial JSON |
| `LoopState.*` | Transient within one optimization cycle |
| `config.*` | Immutable within cycle (set at campaign init or scan phase) |
| `SearchMemory` | Cross-campaign materialized view, queried at format time |
| *(ephemeral)* | Computed and consumed within a single round, not stored |

---

## Prompt Injection Map

### Persistent state (OptSearchPoint)

| Injection | Origin → Retention | L1 Generate | Critique | L2 Refine | L3 Plan |
|-----------|--------------------|-------------|----------|-----------|---------|
| **Rendered prompt** | `opt_sp.render()` from 8 prompt scheme fields | full | — | — | full |
| **Critique text** | `CritiqueAgent.run()` → `opt_sp.critique_text` | when no L2 directive **(a)** | — | always (builds on it) | — |
| **L2 directive** | `refine_context()` → `opt_sp.l2_directive` | replaces critique when set **(a)** | — | prev only (evolves it) | — |
| **Escalation journal** | appended pre-L2 → `opt_sp.escalation_journal` | probe only **(b)** | — | full history | — |
| **Warning inventory** | `update_query_tracker()` → `opt_sp.warning_inventory` | probe only **(b)** | — | when no esc. report **(c)** | — |
| **Task context** | campaign init / L2 override → `opt_sp.task_context` | read-only | — | editable | — |
| **Thinking styles** | `sample_thinking_styles()` → `opt_sp.thinking_styles` | 3 sampled | — | — | — |
| **Plan** | L3 output → `opt_sp.plan` | read-only | — | — | prev (editable) |
| **Optimizer params** | campaign init / L2 override → `opt_sp.optimizer_params` | via overrides | — | editable | — |

### Per-round / transient

| Injection | Origin → Retention | L1 Generate | Critique | L2 Refine | L3 Plan |
|-----------|--------------------|-------------|----------|-----------|---------|
| **Eval results (raw)** | `eval_search_point()` → *(ephemeral)* | — | **sole reader** (intelligence bridge) | — | — |
| **Round history** | accumulated → `LoopState.rounds` | — | full (accuracy trajectory, param changes) | — | — |
| **L2 history** | computed from `l2_history` → *(ephemeral)* | — | — | — | last 3 rounds (`{round, params, acc_change}`) |
| **Failure analysis** | `compile_failure_analysis()` → `LoopState.failure_analysis` | top patterns | — | — | — |

### Config (immutable within cycle)

| Injection | Origin → Retention | L1 Generate | Critique | L2 Refine | L3 Plan |
|-----------|--------------------|-------------|----------|-----------|---------|
| **Scan context** | sensitivity scan → `config.scan_context` | full on r0, compact after | — | — | — |
| **Pipeline schema** | `GET /pipeline` → `config.pipeline_schema` | — | `candidate_keys` derived | via escalation report only **(d)** | param keys per node |

### SearchMemory (cross-campaign)

Each consumer queries a **tailored subset** via a dedicated builder. Critique is the every-round intelligence hub; L2 fires on escalation only; L3 receives the aggregate strategic picture.

| Method | L1 Generate | Critique | L2 Refine | L3 Plan |
|--------|-------------|----------|-----------|---------|
| `failure_clusters()` | `build_l1_search_memory_context()` | `build_critique_search_memory_context()` | — | `build_strategic_search_memory_context(..., include_clusters=True)` |
| `dead_queries()` | `build_l1_search_memory_context()` | — | — | — |
| `axis_rankings()` | `build_l1_search_memory_context()` (top 3) | `build_critique_search_memory_context()` (top 3) | `build_strategic_search_memory_context()` (top 5) | `build_strategic_search_memory_context()` (top 5) |
| `top_k_values()` | `build_l1_search_memory_context()` | — | — | — |
| `discriminating_queries()` | — | `build_critique_search_memory_context()` | — | — |
| `bottleneck_distribution()` | — | — | `build_strategic_search_memory_context()` | `build_strategic_search_memory_context()` |
| `persistent_failures()` | — | `build_critique_search_memory_context()` (tractability) | `build_strategic_search_memory_context()` | `build_strategic_search_memory_context()` |
| `exhausted_axes()` | — | `build_critique_search_memory_context()` | — | — |
| `axis_value_trend()` | — | `build_critique_search_memory_context()` | — | — |
| `intractable_queries_ci()` | — | — | — | — |
| `parameter_failure_correlation()` | — | — | `build_strategic_search_memory_context(..., include_correlations=True)` | — |

### Planned Intelligence Extensions

By design, L1 stays clean — it generates candidates. Deeper sample intelligence is handled in two tiers: deterministic code triage (no LLM) and L2 strategic intelligence.

| Item | Tier | Target | Status |
|------|------|--------|--------|
| **Failure streak triage** | Deterministic | Code — `persistent_failures()` pre-filters eval set | Done |
| **Intractable query CI gating** | Deterministic | Code — `intractable_queries_ci()` confidence-bounded exclusion | Done |
| **Round trajectory** | Strategic | L2 Refine — `build_round_trajectory()` | Done |
| **Candidate comparison** | Strategic | L2 Refine — `build_candidate_comparison()` | Done |
| **Failure group × axis** | Strategic | L2 Refine — `parameter_failure_correlation()` | Done (scan-only producer; periodic refresh planned) |
| **L3 SearchMemory intelligence** | Strategic | L3 Plan — `build_strategic_search_memory_context(..., include_clusters=True)` | Done |
| **Critique tractability profiles** | Every-round | Critique — intractable/chronic/intermittent classification | Done |
| **Axis exhaustion detection** | Every-round | Critique — `exhausted_axes()` | Done |
| **Value momentum/direction** | Every-round | Critique — `axis_value_trend()` | Done |
| **Diminishing returns detector** | Both | Critique (anomaly flag) + L2 (strategic context) | Planned |
| **Candidate diversity monitor** | Strategic | L2 — detect mode collapse in candidate generation | Planned |
| **Query improvement attribution** | Both | Critique (this-round) + L2 (cross-round patterns) | Planned |
| **Cross-candidate failure diff** | Every-round | Critique — missed opportunities from non-winner candidates | Planned |
| **Failure group refresh in loop** | Strategic | L2 — periodic recomputation during optimization | Planned |

See [`docs/methods/search-memory-intelligence.md`](../research/search-memory-intelligence.md) for the full design.

### Internal (not a prompt injection)

**Stale data observations** — accumulated during eval → `opt_sp` → aggregated cross-campaign by SearchMemory. Consumed by `l1_evaluate`'s stale data protocol (`sampleswitch` queries SearchMemory's per-query degradation rate). Never enters an LLM prompt.

---

## Conditional Rules

**(a) Directive / critique mutual exclusion.** L1 sees critique text OR l2_directive, never both. When L2 fires, it reads critique and produces a directive that digests it. L1 then sees only the directive. (`formatting.py:117`)

**(b) Probe round exception.** Probe rounds (L2 `action="probe"`) pass warning_inventory + escalation_journal to L1 even when a directive exists. Non-probe rounds with a directive skip escalation data entirely. The per-query warning detail IS the actionable data for probe targeting. (`formatting.py:60-98`)

**(c) Escalation / warning mutual exclusion in L2.** L2 receives either the full escalation stability report OR the per-query warning breakdown — never both. The stability report already contains aggregate warning counts. (`formatting.py:155-160`)

**(d) L2 sees pipeline schema only via escalation.** L2 does not receive the full pipeline parameter listing — that's L1's domain. When escalation fires, the stability report surfaces the problem step's available parameters and tried configs.

---

## Compression Chain

```
eval results ──► Critique (LLM) ──► critique_text ──► L2 (LLM) ──► l2_directive ──► L1 (LLM)
                 1st hop                               2nd hop
```

When L2 fires, L1 is 2 LLM hops from eval data. Each hop is lossy compression. The directive/critique mutual exclusion ensures L1 gets the most processed form available. Round trajectory and failure group insights will feed L2 directly (bypassing this chain), so L2 can produce better-informed directives for L1.

L3 sees only `l2_summary` (last 3 L2 outcomes: what changed, whether accuracy moved) — never L2's directive or reasoning. Strategy from outcomes, not tactics.
