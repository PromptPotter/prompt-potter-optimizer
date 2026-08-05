# State Record

> **Audience:** Developer reference. Operators see [`../manual/`](../manual/) for usage docs.

Every round carries one record forward — the *individual* (`OptSearchPoint`, often *OSP*). It holds the prompt, the strategic context, the operational memory, and L2's mutations to L1's surface. Implementation: `promptpotter/domain/opt_search_point.py`. Domain contract: [`../../promptpotter/domain/CLAUDE.md`](../../promptpotter/domain/CLAUDE.md).

Two parameter namespaces co-exist on the record: **prompt fields** (persona / task intent / problem description / instruction / thinking style / answer format / few-shot examples / plan) and **pipeline parameters** (thresholds / model / temperature / retrieval budgets — anything the pipeline's nodes expose). Names can overlap; the namespaces are independent. L1 mutates both in one proposal; routing happens at individual-creation time.

L1 writes prompt fields + operational memory each round. L2 (when it fires) writes any subset of: `l1_layout` (the L1 attention lever) and `l1_overrides` (optimizer params) — plus `axis_targeted`, which is prose naming its evidence anchor, not a lever. L2 does **not** write `task_context` (the operator's framing is frozen) and has no `action` field (probe rounds are not wired). L3 writes `plan`. Lineage is set at creation; never mutated.

The record is the optimizer's working memory for two independent reasons:

- **Persistence.** Every round's record is serialized to `<cycle_dir>/rounds/round_NNNN.json`. Resume reads from the latest trial. State that's not on the record does not survive interruption. The serialized record IS the loop's live config, not a log of it — CONTEXT and PLAN are inspectable and editable on disk, so "add this to the plan" means exactly that.
- **Steering.** Every layer reads from the record to know what to do — L1 reads prompt fields + brief + surface overrides; L2 reads operational memory + surface state; L3 reads plan + runtime failures.

## What the record is NOT

- Not the trace archive — per-sample results live in `archive/measurements/` and are referenced by ID. See [`scoring-and-memory.md`](scoring-and-memory.md).
- Not the pipeline configuration — frozen target shape lives in `JobSearchPoint`.
- Not the campaign config — operator knobs (max rounds, patience, n_variants ceiling) live on `CampaignConfig` and never mutate.

For the per-layer prompt structure (8 fields, layer-specific surfaces, render chain) see [`../developer/README.md`](../developer/README.md) + [`../developer/dispatch-hub.md`](../developer/dispatch-hub.md).
