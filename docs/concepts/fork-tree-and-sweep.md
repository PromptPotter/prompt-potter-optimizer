# The Fork Tree and the Sweep Primitive

A campaign is not a line. It's a tree. The root is the cycle that ran first; every fork is a branch from a specific round of its parent; every operator who drops a sweep payload is asking the tree to grow another sibling. This page is about the shape of that tree, what travels on it, and why the tree shape is the same one you'd draw if you mapped a Langfuse trace or a search-tree execution log.

It also addresses the question the operator started asking once forks landed: *can we use the sweep workflow to simulate self-optimization without writing the auto-policy yet?* The answer is yes, and the rest of the page explains why the data substrate is already enough.

---

## The primitive: a cycle, a fork, a parent pointer

The core mechanism is small. A cycle is a directory under `campaigns/`. A fork is a new cycle whose `index.json` carries a `parent_cycle_id`. The parent's ledger gets a `Decision(kind=FORK_CUT)` record naming the child's `cycle_id` and the round at which the cut happened, and the fork's own ledger inherits from the parent's history up to that cut. That's it.

```
campaigns/
  cycle_abc123/                       # root
    index.json                        # parent_cycle_id absent
    ledger (events.jsonl)             # …, FORK_CUT → fork_x, FORK_CUT → fork_y, …
    forks/
      cycle_abc123_fork_x/            # branch
        index.json                    # parent_cycle_id: cycle_abc123
        ledger                        # inherit_from(parent, offset_at_cut)
      cycle_abc123_fork_y/
        index.json                    # parent_cycle_id: cycle_abc123
        ledger
```

Every fork is structurally a sibling cycle that knows its parent. Forks of forks nest the same way (still flat under the family root's `forks/` for one-listing discoverability). No new file format, no separate "branches" registry, no parallel state machine — the cycle directory schema is already a tree, and the FORK_CUT decision is the edge.

The mechanism is *trigger-agnostic*. The same primitive serves three callers today:

- **Scoring divergence** (`optimize --fork-on-divergence`) — the rescore-on-load policy detects a recorded decision no longer holds; mint a sibling under the new scorer.
- **Operator sweep** (`optimize --sweep` with payloads under `datasets/{name}/sweep/`) — the operator wants N siblings under one parent, each starting from a different L1-surface override.
- **Manual rewind** (planned, M11) — operator labels a fork from any round to keep two narratives alive in parallel.

The primitive does not know which caller triggered it. The caller passes a small `extra_data` blob into the FORK_CUT decision's archival `data` field; the operator-sweep caller stores the `SweepPayload` there. New callers add new `data.*` keys. The primitive stays small.

## What rides the tree, what doesn't

Every piece of data PromptPotter records has one home. The tree shape stays clean because nothing is duplicated across homes.

| Data | Lives in | Per-tree | Per-branch | Shared |
|------|----------|----------|------------|--------|
| Library measurements (`library/measurements/{run_id}.json`) | flat archive | — | — | ✓ content-addressed |
| OSP state (`trials/trial_NNNN.json`) | branch dir | — | ✓ | — |
| Ledger records (Decision / Phase / Snapshot) | branch dir | — | ✓ | — |
| Sweep payload (operator input) | `datasets/{name}/sweep/*.json` | — | — | git-tracked |
| Sweep payload (recorded fact) | parent branch's ledger, `Decision.data.fork.sweep_payload` | — | ✓ on parent | — |

A branch's ledger inherits from its parent up to the cut, so reading a fork's history walks parent's records, then fork's own — identical to how a Langfuse trace's children flatten when you read by `parent_observation_id`. The shape is a tree of nested events whose subtree is reconstructable from any node downward.

**Library measurements are deliberately not on the tree.** A measurement is a fact about *one (JobSearchPoint, sample) pair*, content-addressed by the hash of the rendered prompt. Two forks running the same baseline see identical content hashes and read the same `library/` row — that's why the second fork's "baseline" costs zero LLM calls. If measurements were per-branch, each fork would re-measure baseline and the data would fracture into N redundant copies. The branch carries decisions made *over* measurements; the measurements themselves sit in shared ground.

## OSP is branch-state, not tree-structure

The conceptual question that matters when extending the primitive: *does `OptSearchPoint` belong to the tree, or does it just ride on it?*

OSP is the optimizer's working memory for a single branch — the prompt fields, the L2 directive, the surface overrides, the failure analysis, the round history. It is mutable across rounds; checkpointed into `trials/trial_NNNN.json` at every round-end; reloaded into the round loop on resume. (See [The Individual Record](optsearchpoint-as-state.md) for the field-level account.)

OSP is *not* tree-structure. The fork primitive doesn't read OSP; it doesn't even know it exists. The primitive only knows about cycles, ledgers, and parent pointers. When a sweep mints a fork with overrides, the override fields ride into the fork *as payload* — written into the FORK_CUT decision's `data.fork.sweep_payload` (record), and stamped onto the fork's `cycle.opt_sp` after bootstrap returns the cycle (apply). The cycle's existing checkpoint code then dumps OSP into `trials/trial_0001.json` exactly as it does for every other round. The override travels through the tree in the same persistence path the JobSearchPoint trace already uses.

This is the test we want the primitive to pass: any new branch-payload (today: SweepPayload; later: an L2-rebase payload, an L4 auto-rebase payload, a competitor-pipeline payload) is *additive on the same FORK_CUT.data field*, applied via *the same write site OSP already mutates from*, persisted by *the same checkpoint code that already runs every round*. Zero new persistence, zero new schemas.

If a future caller wanted to pass something OSP can't carry — say, a different pipeline shape — that's a hint the primitive has reached its scope. The split would land at the JobSearchPoint layer (a `pipeline_swap` field on the fork payload) without touching the tree mechanism. The primitive stays small; new layers stack on top of it.

## Langfuse compatibility, structurally

Langfuse models a trace as a tree: each observation has a `parent_observation_id`. PromptPotter's tree is one level coarser — the unit of nesting is a *cycle*, not an *observation* — but the shape composes:

- Cycle → Langfuse trace (top-level).
- Round → Langfuse span within the trace.
- Candidate scoring → child span.
- Sample evaluation → grandchild span.
- Fork → new trace whose metadata names the parent trace's id.

The current `langfuse/` mirror under each cycle dir already writes one trace per cycle. The fork relation is recoverable from `index.json::parent_cycle_id` and the parent ledger's FORK_CUT record. A future webapp or Langfuse projection can render the family tree by walking `campaigns/{root}/forks/*/index.json` — the data is already there, the projection is the only missing piece.

The point is: the on-disk shape is *already* what a tree-based observability tool would want. We did not invent a parallel structure for forks; we extended the same per-cycle layout the loop already wrote, with a parent pointer added in the one file that names the cycle.

## The sweep batch as one operator command

The operator authors `N` JSON payloads under `datasets/{name}/sweep/`, each describing an L1-surface override hypothesis (a directive, a section text replacement, a whole-template override). Each payload is validated through the `SweepPayload` Pydantic model (`extra='forbid'`, so a typo in a key fails at parse, not silently in an LLM run). One `optimize --sweep` invocation:

1. Reads every payload under `datasets/{name}/sweep/*.json`.
2. For each: mints a fork from the active root cycle at round 1, stamps the override onto the fork's starting OSP, runs round 1 scored + round 2 generation-only, halts.
3. Restores the active session pointer to the root so the next operator command stays anchored.

Each fork lands as a branch under `campaigns/{root}/forks/`. The leaderboard already groups by parent and pairs sweep cycles with their full counterparts (for the proxy-validity correlation). The review renderer already produces a per-cycle `review.md`. The L1 behavior checks already grade each fork's round 1.

The operator's loop becomes: *author payloads → run command → read forks side-by-side → author next batch from learnings.* The framework's existing post-hoc renderers do all the comparison work; the new `--sweep` dispatch just produces N comparable cycles in one invocation instead of N invocations with manual state-shifting between them.

See [`../operations/rewind-and-fork.md`](../operations/rewind-and-fork.md) for the operator how-to, including the payload field schema.

## Simulating self-optimization without writing the policy

The roadmap calls full self-optimization "L4" — a layer above L3 that proposes the next round of candidate L1 prompts based on the data the current round produced. L4 has two prerequisites: a credit-assignment substrate (cheap, fast feedback on which prompt-edit helped) and a PromptPotter-as-backend write surface (so a higher PromptPotter instance can drive a lower one). The cheap-feedback prerequisite is what the M10 framework was built to deliver; the write surface is M11/M12 work.

In the absence of the write surface, the operator-as-policy variant of the loop already runs:

1. Operator (or Claude via the `/potter-review` skill) reads the leaderboard + `review.md` for the last sweep batch.
2. Operator authors the next batch of `SweepPayload` JSONs based on what the data said — a directive that worked gets a follow-up sharpening it, a section-text override that flopped gets dropped, a new hypothesis goes in.
3. `optimize --sweep` runs the next generation.
4. The library cache means baseline measurements don't repeat; each generation only pays for the actual variants L1 produces from the new starting OSPs.

This is L4, with the operator (or Claude) as the policy. The data accumulating in `campaigns/{root}/forks/` plus the `proxy_lift_corr` math in `leaderboard.py` is exactly the substrate an automated policy would consume. Replacing the human policy with code is a separate piece of work — when it lands, it reads the same trees, applies the same SweepPayload shape, and runs the same primitive. Nothing about the tree changes.

The "potter-optimizing-potter" loop the operator described — CLI calls a PromptPotter server that calls the TermNorm server for scoring — is a special case of this same picture, with PromptPotter exposed as a ConnectorProtocol implementation (M12 work). The current sweep mechanism is the manual rehearsal.

## Stopping conditions for "is this primitive the right size"

Whenever a new fork driver lands, run it through three checks. If any fail, the primitive has likely reached its scope and the new feature wants its own layer:

1. **Trigger-agnostic.** The new caller adds an entry under `data.fork.*` and a few lines in one orchestrator. It does not edit `_fork_at_divergence`'s body, doesn't change FORK_CUT's wire shape, doesn't introduce a new ledger record kind.
2. **Override is OSP-carriable.** The fields the new caller wants to differ across branches are already (or trivially extensible to) `OptSearchPoint` fields. If the diff is a different pipeline shape or a different scoring formula, that's a layer above the L1-surface primitive.
3. **No data fracture.** The new caller doesn't introduce a parallel persistence directory or a duplicate of something already in `library/`, `trials/`, or the ledger.

The current primitive passes all three for sweep, scoring-divergence, and the planned operator-rewind / LLM-rebase callers. The day a check fails, that's the signal to extend at the right layer.

---

## See also

- [Rewind and fork](../operations/rewind-and-fork.md) — operator how-to, including `optimize --sweep`.
- [The individual record](optsearchpoint-as-state.md) — what OSP carries and who writes each field.
- [Scoring and traces](scoring-and-traces.md) — the underlying split between facts (traces) and policy (scoring).
- [The three-layer loop](three-layer-loop.md) — L1/L2/L3 and the open seat L4 will fill.
