# The Campaign Tree

## Campaign, Session, and Unit

A **Campaign** is **one declared optimization effort** — a **dataset**, a
**pipeline origin**, and the **context text**. It is a **forest**: a campaign
holds N **sessions**. It owns the directory `campaigns/{campaign_id}/`, a
`campaign.json` manifest, and a campaign-wide `log.md` + `hard_samples.json`.

`campaign_id = {dataset}__{origin_content_hash}` — the `origin_content_hash` is
the 12-hex content hash of the origin declaration (the same hash that is the
root cycle id). The id is **stable**: re-running `python -m promptpotter new
<dataset>` on an unchanged declaration resolves to the **same** campaign
(find-or-create), never a fresh one. Multiple campaigns may share a dataset —
each distinct `(dataset, origin)` declaration is its own campaign.

A **Session** is **one run of `new`** on a campaign's declaration. The first
`new` mints the campaign and its first session; each subsequent `new` on the
same declaration **adds** a session to the existing campaign. `resume` extends
the *active* session — it does not add one. A session's identity is its
`session_id` (`s_xxxx`). Each session is itself a tree: a root cycle plus its
fork descendants. The session root cycle id is `cycle_{hash}` for session 1 and
`cycle_{hash}_s{N}` for session N — the `_s{N}` suffix only disambiguates the
directory; it is **not** a sibling separator (`root_cycle_id` / `sibling_kind`
treat `cycle_X_s2` as its own family root, `cycle_X_s2_fork_abc` as a fork
rooted at it).

A **Unit** is one continuous-parameter run *inside* a session. A session starts
with one unit — its root cycle. `resume` — even after a Ctrl+C — keeps
extending the *current* unit: same parameters, one continuous run. A unit ends
and a new one **branches** off it on a **fork**, of which there are three kinds:

- **human-induced** — the operator branches a new unit from a chosen round;
- **L3-induced** — the L3 layer replans and forks a new unit;
- **divergence** — the same session is run with one changed parameter, and the
  trace diverges from the recorded one mid-run (`resume --fork-on-divergence`).

So a session's units form a tree: the root unit, plus a branch unit per fork; a
campaign is the forest of those N session trees. "Unit" is the operator-facing
name. On disk and in the API the identifier is `cycle_id` — **a unit is exactly
one cycle**; "cycle" is the internal name for the same thing. The webapp sidebar
shows units (the trees), never raw cycle ids, tagging each with its
`unit_kind` — see below.

The Campaign sits inside the four-entity hierarchy: **Workspace** → **Dataset**
→ **Campaign** → **Unit** (`cycle` internally), with **Session** a unit of a
campaign (its identity is the `session_id`). The same datastore is queryable
at three **data scopes** — `campaign` (one campaign's units, across every
session), `dataset` (every campaign for one dataset), `workspace` (everything) —
used identically by the archive query API, the heatmap artifacts, and the
webapp toggle.

## `unit_kind` — the operator-facing webapp label

The webapp sidebar tags each unit with a `unit_kind`, computed server-side from
`(sibling_kind, fork_trigger)`:

- **`session`** — a session root run; `resume` extends it.
- **`divergent_resume`** — a `resume --fork-on-divergence` branch.
- **`user_fork`** — any operator-initiated branch: HITL fork, diagnostic, and
  sweep all fold into this one kind.
- **`l3_fork`** — reserved for L3 auto-forking; not emitted yet.

> **Deferred (M11 candidate).** A campaign spanning multiple `(dataset,
> origin)` pairs — multi-seed / multi-dataset campaigns, plus cross-campaign
> leverage of workspace-scope archive data — is a deferred M11 candidate;
> `Campaign.dataset_name` stays a single string for now.

## The primitive

A unit is a directory under a campaign's `cycles/` (named by its `cycle_id`). A
fork is a new unit whose `index.json` carries `parent_cycle_id` and
`sibling_kind`. The parent's ledger gets a `ResumeCheckpointRecord(kind=FORK_CUT)`
naming the child's `cycle_id` and the cut round; the new unit's ledger inherits
from the parent's history up to the cut. That's it.

```
campaigns/
  justlogic__a1b2c3d4e5f6/            # one Campaign — {dataset}__{origin_content_hash}
    campaign.json                     # manifest: dataset, config, root_cycle_id, …
    log.md                            # campaign digest (every session + its forks + rounds)
    cycles/
      cycle_abc123/                   # session 1 root cycle (no parent_cycle_id)
        dashboard.json                # live telemetry for session 1 (+ its forks)
        index.json                    # sibling_kind: root
        ledger (events.jsonl)         # …, FORK_CUT → fork_x, …
      cycle_abc123_fork_x/            # branch of session 1 — flat alongside the root
        index.json                    # parent_cycle_id: cycle_abc123, sibling_kind: fork
        ledger                        # inherit_from(parent, offset_at_cut)
      cycle_abc123_s2/                # session 2 root cycle (re-run of `new`)
        dashboard.json                # live telemetry for session 2 (+ its forks)
        index.json                    # sibling_kind: root
```

Every session root and every fork lands flat under the same `cycles/` directory — the trees are reconstructed from `parent_cycle_id` metadata, not directory nesting. A flat store keyed by `parent_cycle_id` scales as the fork tree grows; nested fork-of-fork directories do not. No new file format, no separate "branches" registry — the FORK_CUT decision is the edge, `parent_cycle_id` is the link. `dashboard.json` is **per-session** — each session root carries its own live stream, shared by that session's forks (a fork's family root is its session root).

## Three callers, one primitive

The mechanism is trigger-agnostic:

| Caller | Trigger | Stored in |
|--------|---------|-----------|
| **Scoring divergence** | `resume --fork-on-divergence` detects a recorded decision no longer holds under the current scorer | (no payload) |
| **Operator sweep** | `new --sweep-batch` with payloads under `datasets/{name}/sweep/` | `ResumeCheckpointRecord.data.fork.sweep_payload` |
| **Manual rewind** (M11, planned) | operator labels a fork from any round | (TBD) |

The primitive does not know which caller fired. The caller passes a small `extra_data` blob into the FORK_CUT decision's archival `data` field. New callers add new `data.*` keys; the primitive stays small.

## What rides the tree, what doesn't

| Data | Lives in | Per-campaign | Per-cycle | Shared |
|------|----------|--------------|-----------|--------|
| Campaign manifest (`campaign.json`) | campaign dir | ✓ | — | — |
| Live telemetry (`dashboard.json`) | session-root cycle dir | — | ✓ per session-family (shared by its forks) | — |
| Library measurements (`archive/measurements/{run_id}.json`) | flat archive | — | — | ✓ content-addressed |
| OSP state (`cycles/{id}/rounds/round_NNNN.json`) | cycle dir | — | ✓ | — |
| Ledger records (Decision / Phase / Snapshot) | cycle dir | — | ✓ | — |
| Sweep payload (operator input) | `datasets/{name}/sweep/*.json` | — | — | git-tracked |
| Sweep payload (recorded fact) | parent's ledger, `ResumeCheckpointRecord.data.fork.sweep_payload` | — | ✓ on parent | — |

A branch's ledger inherits from its parent up to the cut, so reading a fork's history walks parent's records, then fork's own.

**Library measurements are deliberately not on the tree.** A measurement is a fact about *one (JobSearchPoint, sample) pair*, content-addressed by the rendered prompt's hash. Two forks running the same origin see identical content hashes and read the same `archive/` row — that's why the second fork's "origin" costs zero LLM calls. See [`scoring-and-memory.md`](scoring-and-memory.md).

OSP is the optimizer's working memory for one branch — not part of the tree structure. When sweep mints a fork with overrides, the override fields ride into the fork as payload (written into FORK_CUT's typed `data.fork: ForkPayload`, stamped onto the fork's `cycle.opt_sp` after bootstrap). For what OSP carries: [`state-record.md`](state-record.md).

## The primitive's three checks

When a new fork driver lands, run it through three checks. If any fail, the primitive has reached its scope and the new feature wants its own layer:

1. **Trigger-agnostic.** New caller adds a `ForkTrigger` enum member and constructs a `ForkPayload` — no edits to `_mint_fork`'s body, no new ledger record kind.
2. **Override is OSP-carriable.** Branch-differing fields are already (or trivially extensible to) `OptSearchPoint` fields. A different pipeline shape or scoring formula is a layer above.
3. **No data fracture.** No parallel persistence directory; no duplicate of something already in `archive/`, `rounds/`, or the ledger.

The primitive passes all three for sweep, scoring-divergence, and the planned operator-rewind / LLM-rebase callers.

## L4 today, with the operator as policy

The roadmap calls full self-optimization "L4" — a layer above L3 proposing the next round of candidate L1 prompts. In the absence of the auto-policy:

1. Operator (or Claude via [`/potter-l1-meta-campaign`](../../.claude/skills/potter-l1-meta-campaign/SKILL.md)) reads `review.md` for the last sweep batch.
2. Operator authors the next batch of `OperatorSweepFile` JSONs (one per candidate, narrow `reason` + `l1_layout` shape; the dispatcher widens each into a `ForkPayload(trigger=OPERATOR_SWEEP, ...)`).
3. `new --sweep-batch` runs the next generation.
4. Library cache means origin measurements don't repeat — each generation only pays for actual L1 variants.

This is L4 with the operator as the policy. The data accumulating in `campaigns/{campaign_id}/cycles/` plus the on-demand `MeasurementArchive` views are the substrate an automated policy would consume. Replacing the human policy with code reads the same trees, constructs the same `ForkPayload`, runs the same primitive.

## See also

- [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) — operator how-to (rewind, fork, sweep).
- [`scoring-and-memory.md`](scoring-and-memory.md) — facts vs policy split underlying fork.
- [`the-loop.md`](the-loop.md) — L1/L2/L3 and the open seat L4 will fill.
