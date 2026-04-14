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
| **L2 directive** | `refine_strategy()` → `opt_sp.l2_directive` | replaces critique when set **(a)** | — | prev only (evolves it) | — |
| **Escalation journal** | appended pre-L2 → `opt_sp.escalation_journal` | probe only **(b)** | — | full history | — |
| **Warning inventory** | `update_query_tracker()` → `opt_sp.warning_inventory` | probe only **(b)** | — | when no esc. report **(c)** | — |
| **Task context** | campaign init / L2 override → `opt_sp.task_context` | read-only | — | editable | — |
| **Thinking styles** | `sample_thinking_styles()` → `opt_sp.thinking_styles` | 3 sampled | — | — | — |
| **Plan** | L3 output → `opt_sp.plan` | read-only | — | — | prev (editable) |
| **Optimizer params** | campaign init / L2 override → `opt_sp.optimizer_params` | via overrides | — | editable | — |
| **Validation failures** | L1 parse-time `validate_overrides()` → `opt_sp.memory.validation_failures` (per-candidate only) | — | — | "L1 VALIDATION FAILURES" section when non-empty **(e)** — rail 1, L2 teaches L1 via directive | — |
| **Runtime failures (new)** | `DegradationCheck` mid-eval → `_score_candidates` synthesises `RuntimeFailure` → attached to **that candidate's** `memory.runtime_failures`, surfaced on `candidate_scores[*].runtime_failures` | — | — | "NEW this round" partition (from `candidate_scores`) **(f)** — rail 2, L2 adjusts its OWN strategy | — |
| **Runtime failures (accumulated)** | End of round: `execute_round` mirrors every new `RuntimeFailure` onto **outer** `state.opt_sp.memory.runtime_failures`, deduped by `(source, dominant_warning, observed_config)`; persists across rounds (not cleared on improvement); deep-copied forward by `LoopState.apply_transition` | — | — | "ACCUMULATED surviving from earlier rounds" partition **(f)** — tells L2 its prior strategy adjustment didn't work | `runtime_failures_section` — the **L2→L3 escalation trail**; L3 treats these as discovered constraints and must replan to escape them |

### Per-round / transient

| Injection | Origin → Retention | L1 Generate | Critique | L2 Refine | L3 Plan |
|-----------|--------------------|-------------|----------|-----------|---------|
| **Eval results (raw)** | `score_search_point()` → *(ephemeral)* | — | **sole reader** (intelligence bridge) | — | — |
| **Round history** | accumulated → `LoopState.rounds` | — | full (accuracy trajectory, param changes) | — | — |
| **L2 history** | computed from `l2_history` → *(ephemeral)* | — | — | — | last 3 rounds (`{round, params, acc_change}`) |
| **Failure analysis** | `compile_failure_analysis()` → `LoopState.failure_analysis` | top patterns | — | — | — |

### Config (immutable within cycle)

| Injection | Origin → Retention | L1 Generate | Critique | L2 Refine | L3 Plan |
|-----------|--------------------|-------------|----------|-----------|---------|
| **Scan context** *(optional)* | sensitivity scan → `config.recon_brief` — **the only sanctioned bridge from `scan/` into `optimization/`**. Absent when the scan is skipped. | full on r0, compact after | — | — | — |
| **Pipeline schema** | `GET /pipeline` → `config.pipeline_schema` | — | `candidate_keys` derived | via escalation report only **(d)** | param keys per node |

### SearchMemory (cross-campaign)

Each consumer queries a **tailored subset** via a dedicated builder. Critique is the every-round intelligence hub; L2 fires on escalation only; L3 receives the aggregate strategic picture.

| Method | L1 Generate | Critique | L2 Refine | L3 Plan |
|--------|-------------|----------|-----------|---------|
| `failure_clusters()` | `build_l1_search_memory_digest()` | `build_critique_search_memory_digest()` | — | `build_strategic_search_memory_digest(..., include_clusters=True)` |
| `dead_queries()` | `build_l1_search_memory_digest()` | — | — | — |
| `axis_rankings()` | `build_l1_search_memory_digest()` (top 3) | `build_critique_search_memory_digest()` (top 3) | `build_strategic_search_memory_digest()` (top 5) | `build_strategic_search_memory_digest()` (top 5) |
| `top_k_values()` | `build_l1_search_memory_digest()` | — | — | — |
| `discriminating_queries()` | — | `build_critique_search_memory_digest()` | — | — |
| `bottleneck_distribution()` | — | — | `build_strategic_search_memory_digest()` | `build_strategic_search_memory_digest()` |
| `persistent_failures()` | — | `build_critique_search_memory_digest()` (tractability) | `build_strategic_search_memory_digest()` | `build_strategic_search_memory_digest()` |
| `exhausted_axes()` | — | `build_critique_search_memory_digest()` | — | — |
| `axis_value_trend()` | — | `build_critique_search_memory_digest()` | — | — |
| `intractable_queries_ci()` | — | — | — | — |
| `parameter_failure_correlation()` | — | — | `build_strategic_search_memory_digest(..., include_correlations=True)` | — |

### Planned Intelligence Extensions

By design, L1 stays clean — it generates candidates. Deeper sample intelligence is handled in two tiers: deterministic code triage (no LLM) and L2 strategic intelligence.

| Item | Tier | Target | Status |
|------|------|--------|--------|
| **Failure streak triage** | Deterministic | Code — `persistent_failures()` pre-filters eval set | Done |
| **Intractable query CI gating** | Deterministic | Code — `intractable_queries_ci()` confidence-bounded exclusion | Done |
| **Round trajectory** | Strategic | L2 Refine — `build_round_trajectory()` | Done |
| **Candidate comparison** | Strategic | L2 Refine — `build_candidate_comparison()` | Done |
| **Failure group × axis** | Strategic | L2 Refine — `parameter_failure_correlation()` | Done (scan-only producer; periodic refresh planned) |
| **L3 SearchMemory intelligence** | Strategic | L3 Plan — `build_strategic_search_memory_digest(..., include_clusters=True)` | Done |
| **Critique tractability profiles** | Every-round | Critique — intractable/chronic/intermittent classification | Done |
| **Axis exhaustion detection** | Every-round | Critique — `exhausted_axes()` | Done |
| **Value momentum/direction** | Every-round | Critique — `axis_value_trend()` | Done |
| **Diminishing returns detector** | Both | Critique (anomaly flag) + L2 (strategic context) | Planned |
| **Candidate diversity monitor** | Strategic | L2 — detect mode collapse in candidate generation | Planned |
| **Query improvement attribution** | Both | Critique (this-round) + L2 (cross-round patterns) | Planned |
| **Cross-candidate failure diff** | Every-round | Critique — missed opportunities from non-winner candidates | Planned |
| **Failure group refresh in loop** | Strategic | L2 — periodic recomputation during optimization | Planned |

See [`search-memory-intelligence.md`](search-memory-intelligence.md) for the full design.

### Internal (not a prompt injection)

**Stale data observations** — accumulated during eval → `opt_sp` → aggregated cross-campaign by SearchMemory. Consumed by `l1_evaluate`'s stale data protocol (`sampleswitch` queries SearchMemory's per-query degradation rate). Never enters an LLM prompt.

---

## Conditional Rules

**(a) Directive / critique mutual exclusion.** L1 sees critique text OR l2_directive, never both. When L2 fires, it reads critique and produces a directive that digests it. L1 then sees only the directive. (`nodes/formatting.py`)

**(b) Probe round exception.** Probe rounds (L2 `action="probe"`) pass warning_inventory + escalation_journal to L1 even when a directive exists. Non-probe rounds with a directive skip escalation data entirely. The per-query warning detail IS the actionable data for probe targeting. (`nodes/formatting.py`)

**(c) Escalation / warning mutual exclusion in L2.** L2 receives either the full escalation stability report OR the per-query warning breakdown — never both. The stability report already contains aggregate warning counts. (`nodes/formatting.py`)

**(d) L2 sees pipeline schema only via escalation.** L2 does not receive the full pipeline parameter listing — that's L1's domain. When escalation fires, the stability report surfaces the problem step's available parameters and tried configs.

**(e) Validation failures as L1 self-healing input (rail 1).** When a candidate's `OptSearchPoint.memory.validation_failures` is non-empty, the candidate skips the backend (synthetic 0) and the failure is fed to L2 `refine_strategy` as an explicit section alongside critique and escalation context. L2 teaches L1: it produces a directive that names the disallowed value by name. L1 never sees the raw failure — it sees the resulting directive on the next round via the normal directive/critique mutual exclusion (rule **(a)**) and heals itself. The signal lives on the outer-layer optimizer trace, not the target-layer `JobSearchPoint`. See [`optimization.md`](optimization.md) "Self-healing optimization — two rails".

**(f) Runtime failures as L2 self-healing input + L3 escalation trail (rail 2).** When `DegradationCheck` fires mid-evaluation with `degraded_rate ≥ threshold`, `_score_candidates` synthesises a `RuntimeFailure` from the check result + the candidate's observed pipeline config and attaches it to **that single candidate's** `OptSearchPoint.memory.runtime_failures`. The candidate is eliminated; the round winner is unaffected. `execute_round` then mirrors every new `RuntimeFailure` onto the **outer** `state.opt_sp.memory.runtime_failures` (deduped across rounds) so the trail persists. L2 `refine_strategy` next round receives two partitions: `NEW (this round)` from `candidate_scores` and `ACCUMULATED (surviving earlier rounds)` from outer memory. L2's healing action is to update its **own** outputs — tighten its directive, refine `task_context`, adjust `optimizer_params` — to re-shape what L1 is allowed to search over. This is **not** the "teach L1 not to propose X" pattern from rail 1; L2 is the healer here. When the `ACCUMULATED` list keeps growing across L2 rounds, `modify_plan` receives a `runtime_failures_section` built from the cumulative outer-memory trail and must replan the pipeline itself — change `pipeline_params`, swap nodes, or rewrite `plan` text — to escape the failing region. The two rails differ in **who the teacher is** and **what the healing action looks like**: rail 1 is L1 self-healing via L2 directive; rail 2 is L2 self-healing with L3 escalation.

---

## Compression Chain

```
eval results ──► Critique (LLM) ──► critique_text ──► L2 (LLM) ──► l2_directive ──► L1 (LLM)
                 1st hop                               2nd hop
```

When L2 fires, L1 is 2 LLM hops from eval data. Each hop is lossy compression. The directive/critique mutual exclusion ensures L1 gets the most processed form available. Round trajectory and failure group insights will feed L2 directly (bypassing this chain), so L2 can produce better-informed directives for L1.

Validation failures bypass critique entirely and feed L2 directly (1 hop instead of 2) — the signal is already structured, no eval-result digestion needed.

L3 sees only `l2_summary` (last 3 L2 outcomes: what changed, whether accuracy moved) — never L2's directive or reasoning. Strategy from outcomes, not tactics.

---

## Self-Optimization (Meta-Level)

The potter's own prompts are themselves `PromptTemplate` instances — the 8-field decomposition applies recursively, which is what enables a future self-optimization mode. The trace dataset (`potter_traces` loader) freezes archived campaign transitions into `{round_context → next_directive → score_delta}` rows that an outer-loop PromptPotter instance can score meta-prompt variants against. See [prompt-scheme.md § Optimizer Meta-Prompts](prompt-scheme.md#optimizer-meta-prompts) and [§ Potter Trace Dataset](prompt-scheme.md#potter-trace-dataset).
