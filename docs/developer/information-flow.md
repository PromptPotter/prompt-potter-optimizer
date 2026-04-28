# Information Flow — Optimization Loop

Every optimizer LLM prompt (L1, L2, L3) receives a `{{dispatch_msg}}` block — the single channel through which evaluation history, axis-side context, and cross-round signals enter a prompt. L1 has two internal phases: generate (proposes candidates) and critique (analyzes round results). Each phase / layer has its own dispatch_msg layout; this doc covers all four.

This file owns the data-routing contract: which data enters which dispatch_msg, how long each piece of data lives, and which fields are mutually exclusive. [../concepts/three-layer-loop.md](../concepts/three-layer-loop.md) covers the conceptual picture; [code-map.md](code-map.md) names every symbol mentioned below.

The key invariant: no prompt site summarizes its own data. All compression flows through the chain documented below — if a field isn't in these tables, it doesn't enter a prompt.

## The flow in five nouns

```
archive ──► AxisIndex (cached) ──► LayerContext (per-call) ──► sections (pure) ──► dispatch_msg ──► LLM
```

1. **archive** — `MeasurementArchive` under `library/measurements/`. Append-only fact table; one row per `(sample × config → outcome)`. The single source of truth.
2. **AxisIndex** — derived axis-keyed view over the archive. Refreshes incrementally each round (cursor: `_axis_seen_runs` for axis side, `sample_index._seen_runs` for sample side). Hosts `digest_for_l1_generate / _l1_critique / _l2 / _l3` — pure derivations of cached state.
3. **LayerContext** — per-call payload built once by `compile_layer_context(layer, cycle, ...)`. Bundles the persistent `cycle` reference, per-call inputs (round_num, scoring_result, candidate_scores, …), the layer-appropriate axis digest pre-fetched from `cycle.axes`, and (only on L1_CRITIQUE) a `_CritiqueContext` with cross-cutting facts.
4. **sections** — pure formatters with signature `(ctx: LayerContext) -> str`. Each returns its rendered text or `""` when inactive. No I/O, no recomputation. The complete catalogue lives in `application/optimization/nodes/dispatch_msg_registry.py::_SECTIONS`.
5. **dispatch_msg** — `assemble_dispatch_msg()` walks `LAYER_ORDER[layer]`, calls each section, drops empties, joins with `\n\n`. Result is the `{{dispatch_msg}}` block injected into the L1/L2/L3 prompt template.

If you can answer "what enters L1?" by listing what's in `LayerContext`, the flow is minimally knotted — by construction.

---

## How to read

- **Retention** — how long the data lives: `memory` (checkpointed with the candidate across rounds), `opt_sp` (on the optimizer state, checkpointed), `transient` (computed per-round, not stored), `config` (immutable within a cycle), `axes` (cross-campaign).
- **L1 / L2** — the section header injected into that layer's prompt, or `—` when the field is not sent to that layer.
- **Mutex** — fields sharing the same group produce only the highest-priority populated section (only one wins per round).

## Compression chain

```
eval results ──► L1 critique phase ──► critique text ──► L2 (LLM) ──► L2 directive ──► L1 generate phase
                 1st hop                                   2nd hop
```

When L2 fires, L1 is 2 LLM hops from evaluation data. Each hop is lossy compression. The guidance mutex ensures L1 sees the most-processed form available. Validation failures bypass L1 critique entirely and feed L2 directly (1 hop instead of 2) — the signal is already structured.

L3 sees only the last 3 L2 outcomes (what changed, whether accuracy moved) — never L2's directive or reasoning. Strategy from outcomes, not tactics.

## Internal — not a prompt injection

**Stale data observations** — per-query warnings accumulate in the warning inventory and are aggregated cross-campaign by `AxisIndex`. The stale-data protocol uses `AxisIndex`'s per-query degradation rate to decide when to swap a sample out. Never enters an LLM prompt.

## L1 / L2 dispatch_msg

Fields assembled by `assemble_dispatch_msg()` from the declarative registry in `application/optimization/nodes/dispatch_msg_registry.py`. The same registry covers all four layers (`Layer.L1_GENERATE`, `Layer.L1_CRITIQUE`, `Layer.L2`, `Layer.L3`); this table covers L1 generate and L2.

| Field | L1 | L2 | Retention | Mutex | Description |
|-------|----|----|-----------| ------|-------------|
| `pipeline_schema_text` | ✓ | — | config | — | Precomputed pipeline node/param catalogue — teaches L1 what it may tune. |
| `failure_analysis` | ✓ | — | transient | — | Top-3 clustered failure patterns with example queries. |
| `axes_l1` | ✓ | — | axes | — | Cross-campaign digest: failure clusters, dead queries, top axes / values. |
| `task_context` | ✓ | — | opt_sp | — | Structured domain context (read-only from L1's view; L2 edits). |
| `escalation_probe` | ✓ | — | memory | — | Probe-round per-query warning dump — fires only when L2 requests a probe. |
| `escalation_alert` | ✓ | — | memory | — | Non-probe aggregated escalation alert — suppressed by an active `l2_directive`. |
| `l2_directive` | DIRECTIVE: | PREVIOUS DIRECTIVE: | memory | guidance pri 2 | L2's one-round guidance window; clears on improvement. |
| `l1_critique_text` | CRITIQUE: | CRITIQUE: | memory | guidance pri 1 | Latest L1 critique output; L2 digests into a directive before L1 sees it. |
| `plan` | ✓ | — | opt_sp | — | L3's strategic plan (read-only from L1's view). |
| `escalation_section` | — | ✓ | transient | — | Aggregated pipeline stability report — composed from `escalation_check_result`. |
| `warning_inventory` | — | ✓ | memory | — | Per-query warning breakdown — L2 fallback when no escalation section. |
| `validation_failures` | — | ✓ | transient | — | L1 parse-time invariant violations — Rail 1 self-healing input. |
| `runtime_failures` | — | ✓ | memory | — | Mid-eval degradation records — Rail 2 self-healing input for L2. |
| `axes_l2` | — | ✓ | axes | — | Cross-campaign strategic digest: axis rankings, bottlenecks, correlations. |

### Mutex rules

Fields sharing a mutex group are mutually exclusive per layer — only the highest-priority populated field renders. On L1: `l2_directive` (pri 2) wins over `l1_critique_text` (pri 1) in the `guidance` group. When L2 fires, L1 sees the directive instead of the raw critique.

## L1 — critique phase dispatch_msg

The critique phase runs inside L1 after scoring and winner selection. Sections share cross-cutting state (anomaly accumulator, near-miss query set passed between sections); the registry runs a one-shot pre-pass — `_compute_critique_context` — that computes those facts up front and stashes them in a `_CritiqueContext` attached to `LayerContext.critique`, so the section renderers stay pure. Sections in order:

| Section | Source |
|---------|--------|
| `SCORING SUMMARY` | Round accuracy, composite score, degraded query count, stall count, best accuracy |
| `ANOMALY FLAGS` | Accumulated by the pre-pass; renders only when non-empty |
| `PIPELINE HEALTH` | Candidate result distribution by termination step |
| `RUNTIME FAILURES THIS ROUND` | Runtime failures from the round's candidates |
| `CANDIDATE RANK ANALYSIS` | Per-candidate results and rankings |
| `ROUND EVOLUTION` | Round-by-round trajectory |
| `QUERY CATEGORIES` | Queries grouped by where processing terminated |
| `FAILURE DETAILS` | Detailed failure breakdown |
| `SUCCESSES` | Example hits |
| `HISTORICAL CONTEXT` | `AxisIndex`: discriminating queries, failure clusters, tractability, exhausted axes, value trends, improvement attribution |
| `THIS ROUND` | Round trajectory and cross-candidate diff |
| `AVAILABLE SCHEMA MUTATIONS` | Pipeline nodes with mutable output schemas |

The critique phase is the only dispatch_msg with access to raw per-query results — it's the every-round analysis hub. Its output flows into L1 generate (next round) and L2 refine (on escalation).

## L3 — multi-hole template

L3 fires when L2 stalls and owns the strategic plan. In addition to its `{{dispatch_msg}}`, it receives several context anchors:

| Hole | Source |
|------|--------|
| `{{current_plan}}` | Current strategic plan |
| `{{l2_summary}}` | Last 3 L2 rounds — what changed, whether accuracy moved |
| `{{rendered_prompt}}` | Current prompt rendered as a single string |
| `{{pipeline_section}}` | Current pipeline parameters |
| `{{runtime_failures_section}}` | Runtime failures accumulated across rounds |
| `{{dispatch_msg}}` | `AxisIndex`: axis rankings, bottleneck distribution, failure clusters, persistent failures |

## Three tiers of analysis

L1 focuses on generating diverse candidates. Everything else is one of three tiers, each with a distinct owner, trigger, and signal type.

| Tier | Handled by | Fires when | What | Example |
|------|-----------|------------|------|---------|
| **Tier 1 — Deterministic** | Code (statistics) | Every round | Per-query triage without LLM reasoning | Zero-signal sample filtering |
| **Tier 2 — Every-round critique hub** | L1 — critique phase | Every round | Frame this-round analysis with historical context | Tractability profiles, axis exhaustion, value trends |
| **Tier 3 — Strategic** | L2 Refine + L3 Plan (LLM) | Escalation only | Meta-reasoning about why optimization is stuck | Round trajectory, candidate comparison, failure group × axis |

L1 continues to receive: L1 critique text, scan context, failure analysis patterns, and `AxisIndex` summaries (failure clusters, top axes, dead queries). L3 receives the aggregate picture (axis rankings, bottleneck distribution, failure clusters, persistent failures) for strategic plan pivots.

More at [axis-index-internals.md](axis-index-internals.md).
