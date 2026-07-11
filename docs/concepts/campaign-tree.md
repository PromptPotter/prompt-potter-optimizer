# The Campaign Tree

> **Audience:** Developer reference. Operators see [`../manual/`](../manual/) for usage docs.

A **Campaign** owns `campaigns/{campaign_id}/`: a `campaign.json` manifest, a campaign-wide `log.md`, `hard_samples.json`, and one root cycle plus its fork descendants under `cycles/`. Entity definitions (Campaign / Cycle / the no-Session-tier rule / `campaign_id` minting) are [`../architecture.md`](../architecture.md) §0's and [`../glossary.md`](../glossary.md)'s — this page owns only the tree mechanics. One id-parsing fact lives here: a cycle's family is parsed purely from its id — `infrastructure/store/layout.py::root_cycle_id` / `::sibling_kind` know exactly three separators (`_fork_`, `_diag_`, `_sweep_`).

## Primitive

A cycle is a directory under a campaign's `cycles/`. A fork is a new cycle whose `index.json` carries `parent_cycle_id` and `sibling_kind`. The parent's ledger gets a `ResumeCheckpointRecord(kind=FORK_CUT)` naming the child's `cycle_id` and the cut round; the new cycle's ledger inherits from the parent's history up to the cut.

```
campaigns/justlogic__a1b2c3/        # one Campaign
  campaign.json                     # manifest
  log.md                            # campaign digest
  cycles/
    cycle_abc123/                   # root (no parent_cycle_id)
      dashboard.json                # this cycle's own live telemetry
      index.json                    # sibling_kind: root
      .runtime/ledger.jsonl         # …, FORK_CUT → fork_x, …
    cycle_abc123_fork_x/            # branch — flat alongside the root
      dashboard.json                # the fork's OWN telemetry (seeded from parent at the cut)
      index.json                    # parent_cycle_id: cycle_abc123, sibling_kind: fork
      .runtime/ledger.jsonl         # inherit_from(parent, offset_at_cut)
```

Forks land flat under `cycles/`. The tree is reconstructed from `parent_cycle_id` metadata, not directory nesting. `dashboard.json` is per-cycle — every cycle owns its own, stamped with its own `cycle_id`.

**`unit_kind`** is the webapp sidebar label derived from `(sibling_kind, fork_trigger)` — the four values are enumerated in [`../glossary.md`](../glossary.md).

## Three callers, one primitive

| Caller | Trigger | Payload |
|---|---|---|
| **Scoring divergence** | `resume --fork-on-divergence` detects a recorded decision no longer holds under the current scorer | none |
| **Operator sweep** | `new --sweep-batch` with payloads under `datasets/{name}/sweep/` | `ResumeCheckpointRecord.data.fork.sweep_payload` |
| **Operator-steered fork** | operator stops the run and forks a sibling from any round, editing the searchpoint's prompt + node config + limits (webapp Steer & fork) | `ForkPayload` (`origin_prompt_fields`, `pipeline_overlay`, `config_overrides`, `steered_by`) |

The primitive does not know which caller fired. New callers add new `data.*` keys; the primitive stays small. Library measurements are deliberately not on the tree — content-addressed by `JobSearchPoint.content_hash`, two forks see identical content hashes and read the same `archive/` row (why the second fork's origin costs zero LLM calls).

## Three checks for new fork drivers

When a new driver lands, run it through three checks. If any fail, the primitive has reached its scope and the feature wants its own layer:

1. **Trigger-agnostic.** New caller adds a `ForkTrigger` enum member + `ForkPayload` — no edits to `_mint_fork`'s body, no new ledger record kind.
2. **Override is OSP-carriable.** Branch-differing fields are (or trivially extend to) `OptSearchPoint` fields. Different pipeline shape or scoring formula is a layer above.
3. **No data fracture.** No parallel persistence directory; no duplicate of something already in `archive/`, `rounds/`, or the ledger.

## See also

- Operator how-to (rewind / fork / sweep): [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md)
- Facts-vs-policy split underlying fork: [`scoring-and-memory.md`](scoring-and-memory.md)
- L1/L2/L3 + the open seat L4 will fill: [`the-loop.md`](the-loop.md)
