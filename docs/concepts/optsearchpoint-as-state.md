# The Individual Record

Every round, the optimizer carries one record forward — the *individual* (in code: `OptSearchPoint`, often shortened to *OSP*). It holds everything the optimizer knows about the current best-so-far: the prompt, the strategic context, the operational memory, and L2's mutations to L1's surface.

This page is for operators. For the implementation, see [`../developer/code-map.md`](../developer/code-map.md).

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

- [what-is-l2.md](what-is-l2.md) — the main writer of the record's strategy fields.
- [l1-generate-surface.md](l1-generate-surface.md) — the surface fields on the record.
- [scoring-and-traces.md](scoring-and-traces.md) — how the record interacts with the scoring archive.
- [`../developer/code-map.md`](../developer/code-map.md) — the `OptSearchPoint` class definition and field list.
