# The Campaign Tree

A campaign is a tree. The root cycle ran first. Every fork branches from a specific round of its parent. Every sweep payload asks the tree to grow another sibling.

## The primitive

A cycle is a directory under `campaigns/`. A fork is a new cycle whose `index.json` carries `parent_cycle_id`. The parent's ledger gets a `ResumeCheckpointRecord(kind=FORK_CUT)` naming the child's `cycle_id` and the cut round; the fork's ledger inherits from the parent's history up to the cut. That's it.

```
campaigns/
  cycle_abc123/                       # root (no parent_cycle_id)
    index.json
    ledger (events.jsonl)             # …, FORK_CUT → fork_x, …
    forks/
      cycle_abc123_fork_x/            # branch
        index.json                    # parent_cycle_id: cycle_abc123
        ledger                        # inherit_from(parent, offset_at_cut)
```

Forks of forks nest the same way (still flat under the family root's `forks/`). No new file format, no separate "branches" registry — the cycle directory schema is already a tree, and the FORK_CUT decision is the edge.

## Three callers, one primitive

The mechanism is trigger-agnostic:

| Caller | Trigger | Stored in |
|--------|---------|-----------|
| **Scoring divergence** | `optimize --fork-on-divergence` detects a recorded decision no longer holds under the current scorer | (no payload) |
| **Operator sweep** | `optimize --sweep` with payloads under `datasets/{name}/sweep/` | `ResumeCheckpointRecord.data.fork.sweep_payload` |
| **Manual rewind** (M11, planned) | operator labels a fork from any round | (TBD) |

The primitive does not know which caller fired. The caller passes a small `extra_data` blob into the FORK_CUT decision's archival `data` field. New callers add new `data.*` keys; the primitive stays small.

## What rides the tree, what doesn't

| Data | Lives in | Per-tree | Per-branch | Shared |
|------|----------|----------|------------|--------|
| Library measurements (`archive/measurements/{run_id}.json`) | flat archive | — | — | ✓ content-addressed |
| OSP state (`rounds/round_NNNN.json`) | branch dir | — | ✓ | — |
| Ledger records (Decision / Phase / Snapshot) | branch dir | — | ✓ | — |
| Sweep payload (operator input) | `datasets/{name}/sweep/*.json` | — | — | git-tracked |
| Sweep payload (recorded fact) | parent's ledger, `ResumeCheckpointRecord.data.fork.sweep_payload` | — | ✓ on parent | — |

A branch's ledger inherits from its parent up to the cut, so reading a fork's history walks parent's records, then fork's own.

**Library measurements are deliberately not on the tree.** A measurement is a fact about *one (JobSearchPoint, sample) pair*, content-addressed by the rendered prompt's hash. Two forks running the same baseline see identical content hashes and read the same `archive/` row — that's why the second fork's "baseline" costs zero LLM calls. See [`scoring-and-memory.md`](scoring-and-memory.md).

OSP is the optimizer's working memory for one branch — not part of the tree structure. When sweep mints a fork with overrides, the override fields ride into the fork as payload (written into FORK_CUT's `data.fork.sweep_payload`, stamped onto the fork's `cycle.opt_sp` after bootstrap). For what OSP carries: [`state-record.md`](state-record.md).

## The primitive's three checks

When a new fork driver lands, run it through three checks. If any fail, the primitive has reached its scope and the new feature wants its own layer:

1. **Trigger-agnostic.** New caller adds an entry under `data.fork.*` and a few lines in one orchestrator. No edits to `_fork_at_divergence`'s body, no new ledger record kind.
2. **Override is OSP-carriable.** Branch-differing fields are already (or trivially extensible to) `OptSearchPoint` fields. A different pipeline shape or scoring formula is a layer above.
3. **No data fracture.** No parallel persistence directory; no duplicate of something already in `archive/`, `rounds/`, or the ledger.

The primitive passes all three for sweep, scoring-divergence, and the planned operator-rewind / LLM-rebase callers.

## L4 today, with the operator as policy

The roadmap calls full self-optimization "L4" — a layer above L3 proposing the next round of candidate L1 prompts. In the absence of the auto-policy:

1. Operator (or Claude via `/potter-review`) reads the leaderboard + `review.md` for the last sweep batch.
2. Operator authors the next batch of `SweepPayload` JSONs.
3. `optimize --sweep` runs the next generation.
4. Library cache means baseline measurements don't repeat — each generation only pays for actual L1 variants.

This is L4 with the operator as the policy. The data accumulating in `campaigns/{root}/forks/` plus `proxy_lift_corr` math in `leaderboard.py` is the substrate an automated policy would consume. Replacing the human policy with code reads the same trees, applies the same `SweepPayload` shape, runs the same primitive.

## See also

- [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) — operator how-to (rewind, fork, sweep).
- [`scoring-and-memory.md`](scoring-and-memory.md) — facts vs policy split underlying fork.
- [`the-loop.md`](the-loop.md) — L1/L2/L3 and the open seat L4 will fill.
