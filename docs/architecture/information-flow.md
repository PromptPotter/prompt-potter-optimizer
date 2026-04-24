# Information Flow — Optimization Loop

Every optimizer LLM prompt (L1, L2, L1 Critique, L3) receives an `{{inbox}}` block.

## How to read

- **Retention** — how long the data lives: `memory` (checkpointed with the candidate across rounds), `opt_sp` (on the optimizer state, checkpointed), `transient` (computed per-round, not stored), `config` (immutable within a cycle), `search_memory` (cross-campaign).
- **L1 / L2** — the section header injected into that layer's prompt, or `—` when the field is not sent to that layer.
- **Mutex** — fields sharing the same group produce only the highest-priority populated section (only one wins per round).

## Compression chain

```
eval results ──► L1 Critique (LLM) ──► critique text ──► L2 (LLM) ──► L2 directive ──► L1 (LLM)
                 1st hop                                   2nd hop
```

When L2 fires, L1 is 2 LLM hops from evaluation data. Each hop is lossy compression. The guidance mutex ensures L1 sees the most-processed form available. Validation failures bypass L1 critique entirely and feed L2 directly (1 hop instead of 2) — the signal is already structured.

L3 sees only the last 3 L2 outcomes (what changed, whether accuracy moved) — never L2's directive or reasoning. Strategy from outcomes, not tactics.

## Internal — not a prompt injection

**Stale data observations** — per-query warnings accumulate in the warning inventory and are aggregated cross-campaign by SearchMemory. The stale-data protocol uses SearchMemory's per-query degradation rate to decide when to swap a sample out. Never enters an LLM prompt.

## L1 Critique inbox

L1 critique keeps its own assembler because its sections share cross-cutting state (anomaly accumulator, near-miss query set passed between sections). Sections in order:

| Section | Source |
|---------|--------|
| `SCORING SUMMARY` | Round accuracy, composite score, degraded query count, stall count, best accuracy |
| `PIPELINE HEALTH` | Candidate result distribution by termination step |
| `RUNTIME FAILURES THIS ROUND` | Runtime failures from the round's candidates |
| `CANDIDATE RANK ANALYSIS` | Per-candidate results and rankings |
| `ROUND EVOLUTION` | Round-by-round trajectory |
| `QUERY CATEGORIES` | Queries grouped by where processing terminated |
| `FAILURE DETAILS` | Detailed failure breakdown |
| `SUCCESSES` | Example hits |
| `ANOMALY FLAGS` | Accumulated across the sections above |
| `HISTORICAL INTELLIGENCE` | SearchMemory: discriminating queries, failure clusters, tractability, exhausted axes, value trends, improvement attribution |
| `THIS ROUND` | Round trajectory and cross-candidate diff |
| `AVAILABLE SCHEMA MUTATIONS` | Pipeline nodes with mutable output schemas |

L1 critique is the only layer with access to raw per-query results — it's the every-round intelligence hub. Its output flows into L1 generate (next round) and L2 refine (on escalation).

## L3 — multi-hole template

L3 fires when L2 stalls and owns the strategic plan. In addition to its `{{inbox}}`, it receives several context anchors:

| Hole | Source |
|------|--------|
| `{{current_plan}}` | Current strategic plan |
| `{{l2_summary}}` | Last 3 L2 rounds — what changed, whether accuracy moved |
| `{{rendered_prompt}}` | Current prompt rendered as a single string |
| `{{pipeline_section}}` | Current pipeline parameters |
| `{{runtime_failures_section}}` | Runtime failures accumulated across rounds |
| `{{inbox}}` | SearchMemory: axis rankings, bottleneck distribution, failure clusters, persistent failures |


## Compression chain

```
eval results ──► L1 Critique (LLM) ──► critique text ──► L2 (LLM) ──► L2 directive ──► L1 (LLM)
                 1st hop                                   2nd hop
```

When L2 fires, L1 is 2 LLM hops from evaluation data. Each hop is lossy compression. The guidance mutex ensures L1 sees the most-processed form available. Validation failures bypass L1 critique entirely and feed L2 directly (1 hop instead of 2) — the signal is already structured.

L3 sees only the last 3 L2 outcomes (what changed, whether accuracy moved) — never L2's directive or reasoning. Strategy from outcomes, not tactics.

## Internal — not a prompt injection

**Stale data observations** — per-query warnings accumulate in the warning inventory and are aggregated cross-campaign by SearchMemory. The stale-data protocol uses SearchMemory's per-query degradation rate to decide when to swap a sample out. Never enters an LLM prompt.
