# Surface field reference

Per-field tables for the L1/L2/L3 surfaces. For the conceptual flow (sections → surface → LLM, render chain, compression chain), see [`README.md`](README.md).

The key invariant: no prompt site summarizes its own data. If a field isn't in these tables, it doesn't enter a prompt.

---

## Dispatch state and layer configs

Every optimizer LLM call follows the same path:

```
DispatchState (per-call state, built by build_dispatch_state)
    → LAYER_CONFIGS[layer]   ({template_var → section_renderer} table)
    → compile_prompt_vars       (applies OSP overrides, merges per-call extras)
    → run_optimizer_node     (renders the prompt template, calls LLM)
```

`LAYER_CONFIGS` (`promptpotter/application/optimization/pipeline.py`) declares each layer's prompt holes:

- **`Layer.L1_GENERATE`** — 8 section renderers (incl. `plan`, `l2_directive`) + L2 override channel (`read_overrides`). Per-call scalars (`n_variants`, `accuracy_pct`, `n_queries`, `rendered_prompt`) ride as extras.
- **`Layer.L2`** — 7 section renderers (incl. `plan` — symmetric injection from L3); `current_params`, `task_context_section`, `l1_generate_field_catalogue` ride as extras (`_compile_l2_extras`).
- **`Layer.L3`** — no auto-suffix sections; all 7 vars ride as extras (`_compile_l3_extras`). L3 has no override channel — it owns the strategic plan directly.
- **`Layer.L1_CRITIQUE`** — single `dispatch_msg` blob built by `compile_l1_critique_blob` and passed as an extra. No per-section override channel.

**Field lifetime — the channels between layers:**

| Field | Writer | Reader(s) | Lifetime |
|-------|--------|-----------|----------|
| `dispatch_msg` | `compile_l1_critique_blob` | L1-critique prompt | per-call, not persisted |
| `l1_critique_text` (+ critique fields) | L1-critique | L1-generate, L2 | one round (cleared by `clear_volatile`) |
| `l2_directive` | L2 | L1-generate | one round (cleared by `clear_volatile`) |
| `l1_section_overrides` / `_text` | L2 | L1-generate `read_overrides` | persistent (memory) |
| `plan` | L3 | L1-generate, L2 | persistent until next L3 (never cleared) |

**Symmetric plan injection:** L3 writes `plan` to `OptSearchPoint`; both L1-generate **and** L2 read it. L1 receives it as a strategic constraint on candidate generation; L2 receives it as the operating context for its directive. (`l2_directive` flows L2→L1; `plan` flows L3→{L1, L2}.)

---

## L1 / L2 surface fields

L1-generate's section renderers are registered in `LAYER_CONFIGS[Layer.L1_GENERATE].sections`; L2's in `LAYER_CONFIGS[Layer.L2].sections`. Each section name is the prompt template hole it fills. The L1-generate side is owned by L2 via OSP overrides; see [`l1-generate-surface.md`](l1-generate-surface.md).

**Retention legend:** `memory` (checkpointed with the candidate across rounds), `opt_sp` (on the optimizer state, checkpointed), `transient` (computed per-round, not stored), `config` (immutable within a cycle), `axes` (cross-campaign).

| Field | L1 | L2 | Retention | Description |
|-------|----|----|-----------|-------------|
| `pipeline_schema_text` | ✓ | — | config | Precomputed pipeline node/param catalogue — teaches L1 what it may tune. |
| `failure_analysis` | ✓ | — | transient | Top-3 clustered failure patterns with example queries. |
| `axes_l1` | ✓ | — | axes | Cross-campaign digest: failure clusters, dead queries, top axes / values. |
| `task_context` | ✓ | — | opt_sp | Structured domain context (read-only from L1's view; L2 edits). |
| `escalation_probe` | ✓ | — | memory | Probe-round per-query warning dump — fires only when L2 requests a probe. |
| `escalation_alert` | ✓ | — | memory | Non-probe aggregated escalation alert — suppressed by an active `l2_directive`. |
| `l2_directive` | DIRECTIVE: | PREVIOUS DIRECTIVE: | memory | L2's one-round guidance window; clears on improvement. The only guidance channel into L1 generate. |
| `plan` | ✓ | ✓ | opt_sp | L3's strategic plan; symmetric read by L1 (constraint on generation) and L2 (operating context for directives). |
| `escalation_section` | — | ✓ | transient | Aggregated pipeline stability report — composed from `escalation_check_result`. |
| `warning_inventory` | — | ✓ | memory | Per-query warning breakdown — L2 fallback when no escalation section. |
| `validation_failures` | — | ✓ | transient | L1 parse-time invariant violations — Loop 1 self-healing input. |
| `runtime_failures` | — | ✓ | memory | Mid-eval degradation records — Loop 2 self-healing input for L2. |
| `l2_output_failures` | — | (L3) | memory | Validator outcomes on L2's own parsed output — Loop 4 self-healing input for L3. |
| `axes_l2` | — | ✓ | axes | Cross-campaign strategic digest: axis rankings, bottlenecks, correlations. |

L2 also receives an `l1_generate_field_catalogue` hole — a code-derived menu of every L1-generate registry entry with its current visibility / override state. See [`l1-generate-surface.md`](l1-generate-surface.md).

L2 writes back a flat dict: any subset of `directive`, `optimizer_params`, `task_context`, `scheme_overrides`, `text_overrides`, `template_override`, `action`. Each field is independent and lands directly on the corresponding `OptSearchPoint` field. See [`l2-internals.md`](l2-internals.md).

---

## L1 critique blob composites

The critique phase runs inside L1 after scoring and winner selection. Four composite sections in order; each delegates to private `_section_l1c_*` helpers and joins their outputs with `\n\n`:

| Section | Inner blocks |
|---------|--------------|
| `ROUND_REPORT` | scoring summary, anomaly flags, pipeline health, candidate rank analysis, round evolution, this-round trajectory + diff |
| `PER_QUERY_REPORT` | runtime failures this round, query categories, failure details, successes |
| `HISTORICAL_CONTEXT` | `AxisIndex`: discriminating queries, failure clusters, tractability, exhausted axes, value trends, improvement attribution |
| `AVAILABLE_SCHEMA_MUTATIONS` | Pipeline nodes with mutable output schemas |

The blob is assembled by `compile_l1_critique_blob(state)` and passed as the `dispatch_msg` extra to `compile_prompt_vars(Layer.L1_CRITIQUE, ...)`. Output flows into L2 refine (on escalation), which compresses it into a directive that L1 generate reads.

---

## L3 multi-hole template

L3 fires when L2 stalls and owns the strategic plan. The template is built entirely from explicit holes:

| Hole | Source |
|------|--------|
| `{{current_plan}}` | Current strategic plan |
| `{{l2_summary}}` | Last 3 L2 rounds — what changed, whether accuracy moved |
| `{{rendered_prompt}}` | Current prompt rendered as a single string |
| `{{pipeline_section}}` | Current pipeline parameters |
| `{{runtime_failures_section}}` | Runtime failures accumulated across rounds |
| `{{axes_digest}}` | `AxisIndex.digest_for_l3()`: axis rankings, bottleneck distribution, failure clusters, persistent failures |
