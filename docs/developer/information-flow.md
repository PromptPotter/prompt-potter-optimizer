# Surface field reference

Per-field tables for the L1/L2/L3 surfaces. For the conceptual flow (sections → surface → LLM, render chain, compression chain), see [`README.md`](README.md).

The key invariant: no prompt site summarizes its own data. If a field isn't in these tables, it doesn't enter a prompt.

---

## L1 / L2 surface fields

L1 generate and L2 receive typed surface payloads — `L1GenerateSurface` from `compile_l1_surface()`, `L2Surface` from `compile_l2_surface()`. Each surface field maps to a named hole in the prompt template. The L1-generate side is owned by L2 via OSP overrides; see [`l1-generate-surface.md`](l1-generate-surface.md).

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
| `plan` | ✓ | — | opt_sp | L3's strategic plan (read-only from L1's view). |
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

The blob is assembled by `compile_l1_critique_blob()`. Output flows into L2 refine (on escalation), which compresses it into a directive that L1 generate reads.

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
