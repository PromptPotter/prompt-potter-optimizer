# State Record

> **Audience:** Developer reference. Operators see [`../manual/`](../manual/) for usage docs.

Every round carries one record forward — the *individual* (`OptSearchPoint`, often *OSP*). It holds the prompt, the strategic context, the operational memory, and L2's mutations to L1's surface. Implementation: `promptpotter/domain/opt_search_point.py`. Domain contract: [`../../promptpotter/domain/CLAUDE.md`](../../promptpotter/domain/CLAUDE.md).

Two parameter namespaces co-exist on the record: **prompt fields** (persona / task intent / problem description / instruction / thinking style / answer format / few-shot examples / plan) and **pipeline parameters** (thresholds / model / temperature / retrieval budgets — anything the pipeline's nodes expose). Names can overlap; the namespaces are independent. L1 mutates both in one proposal; routing happens at individual-creation time.

L1 writes prompt fields + operational memory each round. L2 (when it fires) writes any subset of: `brief`, `task_context`, `l1_overrides` (optimizer params), `scheme_overrides` / `text_overrides` / `template_override` (L1 surface levers), `action` (`normal_round` / `probe_round`). L3 writes `plan`. Lineage is set at creation; never mutated.

The record is the optimizer's working memory for two independent reasons:

- **Persistence.** Every round's record is serialized to `campaigns/{cycle_id}/rounds/round_NNNN.json`. Resume reads from the latest trial. State that's not on the record does not survive interruption.
- **Steering.** Every layer reads from the record to know what to do — L1 reads prompt fields + brief + surface overrides; L2 reads operational memory + surface state; L3 reads plan + runtime failures.

## What the record is NOT

- Not the trace archive — per-sample results live in `archive/measurements/` and are referenced by ID. See [`scoring-and-memory.md`](scoring-and-memory.md).
- Not the pipeline configuration — frozen target shape lives in `JobSearchPoint`.
- Not the campaign config — operator knobs (max rounds, patience, n_variants ceiling) live on `CampaignConfig` and never mutate.

For the per-layer prompt structure (8 fields, layer-specific surfaces, render chain) see [`../developer/README.md`](../developer/README.md) + [`../developer/l1-generate-surface.md`](../developer/l1-generate-surface.md).
