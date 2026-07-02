# Mask — a criterion projected over the lineage

**Status:** the read-side is SHIPPED (scoring + abort verdicts on `GET /lineage`;
code is the SoT — see § Shipped read-side). This spec keeps the framing, the
design decisions, and the deferred **write-side** (fork-from-divergence, roadmap
**Lane C8**, [`roadmap.md`](roadmap.md) § Lineage mask). Divergence is projection
logic and is backend-owned ([R-36]).

## What a mask is (the framing)

**Purpose — a backend organizing abstraction.** The mask unifies scattered ad-hoc
treatments — evaluator what-if recompute, order-collision, constraint checks,
alternative projections — into **one** shape for *"an alternative criterion over
the campaign's computed data, and what carries over vs what doesn't."* The
uniformity is in **divergence detection** (one tree-recursive fold + a per-round
verdict), **not** in valuation — only the scoring verdict re-values nodes. It is
**background infrastructure**: the operator need never know it exists; any
operator-facing surface (the lineage divergence) is a thin downstream consumer.

The **measurement archive** is the substrate; a campaign is a **tree** (a
branching time order, not a line), and the realized tree is **the record**. A
mask applies a change and lets it propagate through the record, partitioning it:

- **Invariant** — facts that hold regardless: every measured `(candidate, sample)`
  result, each node's local value, any subtree off the diverging path. Carries
  over unchanged.
- **Divergent** — facts contingent on a choice the change would have made
  differently: the lineage *structure* downstream. Counterfactual; does not carry
  over. (A divergent node's own value stays real — only its *position* is
  counterfactual. It renders dimmed; the dimming is presentation, not vocabulary.)

The **divergence point** is the boundary — **emergent, not a fixed site**: it
appears wherever the change first stops carrying over. Because the lineage is a
tree, divergence is per-branch and tree-recursive: a divergence claims its
descendant subtree; a fork rooted *before* it stays invariant and is analyzed for
its own divergence. One mask can yield several divergence points.

Mechanically the mask is **a function, not a stored thing**: one shared fold,
`find_divergences(record, verdict)`, walks the record asking the **verdict**
"would this have gone differently here?" — first flip per branch is a
divergence. What varies between masks is only the verdict (a strategy callable
living where its math already is), never the fold. It does **not** build a second
alternative tree — uncomputable past the first divergence, which is the point.

Vocabulary: **record / mask / divergence point**; the partition is **invariant /
divergent**. Scoring authority is backend ([R-36], [R-12]) — mask math lives
behind the gateway / archive and is served; the webapp renders, never recomputes.

## Design decisions (the non-derivable rationale)

1. **No persisted `MaskSpec` until a reader exists.** The genuine reuse is the
   fold + the verdict strategies; the criterion rides the request
   (`?lens=…`), computed-then-discarded. **Persisted, addressable mask identity
   lands with the fork-from-divergence write-side** (a fork recording *which*
   mask — its first real reader). No-backcompat makes the later promotion
   additive, so there is nothing to pre-build for.
2. **A mask is a *verdict over the record*, not a discrete data-kind.** The value
   face is scoring-specific (`value_with_mask_applied` stays OUT of the shared
   fold). "Compose vs union" dissolves — a verdict reads whatever features it
   needs; the fold is indifferent.
3. **One-step counterfactual.** At a divergence, name the alternative option (it
   was measured = invariant); its descendants were never generated; claim nothing
   deeper until a real fork runs.
4. **Full loop (hypothesis).** A mask is a *testable hypothesis* about the
   selection criterion: anticipate fork-under-mask → measure the real branch →
   attribute the outcome back. That closure *is* MCTS backprop. Nothing built yet
   must preclude recording mask→fork→outcome.
5. **One active mask** in the lineage at a time; overlays are additive later.
6. **Naming: "divergence point", never "decision point" / "fork point"** ("fork"
   collides with the real branched-cycle `ForkSpec`, and the write-side is
   literally fork-from-divergence).
7. **Don't roadmap-commit "migrate all 5".** Scoring + abort are both per-round
   predicates, so the two shipped consumers prove a **per-round verdict fold**,
   not open-ended generality. **Order** is per-*step* (`SampleOrderStep`), and
   whether it can host on this fold is **structurally unverified** — an open
   question, not a scheduled migration. Constraint stays a hypothesis; extract
   only when a real consumer lands.

## Shipped read-side (code is the SoT)

`find_divergences(record, verdict)` + verdict strategies live in
`application/mask/` (`divergence.py`, `verdicts.py`, `record.py`, `load.py`);
`value_with_mask_applied` sits in `application/scoring/metrics.py` next to
`compute_composite_fitness` (missing evaluator name → `None` = honest absence,
never a fabricated score); the API-edge selector is `_resolve_verdict(lens)` in
`presentation/api/routers/campaigns/lineage.py` (**one** `lens` query param:
`score:<formula>` | `abort:<variant>`; `samples=` composes with a `score:`
lens); the webapp reads one fetch via `LineageOverlayProvider`
(`webapp/lib/lineage-overlay.tsx`), rendered, never recomputed. Two verdicts are
live: **scoring** (re-elected leader under a swapped formula ≠ `is_winner`; the
only verdict with a value face) and **abort** (`make_abort_verdict(suppress)` —
did a suppressed PoBB contributor fire, read off `elimination_context`; names no
one-step alternative). Composite-fitness chain + R-36: `architecture.md` §0.5.

Two non-derivable boundaries to preserve:

- **The computable boundary (abort).** *Suppressing* a contributor that did fire
  is record-computable. *Adding* one the run lacked is **not** — lock-in keys off
  the leader's per-step `p_best` trajectory, which the per-candidate snapshot
  doesn't carry. That case is a real, measured sibling cycle via the existing
  policy-scope fork path — not a read-side mask.
- **No backfill, structurally.** Row-derivable evaluators (`accuracy`,
  `latency_norm`, …) are recomputed from persisted per-sample rows at read time
  (`mask/load.py`), so they are never "missing" from an old record; snapshot-only
  evaluators resolve to honest absence. No tool rewrites stored round files.

## The write-side (deferred — Lane C8, the living contract)

**Fork-from-divergence under a mask**: mint a fork at the divergence point
carrying the mask as the new criterion-of-record — a **Control-remote** command
(declare schema in `m12-api-openapi.yaml` before the handler). This is where
persisted mask identity lands (decision 1). It is the one honest way to "follow
the divergence": the alternative branch only ever materializes as a **real,
measured** fork the operator chose to run — never a stored forecast tail.

- *Substrate note (the deferred hard part).* The existing operator-fork seam
  roots at round + candidate (`ForkSpec`), requires `fork_from_round=0`, carries
  only the edited searchpoint (`CycleSeed`), and inherits **no measurement
  order**. Evaluator / sample-set / constraint masks fork on existing rails (they
  re-select a *candidate*); only an **order**-mask fork needs a mid-scoring
  write-point + a replayable order-seed — a substrate change.
- *Prior art.* Append-only fork substrate with forks as **pointers** into a
  shared log (common prefix stored once, addressing
  `cycle:round:write_point[:i[:j]]`) + decision-replay forking at the first
  mismatch. All branches real; nothing counterfactual stored — the same honesty
  the mask must keep.
- **MCTS over the lineage** (far horizon) — backprop reuses the re-evaluation
  face; UCB re-selection reuses the divergence machinery.

## Invariants

- **Honest divergence.** A node lacking the masked input ⇒ compliance *unknown*
  ⇒ not divergent. Never fabricate a divergence from absent data.
- **Own-set re-selection reproduces `winner.py`.** The mask re-scores each entity
  on its *own* stored evaluator namespace (no cross-candidate sample matching);
  fed the *realizing* criterion, the fold must reproduce stored `is_winner`
  across the whole tree, **including the recorded eligibility filter** — a
  higher-scoring but ineligible candidate must not be named leader.
- **Non-stationary realized criterion.** `per_round` formulas hot-swap
  mid-campaign; diff against the formula in effect at each round.
- **One-step counterfactual** (decision 3). **Record unchanged** — a mask is a
  projection on top; the realized lineage + winners never change.
- **One scoring home** — no mask math in TypeScript ([R-36]); `score_search_point()`
  untouched ([R-12]); the fold is a read-time `application/` service, never an
  infrastructure ledger-projection ([R-14]); no mask state persisted (decision 1).
- **Selection unchanged.** Divergent nodes stay clickable; dimming is opacity +
  label, never a disabled interaction.
