# The Campaign Tree

> **Audience:** Developer reference. Operators see [`../manual/`](../manual/) for usage docs.

A **Campaign** is one declared optimization effort — a **dataset**, a **pipeline origin**, the **context text**, and the **optimizer meta-prompts** it runs under. It owns `campaigns/{campaign_id}/`, a `campaign.json` manifest, a campaign-wide `log.md`, and `hard_samples.json`.

`campaign_id = {dataset}__{rand6_hex}` — minted fresh per `new` invocation. Each `python -m promptpotter new <dataset>` produces a distinct campaign. The declaration (target hash + optimizer-prompt hash) is recorded as properties on `campaign.json` (`root_content_hash`, `optimizer_prompt_hash`) and used by resume to warn on drift, not to derive the id. Cross-campaign evidence pooling on the same declaration rides the dataset-scoped `archive/measurements/`.

A **Session** is one `new` invocation. A campaign holds one session — the `new` that minted it. `resume` extends it; `resume --fork-on-divergence` adds sibling cycles. Each session is itself a tree: a root cycle (`cycle_<target_hash>`) plus its fork descendants. The four-entity hierarchy: **Workspace → Dataset → Campaign → Cycle**, with **Session** a unit of a campaign.

> **Legacy on-disk shape.** Pre-existing campaigns minted under the previous content-addressed scheme (`{dataset}__{declaration_hash}`, find-or-create on duplicate) carry multiple session roots — `cycle_<hash>` for session 1, `cycle_<hash>_s{N}` for session N — under one `campaign_id`. Readers still parse them; the `_s{N}` suffix is no longer written. See `promptpotter/infrastructure/store/paths.py` for the canonical reader.

## Primitive

A cycle is a directory under a campaign's `cycles/`. A fork is a new cycle whose `index.json` carries `parent_cycle_id` and `sibling_kind`. The parent's ledger gets a `ResumeCheckpointRecord(kind=FORK_CUT)` naming the child's `cycle_id` and the cut round; the new cycle's ledger inherits from the parent's history up to the cut.

```
campaigns/justlogic__a1b2c3/        # one Campaign
  campaign.json                     # manifest
  log.md                            # campaign digest
  cycles/
    cycle_abc123/                   # session root (no parent_cycle_id)
      dashboard.json                # live telemetry for the session-family
      index.json                    # sibling_kind: root
      ledger (events.jsonl)         # …, FORK_CUT → fork_x, …
    cycle_abc123_fork_x/            # branch — flat alongside the root
      index.json                    # parent_cycle_id: cycle_abc123, sibling_kind: fork
      ledger                        # inherit_from(parent, offset_at_cut)
```

Forks land flat under `cycles/`. The tree is reconstructed from `parent_cycle_id` metadata, not directory nesting. `dashboard.json` lives in the session-family root cycle, shared by its forks.

**`unit_kind` (webapp sidebar label):** `session` (root, `resume`-extended) · `divergent_resume` (a `resume --fork-on-divergence` branch) · `user_fork` (HITL fork / diagnostic / sweep, all one kind) · `l3_fork` (reserved for L3 auto-forking, not emitted yet).

## Three callers, one primitive

| Caller | Trigger | Payload |
|---|---|---|
| **Scoring divergence** | `resume --fork-on-divergence` detects a recorded decision no longer holds under the current scorer | none |
| **Operator sweep** | `new --sweep-batch` with payloads under `datasets/{name}/sweep/` | `ResumeCheckpointRecord.data.fork.sweep_payload` |
| **Manual rewind** (M11, planned) | operator labels a fork from any round | TBD |

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
