# Information Flow — Optimization Loop

Every optimizer LLM prompt (L1, L2, Critique, L3) receives an `{{inbox}}`
block assembled from one declarative catalogue:
[`promptpotter/application/optimization/nodes/inbox_registry.py`](../../promptpotter/application/optimization/nodes/inbox_registry.py).

One registry. One function (`assemble_inbox`). One table below. Replaces
the four bespoke formatters + three scattered mutex `if/elif` blocks
that used to live in `formatting.py`.

## How to read

- **Source** — where the raw value lives. `opt_sp.memory.*` survives
  across rounds (carried through `OptSearchPoint.derive_candidate` +
  `LoopState.adopt_transition` via `memory.model_copy(deep=True)`);
  `ctx.*` is a per-round computed value carried only on the
  `OptimizerStateView`; `SearchMemory` is the cross-campaign aggregate.
  `LoopState` no longer carries per-round optimizer *content* — only
  the heavy transient `RoundResult` buffer plus orchestration caches
  (current/best, escalation counters, probe flag, pending decisions).
- **Retention** — `memory` (checkpointed on `OptSearchPoint.memory`),
  `opt_sp` (checkpointed on `OptSearchPoint` top-level), `transient`
  (computed per-round, not stored), `config` (immutable in-cycle),
  `search_memory` (cross-campaign).

## State ownership

Three state concepts, flat hierarchy:

| Concept | What it holds | Lifetime | Persisted |
|---------|---------------|----------|-----------|
| `OptSearchPoint` (opt_sp) | prompt fields + `task_context` + `plan`; `opt_sp.memory` carries `critique_text`, `l2_directive`, `thinking_styles`, `escalation_journal`, `warning_inventory`, `runtime_failures`, `validation_failures`, `failure_analysis`, `round_history`, … | per-cycle, mutable | `campaigns/{cycle_id}/trial_NNNN.json` |
| `SearchMemory` | cross-cycle aggregates (axis impact, sample_index, failure clusters) | cross-cycle, watermarked | `library/search_memory.json` + `library/sample_index.json` |
| `LoopEnv` + narrowed `LoopState` | infra handles (store, scoring_ctx, pipeline_schema) on `LoopEnv`; orchestration state (full `RoundResult` buffer, best/current cache, escalation counters, probe flag, pending decisions) on `LoopState` | per-session | reconstructed on resume from opt_sp + trial JSON |

Every L1/L2 prompt reads this triad through a single view:
`OptimizerStateView` (defined in `inbox_registry.py`). Writes land on
the natural owner; reads route through one catalogue.
- **L1 / L2** — section label shown in that layer's prompt, or `—`
  when the field is not consumed by that layer.
- **Mutex** — ``(group, priority)``; fields sharing a group produce
  only the highest-priority populated section.

## L1 / L2 inbox (from registry)

Row order is the section order inside each layer's assembled inbox.
Rows with `—` in a layer column are skipped when rendering that layer.

| Field | Source | Retention | L1 | L2 | Mutex |
|-------|--------|-----------|----|----|-------|
| `pipeline_schema_text` | precomputed in `l1_generate` | config | _(raw — no header)_ | — | |
| `failure_analysis` | `opt_sp.memory.failure_analysis` | memory | `FAILURE ANALYSIS ...` | — | |
| `search_memory_l1` | `SearchMemory.digest({failure_clusters, dead_queries, top_axes, top_values})` | search_memory | `HISTORICAL INTELLIGENCE:` | — | |
| `task_context` | `opt_sp.task_context` | opt_sp | `CONTEXT:` | — | |
| `escalation_probe` | `opt_sp.memory.escalation_journal` (probe-round only) | memory | _probe-round block_ | — | |
| `escalation_alert` | `opt_sp.memory.escalation_journal` (non-probe, no directive) | memory | `PIPELINE ISSUE: ...` | — | |
| `l2_directive` | `opt_sp.memory.l2_directive` | memory | `DIRECTIVE:` | `PREVIOUS DIRECTIVE:` | `(L1, guidance, 2)` |
| `critique_text` | `opt_sp.memory.critique_text` | memory | `CRITIQUE:` | `CRITIQUE:` | `(L1, guidance, 1)` |
| `thinking_styles` | `opt_sp.memory.thinking_styles` | memory | `THINKING STYLES:` | — | |
| `plan` | `opt_sp.plan` | opt_sp | `PLAN:` | — | |
| `escalation_section` | `ctx.escalation_check_result` + `opt_sp.memory.escalation_journal` | transient | — | `PIPELINE STABILITY REPORT ...` | |
| `warning_inventory` | `opt_sp.memory.warning_inventory` (L2 fallback when no escalation) | memory | — | `## RECURRING PIPELINE WARNINGS ...` | |
| `trajectory` | `build_trajectory_report(opt_sp.memory.round_history)` | memory | — | `CAMPAIGN TRAJECTORY:` | |
| `candidate_comparison` | `build_candidate_comparison(candidate_scores)` | transient | — | `LAST ROUND CANDIDATES:` | |
| `diversity_alert` | `assess_candidate_diversity(opt_sp.memory.round_history)` | memory | — | `DIVERSITY ALERT:` | |
| `validation_failures` | `candidate_scores[*].validation_failures` | transient | — | `L1 VALIDATION FAILURES ...` | |
| `runtime_failures` | `opt_sp.memory.runtime_failures` | memory | — | `RUNTIME FAILURES — L2 SELF-HEALING ...` | |
| `search_memory_l2` | `SearchMemory.digest({axis_rankings, bottleneck_distribution, failure_group_insights, persistent_failures, volatile_queries}, include_correlations=True)` | search_memory | — | `HISTORICAL INTELLIGENCE:` | |

### Mutex rules (the three rules hiding in prose before)

- **L1 `guidance`.** `l2_directive` wins over `critique_text` on L1 —
  when L2 fires it digests the critique into a directive, so the
  directive absorbs the signal. `clear_volatile()` wipes `l2_directive`
  on improvement; `critique_text` regenerates every round.
- **L2 `escalation_section` vs `warning_inventory`.** L2's fallback rule
  implemented by the `warning_inventory` source: it returns `None`
  whenever `escalation_section`'s source produces content. Same single
  slot, two sources, escalation wins.
- **L1 `escalation_probe` vs `escalation_alert`.** Both source from
  `opt_sp.memory.escalation_journal` but their sources gate on
  `ctx.is_probe_round` — probe returns the per-query block in probe
  rounds only; alert returns the aggregated banner only when NOT a
  probe AND no `l2_directive` is active.

## Critique inbox

Critique keeps its own assembler (in
[`critique.py`](../../promptpotter/application/optimization/nodes/critique.py))
because its sections share cross-cutting state (`anomalies` accumulator,
near-miss-query set passed between `_rank_analysis_section` and
`_failure_details_section`). Each section is a private helper:

| Section | Source |
|---------|--------|
| `## SCORING SUMMARY` | `RoundSnapshot.{accuracy, composite, degraded_queries, current_round, l1_stall_count, best_accuracy, best_round}` |
| `## PIPELINE HEALTH` | `RoundSnapshot.results` termination distribution |
| `## RUNTIME FAILURES THIS ROUND` | `RoundSnapshot.runtime_failures` |
| `## CANDIDATE RANK ANALYSIS` | `RoundSnapshot.{results, candidate_keys}` |
| `## ROUND EVOLUTION` | `RoundSnapshot.round_history` |
| `## QUERY CATEGORIES` | `RoundSnapshot.results` by termination step |
| `## FAILURE DETAILS` | `RoundSnapshot.{results, candidate_keys, pipeline_schema}` |
| `## SUCCESSES` | `RoundSnapshot.results` filtered to hits |
| `## ANOMALY FLAGS` | accumulated across sections above |
| `## HISTORICAL INTELLIGENCE` | `SearchMemory.digest({discriminating_queries, failure_clusters, tractability, exhausted_axes, value_trends, improvement_attribution})` |
| `## THIS ROUND` | `RoundSnapshot.round_analysis` (trajectory + cross-candidate diff) |
| `## AVAILABLE SCHEMA MUTATIONS` | pipeline-schema nodes with mutable `output_schema` |

Critique is the only layer with raw `QueryResult` access — it's the
every-round intelligence hub. Its output lands on
`opt_sp.memory.critique_text` and then flows into L1/L2's `critique_text`
inbox field.

## L3 — multi-hole template

L3 fires when L2 stalls and owns the strategic plan. Its prompt keeps
four non-inbox holes for context anchoring:

| Hole | Source |
|------|--------|
| `{{current_plan}}` | `opt_sp.plan` |
| `{{l2_summary}}` | last 3 rounds via `l2_history` (built from `LoopState.rounds` in `escalation.py`) |
| `{{rendered_prompt}}` | `opt_sp.render()` |
| `{{pipeline_section}}` | `format_pipeline_section(pipeline_params, schema)` |
| `{{runtime_failures_section}}` | `format_runtime_failures_for_l3(opt_sp.memory.runtime_failures)` |
| `{{inbox}}` | `SearchMemory.digest({axis_rankings, bottleneck_distribution, failure_clusters, persistent_failures}, include_clusters=True)` |

## Self-healing rails

Two independent flows use the above inbox:

**Rail 1 — Validation failures.** L1 parse-time invariant check
(`validate_overrides()`) finds a proposed value outside the allowed
set. The candidate short-circuits to synthetic 0; the failure lands on
`opt_sp.memory.validation_failures`. L2's next round receives it in the
`validation_failures` inbox field and produces a directive that names
the disallowed value. L1 heals on the following round via the
`guidance` mutex — the directive absorbs the critique channel.

**Rail 2 — Runtime failures.** `DegradationCheck` fires mid-evaluation
when `degraded_rate ≥ threshold`. `_score_candidates()` synthesises a
`RuntimeFailure` (with `first_seen_round` + `candidate_label`) attached
to the failing candidate's `memory.runtime_failures`. End-of-round,
`execute_round` mirrors new failures onto the outer
`state.opt_sp.memory.runtime_failures` — deduped by
`(source, dominant_warning, observed_config)`. L2 reads the mirror via
the `runtime_failures` inbox field, partitioned at format time into
NEW (this round) vs ACCUMULATED (surviving earlier rounds). L2 heals
itself by adjusting directive / task_context / optimizer_params. When
ACCUMULATED patterns persist across L2 rounds, L3 receives the trail
via `{{runtime_failures_section}}` and must replan the pipeline itself.

The rails differ in **who heals** and **what the healing action is**:
Rail 1 is L1 self-healing via L2 directive; Rail 2 is L2 self-healing
with L3 escalation.

## Compression chain

```
eval results ──► Critique (LLM) ──► critique_text ──► L2 (LLM) ──► l2_directive ──► L1 (LLM)
                 1st hop                               2nd hop
```

When L2 fires, L1 is 2 LLM hops from eval data. Each hop is lossy
compression. The `guidance` mutex ensures L1 sees the most-processed
form available. Validation failures bypass critique entirely and feed
L2 directly (1 hop instead of 2) — the signal is already structured,
no eval-result digestion needed.

L3 sees only `{{l2_summary}}` (last 3 L2 outcomes: what changed,
whether accuracy moved) — never L2's directive or reasoning. Strategy
from outcomes, not tactics.

## Internal — not a prompt injection

**Stale data observations** — accumulated during eval →
`opt_sp.memory.warning_inventory` → aggregated cross-campaign by
`SearchMemory`. Consumed by L1's stale data protocol (`sampleswitch`
queries SearchMemory's per-query degradation rate). Never enters an
LLM prompt.

## Self-optimization (meta-level)

The potter's own prompts are themselves `PromptTemplate` instances —
the 8-field decomposition applies recursively, which is what enables a
future self-optimization mode. The trace dataset (`potter_traces`
loader) freezes archived campaign transitions into
`{round_context → next_directive → score_delta}` rows that an
outer-loop PromptPotter instance can score meta-prompt variants
against. See
[prompt-scheme.md § Optimizer Meta-Prompts](prompt-scheme.md#optimizer-meta-prompts)
and [§ Potter Trace Dataset](prompt-scheme.md#potter-trace-dataset).
