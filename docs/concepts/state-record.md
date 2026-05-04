# State Record

Every round, the optimizer carries one record forward — the *individual* (in code: `OptSearchPoint`, often shortened to *OSP*). It holds everything the optimizer knows about the current best-so-far: the prompt, the strategic context, the operational memory, and L2's mutations to L1's surface.

For the implementation, see `OptSearchPoint` in `promptpotter/domain/opt_search_point.py`.

---

## What lives on the record

Loosely grouped:

| Group | Holds | Who writes |
|-------|-------|------------|
| **Prompt fields** | persona, task intent, problem description, instruction, thinking style, answer format, few-shot examples, plan. | L1 (each round) and L3 (plan field, on stall). |
| **Lineage** | id, parent id, change description, timestamp. | Set on creation; never mutated. |
| **Optimizer params** | creativity, candidate budget, variant strategy. | L2 when it fires. |
| **Task context** | structured domain understanding (domain, pipeline purpose, data characteristics, optimization goals, key challenges). | One-time decomposition at init; refined by L2. |
| **Operational memory** | latest critique, escalation journal, warning inventory, L2 directive, validation failures, runtime failures, failure analysis, round history. | L1 (each round); preserved across L2/L3 transitions. |
| **L1-generate surface overrides** | section visibility toggles, section text overrides, whole-body override. | L2 (via `mutate_scheme` and `rewrite_full`). |

## Two parameter namespaces

An individual is more than a prompt. It also carries pipeline parameters — thresholds, model names, temperature, retrieval budgets — anything the pipeline's nodes expose. These live in a separate namespace from prompt fields. Names can overlap (*thinking style* may be both a prompt field and a node parameter); they remain independent axes regardless.

L1 mutates both namespaces in the same proposal — "change the persona and bump the web search budget" — with routing handled at individual-creation time.

## Why decomposition matters

PromptPotter doesn't treat a prompt as one opaque string. It decomposes every prompt into independently mutable fields — six prompt-string fields rendered in order, plus two appended sections (few-shot examples, plan).

- **Measurable axes.** Search memory tracks effect size per axis. After enough campaigns, *thinking style* may routinely move fitness by several points on this kind of problem while *persona* barely matters; future rounds spend mutation budget where it pays off.
- **Targeted mutation.** L1 mutates one field, holds the rest, scores the delta. The genotype is high-dimensional but each per-round move is one-dimensional, so the signal is clean.
- **Recursion.** The optimizer's own meta-prompts (L1, L2, L3, critique) use the same scheme, so the same evolution machinery applies recursively when an outer loop optimises the optimiser.

For the per-layer prompt structure (8 fields, layer-specific surfaces, render chain) see [`../developer/README.md § Prompt structure`](../developer/README.md).

## The record is the optimizer's working memory

Two independent reasons the record matters:

- **Persistence.** Every round's record is serialized to `trials/trial_NNNN.json` in the campaign directory. Resume reads from the latest trial. State that's not on the record does not survive interruption.
- **Steering.** Every layer reads from the record to know what to do. L1 reads the prompt fields and the directive. L2 reads the operational memory and the surface overrides. L3 reads the plan and the runtime failures.

When L2 mutates the surface (toggles a section off, replaces section text, rewrites the body), it is writing onto this record. The next round's L1 reads from the same record — that's the bridge.

## What a round looks like for the record

```
Round N starts:
  L1 reads the record's prompt fields + directive + surface overrides
       ↓
  L1 produces candidates → measures fitness → selects winner
       ↓
  L1 writes operational memory back onto the record
       (critique, runtime failures, warning inventory, etc.)
       ↓
  L2 reads the operational memory + surface state
       ↓
  L2 (when it fires) writes any subset of fields onto the record
       (directive, optimizer params, task context, surface overrides, action)
       ↓
  Round N's record is checkpointed to trials/trial_NNNN.json
       ↓
Round N+1 starts: L1 reads the same record again
```

Every change L2 makes is on this record. Nothing is lost between rounds because nothing lives anywhere else.

## What the record is NOT

- It is not the trace archive. Per-query results live in `library/measurements/` and are referenced by ID.
- It is not the pipeline configuration. The frozen target pipeline shape lives in `JobSearchPoint`, projected from this record on demand.
- It is not the campaign config. Operator knobs (max rounds, patience, n_variants ceiling) live on `CampaignConfig` and never mutate.

## See also

- [`the-loop.md`](the-loop.md) — L2 is the main writer of the record's strategy fields.
- [`../developer/l1-generate-surface.md`](../developer/l1-generate-surface.md) — the surface fields on the record.
- [`scoring-and-memory.md`](scoring-and-memory.md) — how the record interacts with the scoring archive.
- `promptpotter/domain/opt_search_point.py` — the `OptSearchPoint` class definition and field list.
