# Information Flow — Optimization Loop

L1 generate and L2 receive typed surface payloads (`L1GenerateSurface`, `L2Surface`) compiled from a closed registry; each surface field maps onto a named hole in the prompt template. L1 critique still uses a single `{{dispatch_msg}}` blob because nothing external mutates its surface. L3 is multi-hole. This doc covers the data-routing contract for all four sites.

[../concepts/three-layer-loop.md](../concepts/three-layer-loop.md) covers the conceptual picture; [code-map.md](code-map.md) names every symbol mentioned below. For L2-specific orchestration see [l2-internals.md](l2-internals.md); for the L1-generate registry see [l1-generate-surface.md](l1-generate-surface.md).

The key invariant: no prompt site summarizes its own data. All compression flows through the chain documented below — if a field isn't in these tables, it doesn't enter a prompt.

## The flow in six nouns

```
archive ──► AxisIndex (cached) ──► LayerContext (per-call) ──► sections (pure) ──► surface (OSP overrides applied) ──► LLM
```

1. **archive** — `MeasurementArchive` under `library/measurements/`. Append-only fact table; one row per `(sample × config → outcome)`. The single source of truth.
2. **AxisIndex** — derived axis-keyed view over the archive. Refreshes incrementally each round. Hosts `digest_for_l1_generate / _l1_critique / _l2 / _l3` — pure derivations of cached state.
3. **LayerContext** — per-call payload built once by `compile_layer_context(layer, cycle, ...)`. Bundles the persistent `cycle` reference, per-call inputs, the layer-appropriate axis digest pre-fetched from `cycle.axes`, and (only on L1_CRITIQUE) a `_CritiqueContext` with cross-cutting facts.
4. **sections** — pure formatters with signature `(ctx: LayerContext) -> str`. Each returns its rendered text or `""` when inactive. The complete catalogue lives in `application/optimization/pipeline.py::_SECTIONS`.
5. **surface** — typed payload owned by L2 via OSP overrides. `compile_l1_surface()` walks `L1GenerateField`, applies `OptSearchPoint.l1_section_overrides` / `l1_section_overrides_text`, and returns `L1GenerateSurface`. `compile_l2_surface()` does the same plus a code-derived L1-generate field catalogue. Each surface dataclass exposes `to_compile_vars()` for the prompt template.
6. **LLM** — surface compile-vars feed `run_optimizer_node(template_name, compile_vars=...)`. L1 critique stays on the legacy blob path via `compile_l1_critique_blob()` because nothing external mutates its surface.

If you can answer "what enters L1?" by listing what's in `LayerContext`, the flow is minimally knotted — by construction.

---

## How to read

- **Retention** — how long the data lives: `memory` (checkpointed with the candidate across rounds), `opt_sp` (on the optimizer state, checkpointed), `transient` (computed per-round, not stored), `config` (immutable within a cycle), `axes` (cross-campaign).
- **L1 / L2** — the section header injected into that layer's prompt, or `—` when the field is not sent to that layer.

## Compression chain

```
eval results ──► L1 critique phase ──► critique text ──► L2 (LLM) ──► L2 directive ──► L1 generate phase
                 1st hop                                   2nd hop
```

L1 critique runs every round and writes `opt_sp.l1_critique_text` for display + telemetry. The text does **not** enter any prompt directly — it is L2's input only (when L2 fires) and L2 distills it into a directive that L1 generate then reads. There is one guidance source visible to L1 generate: `l2_directive`. When L2 hasn't fired, L1 generate has no critique-derived text channel — it relies on `failure_analysis`, `axes_l1`, and `escalation_alert`.

Validation failures bypass L1 critique entirely and feed L2 directly (1 hop instead of 2) — the signal is already structured.

L3 sees only the last 3 L2 outcomes (what changed, whether accuracy moved) — never L2's directive or reasoning. Strategy from outcomes, not tactics.

## Internal — not a prompt injection

**Stale data observations** — per-query warnings accumulate in the warning inventory and are aggregated cross-campaign by `AxisIndex`. The stale-data protocol uses `AxisIndex`'s per-query degradation rate to decide when to swap a sample out. Never enters an LLM prompt.

## L1 / L2 surface fields

L1 generate and L2 receive typed surface payloads — `L1GenerateSurface` from `compile_l1_surface()`, `L2Surface` from `compile_l2_surface()`. Each surface field maps to a named hole in the prompt template (no `{{dispatch_msg}}` blob). The L1-generate side is owned by L2 via OSP overrides; see [l1-generate-surface.md](l1-generate-surface.md).

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

L2 also receives a `l1_generate_field_catalogue` hole — a code-derived menu of every L1-generate registry entry with current visibility / override state. L2 cannot lose track of a section that exists in code; capability removal is a deliberate enum-deletion change. See [l1-generate-surface.md](l1-generate-surface.md).

L2's job on each fire is to write a flat dict back: any subset of `directive`, `optimizer_params`, `task_context`, `scheme_overrides`, `text_overrides`, `template_override`, `action`. Each field is independent and lands directly on the corresponding `OptSearchPoint` field; the next round's L1 reads from the same OSP. See [l2-internals.md](l2-internals.md).

## L1 — critique phase blob

The critique phase runs inside L1 after scoring and winner selection. Sections share cross-cutting state (anomaly accumulator, near-miss query set passed between sections); the registry runs a one-shot pre-pass — `_compute_critique_context` — that computes those facts up front and stashes them in a `_CritiqueContext` attached to `LayerContext.critique`, so the section renderers stay pure. Four composite sections in order:

| Section | Inner blocks |
|---------|--------------|
| `ROUND_REPORT` | scoring summary, anomaly flags, pipeline health, candidate rank analysis, round evolution, this-round trajectory + diff |
| `PER_QUERY_REPORT` | runtime failures this round, query categories, failure details, successes |
| `HISTORICAL_CONTEXT` | `AxisIndex`: discriminating queries, failure clusters, tractability, exhausted axes, value trends, improvement attribution |
| `AVAILABLE_SCHEMA_MUTATIONS` | Pipeline nodes with mutable output schemas |

Each composite delegates to private `_section_l1c_*` helpers and joins their non-empty outputs with `\n\n`. Inner blocks keep their `## HEADER` lines so the LLM still gets navigable sub-structure inside the four registry entries.

The critique phase is the only site with access to raw per-query results — it's the every-round analysis hub. The blob is assembled by `compile_l1_critique_blob()`. Its output flows into L2 refine (on escalation), which compresses it into a directive (or other OSP writes) that L1 generate reads.

## L3 — multi-hole template

L3 fires when L2 stalls and owns the strategic plan. L3 has always been multi-hole — its template is built entirely from explicit holes, including a direct `{{axes_digest}}` rendered via `format_axis_digest_block(cycle.axes.digest_for_l3(), ...)`:

| Hole | Source |
|------|--------|
| `{{current_plan}}` | Current strategic plan |
| `{{l2_summary}}` | Last 3 L2 rounds — what changed, whether accuracy moved |
| `{{rendered_prompt}}` | Current prompt rendered as a single string |
| `{{pipeline_section}}` | Current pipeline parameters |
| `{{runtime_failures_section}}` | Runtime failures accumulated across rounds |
| `{{axes_digest}}` | `AxisIndex.digest_for_l3()`: axis rankings, bottleneck distribution, failure clusters, persistent failures |

## Three tiers of analysis

L1 focuses on generating diverse candidates. Everything else is one of three tiers, each with a distinct owner, trigger, and signal type.

| Tier | Handled by | Fires when | What | Example |
|------|-----------|------------|------|---------|
| **Tier 1 — Deterministic** | Code (statistics) | Every round | Per-query triage without LLM reasoning | Zero-signal sample filtering |
| **Tier 2 — Every-round critique hub** | L1 — critique phase | Every round | Frame this-round analysis with historical context | Tractability profiles, axis exhaustion, value trends |
| **Tier 3 — Strategic** | L2 Refine + L3 Plan (LLM) | Escalation only | Meta-reasoning about why optimization is stuck | Round trajectory, candidate comparison, failure group × axis |

More at [axis-index-internals.md](axis-index-internals.md).
