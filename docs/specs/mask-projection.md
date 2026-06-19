# Mask — a criterion projected over the lineage

**Status:** design, pre-build. Roadmap: **Lane C8** ([`roadmap.md`](roadmap.md)
§ Lineage mask). Build bottom-up (concrete → general), one `feat(mask)` commit at
arc close. (A frontend-first attempt at the order-mask collision signal —
`pick_margin` + a `COLLISION_MARGIN_EPS` threshold owned in `SampleTrajectory.tsx`
— was dropped: the divergence boundary is projection logic and must be
backend-owned ([R-36]); the order mask is rebuilt here backend-first, see the
Migration section + Future.)

## What a mask is (the framing)

**Purpose — a backend organizing abstraction (this is the point).** The mask
exists first to keep the **backend** clean and structured: it unifies what are
otherwise scattered, ad-hoc treatments — evaluator what-if recompute,
order-collision, constraint checks, alternative projections — into **one** uniform
structure for *"an alternative criterion over the campaign's computed data, and
what carries over vs what doesn't."* Less machinery, one shape. The uniformity is in
**divergence detection** (the fold + the per-round verdict), **not** in valuation —
only the scoring verdict re-values nodes (a value face); the others are pure
predicates. The shape is one *traversal*, not one *computation*. It is **background
infrastructure** — it runs in the backend, and the operator need never know it
exists or that it is called a "mask." Any operator-facing surface (a divergence
shown in the lineage, say) is a **thin downstream consumer** of this structure,
never its reason for being. [R-36]

The **measurement archive** is the substrate — every `(searchpoint, sample) →
result` — and a campaign is a **tree**: a forest of cycles and forks, each cycle a
sequence of round-winners, branching wherever a fork rooted off a parent. It is a
*branching* time order, not a line. The realized tree is **the record**. A mask
applies a change and lets it **propagate through the record** as an alternative
projection. It is defined by a **space of features** — the dimensions it touches (a
scoring lens / which evaluators, a sample set, config / hyperparameters,
measurement order). As the change propagates, the data partitions:

- **Invariant** — facts that hold regardless: every measured `(candidate, sample)`
  result, each node's local value (accuracy, evaluators), any subtree off the
  diverging path. These carry over under the mask **unchanged**.
- **Divergent** — facts contingent on a choice the change would have made
  differently: the lineage *structure* downstream of it. Counterfactual; it does
  not carry over.

The **divergence point** is where a branch forks — the first value the applied
change can no longer carry over unchanged, the boundary between the **invariant**
prefix and the **divergent** descendant subtree. It is **emergent, not a fixed
site**: it appears wherever the change first stops carrying over, which the mask's
space determines (there is no pre-enumerated table of fork sites). The invariant
facts inside the divergent subtree stay valid — a divergent node's own value is
real, only its *position* under the mask is counterfactual. (It renders dimmed; the
dimming is presentation, not vocabulary.)

Because the lineage is a **tree**, this is **per-branch and tree-recursive**, not
linear: a divergence makes its descendant subtree divergent (later rounds + any
fork rooted *at or after* it), while a fork rooted *before* it stays in the
invariant prefix and is analyzed for its own divergence. One mask can yield several
divergence points across the tree.

This structures the backend **everywhere in the loop** computed data branches, not
only winner-selection — one shape wherever an alternative criterion could apply.
Any operator-facing surfacing (the lineage box, Milestone 1) is one thin consumer
of that structure, not the point of it.

This single partition **derives** the rest, rather than us bolting them on:

- the **value face** (fitness bars) = the *invariant* projection — every node
  revalued under the lens, valid everywhere, carries over unchanged;
- the **divergent subtree** = the *divergent* projection — lineage structure past
  divergence;
- the **one-step limit** falls out for free: at the divergence point the
  alternative option is itself invariant (it was measured), but its descendants are
  divergent (never generated) — so name the one measured alternative, claim nothing
  deeper.

Mechanically the mask is **a function, not a stored thing** — and a *higher-order*
one. The single shared piece is a tree-recursive fold,
`find_divergences(record, verdict)`, that walks the record and, at each node, asks
the **verdict** "would this have gone differently here?" — the **first** flip per
branch is a divergence; its descendant subtree is divergent. It does **not** build a
second, full alternative tree (uncomputable past the first divergence — the whole
point); it finds where the record *departs*. What varies between masks is **only the
verdict** (a strategy callable living where its math already is), never the fold. The
realized run is `find_divergences` fed the *realizing* verdict (the self-consistency
gate). `MaskSpec` is a **thin selector at the API edge** mapping a chosen criterion
to its verdict — function + strategy, not a `project_mask` that switches internally,
not a class hierarchy that re-buries the traversal per subclass.

Vocabulary: **record / mask / divergence point**; the partition is **invariant /
divergent** (the divergent subtree renders dimmed — presentation, not a coined
term). Scoring authority is backend ([R-36], [R-12]) — a mask's math lives behind
the gateway / archive and is served; the webapp renders, never recomputes.

## Design decisions (resolved 2026-06-10 — deliberate, don't relitigate)

1. **`MaskSpec` is a thin API-edge selector + `find_divergences(record, verdict)` is
   the real function — neither persisted nor addressable until a reader exists.** The
   genuine reuse is the fold (`find_divergences(record, verdict) -> {divergences,
   divergent}`) plus the verdict strategies it folds; `MaskSpec` is just the value
   that picks a verdict at the edge — the seam fork/re-run/MCTS inherit, and the
   unification that cleans the backend. **Persisted, addressable mask *identity* is deferred to
   the fork-from-divergence write-side** (its first real reader — a fork recording
   which mask). M1 is Display-only: the criterion rides the request,
   computed-then-discarded; persisting it now is a sidecar with no reader ([R-09] /
   pre-flight gate), and no-backcompat ([R-07]) makes the later promotion *additive*,
   not a rebuild — so there is nothing to pre-build for. (Revised 2026-06-10 after a
   second-opinion review caught the over-build; the *function* is first-class, the
   *entity* waits for its consumer.)
2. **A mask is a *verdict over the record*, not a discrete data-kind.** The
   divergent subtree derives from the invariant / divergent partition; the **value
   face is scoring-specific** (only the scoring verdict re-values nodes — `value_with_mask_applied`
   + the bars stay OUT of the shared fold). "Compose vs union" dissolves — a verdict
   reads whatever features it needs, and the fold is indifferent.
3. **One-step counterfactual.** At a divergence, name the alternative option (it
   was measured = invariant); its descendants are divergent (never generated);
   claim nothing deeper until a future re-run phase.
4. **Milestone 1 is a truth-revealer.** The record is the engine's *real* selection
   (composite, `round_winner_key`); the first mask is a **scoring-function swap**
   (composite ↔ accuracy / another formula). It reveals where the swapped function
   would have forked the record — what-you-now-score vs what-decided.
5. **Full loop (hypothesis).** A mask is a *testable hypothesis* about the
   selection criterion, not just a view: the model anticipates fork-under-mask →
   measure the real branch → **attribute the outcome back** (did the mask's path
   beat reality?). That closure *is* MCTS backprop. Milestone 1 doesn't build it;
   the (future) persisted MaskSpec + the ledger must not preclude recording mask→fork→outcome.
6. **One active mask** in the lineage at a time (single divergence set);
   multiple-overlaid is an additive enhancement, not a Milestone-1 constraint.
7. **Naming + scope: "divergence point", never "decision point".** The fork is
   where the operator's *applied change* stops carrying over — emergent, not the
   optimizer's choice, and not a pre-enumerated inventory of sites. "Fork point"
   was rejected: it collides with the real branched-cycle **fork** (`ForkSpec`),
   and the write-side is literally *fork-from-divergence*. The mask is **backend
   infrastructure**, not a user-facing feature — its purpose is a clean, structured
   backend; the operator needn't know masks exist. Operator-facing display (the
   lineage divergence) is a thin downstream consumer.

**Correctness invariants:**
- **Own-set re-selection reproduces winner.py.** The realized winner selection
  compares each entity on its *own* measured set — `argmax round_winner_key(composite,
  accuracy)` over `{origin-anchor} ∪ candidates`, the candidate composites computed on
  whatever set each ran (PoBB may stop one early). It does **not** match-restrict to a
  common set; `matched_origin_stats` feeds the separate `improved`/delta gate vs
  origin, **not** the winner pick. The mask reproduces the selection, so it re-scores
  each entity on its own stored evaluator namespace — no cross-candidate sample
  matching. (Honest-data guard below keeps a not-fairly-measured candidate out.)
- **Self-consistency (free correctness test) — holds by construction.** The scoring
  verdict re-runs the *exact* realized election above, only the **formula** swapped,
  re-scoring from each entity's **stored, materialized evaluator namespace** (the
  realized composite *was* `realized_formula(evaluators)`, so the realizing formula
  reproduces it exactly — no schema, no re-run). The eligible filter is the realized
  one verbatim (`is_leader_eligible` — escalation-abort, degradation — **plus** a guard
  dropping structurally-invalid / validation-failed candidates, whose realized
  composite was force-zeroed *post-formula*, which a formula re-score over their stored
  evaluators cannot reproduce). Eligibility is a **recorded fact, invariant under a
  scoring swap**, so feeding the *realizing* criterion reproduces stored `is_winner` by
  construction — not by coincidence. The subtle failure the gate guards: a
  higher-scoring but **ineligible** candidate must NOT be named leader, or the gate
  fails on reality itself. (This also pins what a "criterion" is: scoring fn +
  eligibility rule + (for order) sample order.)
- **Non-stationary realized criterion.** The `per_round` formula can hot-swap
  mid-campaign (`campaign.json::scoring`); the diff compares against the formula in
  effect **at each round**, never a single campaign-final formula.
- **Formula-mask = proposed scoring config.** A "try formula X" mask is the
  read-side preview of the existing formula hot-swap; its write-side
  (fork-from-divergence) is a config change on existing rails — build it coherent
  with the scoring-config system, not parallel.

## The record (what's realized — never mutated)

- The round winner is `round_winner_key(composite_fitness, accuracy)` =
  **composite-first, accuracy-tiebreak** (`l1/score/winner.py:41`). The lineage
  spine + forks descend from these winners; `is_winner` marks them.
- Stored per candidate (`round_NNNN.json::scoreboard`,
  `dashboard.json::rounds[].candidates[]`): `accuracy`, `composite_fitness`,
  `evaluators: dict[str,float]`, `is_winner`, plus the `matched_origin_*` fields
  and per-sample results. The campaign `/lineage` response carries only
  `accuracy / rank / is_winner` today (`presentation/api/routers/campaigns/lineage.py`).

## Milestone 1 — the foundation: the scoring-function mask + visual clues

The first mask is **the scoring function itself**: the operator swaps it to/from
composite (composite ↔ accuracy, or another formula), and we surface where that
swap forks the record. This milestone exists to **establish the backend
foundation** — the mask abstraction + the divergence projection — on the smallest
honest case. Everything else migrates onto it afterward.

The realized winner is composite-first (`round_winner_key`). Under the swapped
scoring formula the round leader is re-elected per round (each entity re-scored on
its own stored evaluator namespace), and the **divergence point** is the first round
its leader ≠ `is_winner`. Before it the swapped formula explains the tree; at and
after it the realized descent followed the *other* formula — counterfactual under the
swap.

End to end:

- **Backend foundation** — `find_divergences(record, verdict)` + the **scoring
  verdict**, **traversing the lineage tree** (not a linear walk): the verdict re-runs
  the realized election under the swapped formula (via `value_with_mask_applied`, a
  formula over each entity's stored evaluators) and asks, per round, "is the re-elected
  leader ≠ `is_winner`?"; the fold marks the first such node's **descendant subtree**
  divergent (later rounds + forks rooted at/after it) and recurses into forks rooted
  *before* it. Returns `{divergences: [{node_key, alternative_candidate_id}],
  divergent: [node_key]}` (node key = the lineage spine key `{cycle_id}::r{round}`).
  Reads round files; **never re-runs**; in-flight round pending; nothing written.
  **Self-consistency gate (per verdict):** the fold fed the *realizing* scoring
  verdict must reproduce `is_winner` across the whole tree (incl. eligibility).
- **Served as Display** (read-after-run, no ledger write) under a **requested**
  `MaskSpec` — the criterion rides the request, computed-then-discarded, not
  persisted. **One `lens` query param** carries it: `lens=score:<formula>` |
  `lens=abort:<variant>` (mutually exclusive — one active lens; `samples` is the one
  orthogonal param that *composes* with a `score:` lens). The pre-resolution
  two-param `?mask=` + `?abort=` shape (where the edge hard-coded "abort wins") was
  collapsed into this single discriminated value. **Schema-first**:
  `docs/specs/m12-api-openapi.yaml` → projection → regenerate
  `webapp/lib/api/types.generated.ts`.
- **Frontend — minimal visual clues** at and after the divergence point: a marker
  on that node + the divergent downstream subtree dimmed, rendered from the served
  flags (no TS recompute). Just enough to read "the record forks here under this
  scoring function." Divergent nodes stay clickable; the dimming pairs with a label,
  not colour-only. Not a full feature — the foundation is the point.
  - *Record-scoped overlay seam.* The served overlay is a property of the **record**,
    not of the lineage widget — both the lineage card and the per-candidate fitness
    panel render it. So a single `LineageOverlayProvider` (`webapp/lib/lineage-overlay.tsx`)
    owns the **one** `/lineage` fetch + the lens selection; both surfaces read it via
    `useLineageOverlay()`. No widget publishes to a module global from a render effect
    (the prior `mask-store.ts` singleton is **deleted**) — one fetch, one source,
    rendered, never recomputed ([R-36]). The same fetch carries the **per-candidate**
    served values: `lens_value` (fitness under a `score:` lens) and `sample_set_accuracy`
    / `sample_set_n` (scorer-faithful accuracy + count over a `samples=` subset, for the
    fixed-sample-set bars). Closed candidates read these; the in-flight round has no round
    file yet, so its bars live-slice `dash` HIT/MISS (binary — there is no continuous live fitness to be faithful to).

**Then — validate the fold with a second real consumer.** The next mask (the
abort-ablation, below) is a *different verdict* riding the *same* `find_divergences`
fold. Be honest about the strength of evidence this gives: scoring and abort are both
**per-round predicates**, so two consumers of the *same shape* prove a **per-round
verdict fold**, not open-ended generality. The genuinely different case is **order**
(per-*step* signal, not a per-round node) — and it is **structurally unverified**,
not merely deferred: whether it can host on this fold at all is an open question, not
a scheduled migration. Constraint stays a **hypothesis**; extract either only when a
real consumer lands. Do **not** roadmap-commit a grand "migrate all 5"
([R-09]) — no-backcompat ([R-07]) makes the later extraction free, so waiting costs
nothing.

## The shape — one fold, verdict strategies

- **`find_divergences(record, verdict) -> {divergences, divergent}`** — the one
  shared tree-recursive fold. A **read-time `application/` service** the API calls
  (reads round files via a store, returns) — **not** an infrastructure
  ledger-projection (it calls into `application/scoring/`; infra can't, per [R-14]).
  It knows nothing about mask kinds.
- **Verdicts are strategy callables, each in its home.** `MaskSpec` is the thin
  API-edge value that selects one:
  - **scoring** (M1) — "re-elected leader under the swapped formula ≠ `is_winner`?"
    Calls `value_with_mask_applied`. The **only** verdict with a value face.
  - **abort** *(built — the first migration, below)* — "did a *switched-off* PoBB
    abort contributor fire here?" A log-read over `elimination_context` (no value
    face, no one-step alternative). `make_abort_verdict(suppress)` with
    `suppress ⊆ {epsilon, lock_in}`; empty = the realized config = no divergence.
  - **constraint** *(hypothesis, not built)* — "does this node's
    `pipeline_params_override` comply with the constraint?" A predicate; the
    operator's "hyperparameters not in line."
  - **order** *(deferred)* — its signal is per-*step* (`SampleOrderStep`), **not** a
    per-round node; whether it can host on this fold's node stream is **unverified**.
    Don't claim it until checked.
- **`value_with_mask_applied(evaluators, criterion) -> value | None`** in
  `application/scoring/` (next to `compute_composite_fitness`): the per-candidate
  re-evaluation the **scoring** verdict calls — a formula (`compile_round_scorer`)
  over the entity's **materialized evaluator namespace**. No schema, no measurements,
  no re-run: the round score always *was* a formula over those evaluators, so this
  reproduces the realized composite exactly and the read path needs no
  `PipelineSchema` (never persisted). **Stays out of the fold** — the later
  fitness-card value face calls this same helper, but `find_divergences` never does.
  Returns **`None`** when the criterion names an evaluator absent from the namespace —
  the *single, one-place* missing-name resolution (a `NameError` from the formula eval
  becomes `None` = "unscorable under this mask", **not** a fabricated score). The live
  round scorer stays fail-loud (broken formula = real bug); only this read-side seam
  treats a missing name as honest absence.

### The evaluator namespace — row-derivable vs. snapshot (resolved 2026-06-10)

The namespace a mask scores over is **not** simply the stored `evaluators` snapshot.
`Evaluator.from_rows` partitions the registry:

- **row-derivable** (`accuracy`, `output_compactness`, `latency_norm`, `error_rate`,
  `degraded_rate`) — pure functions of the persisted per-sample rows
  (`all_candidate_results`). The loader (`mask/load._candidates`) **recomputes** these
  from the rows at read time (`materialize_row_derivable`) and merges them over the
  snapshot, so they are present on **every** record regardless of when it was written.
  A sample-set mask filters the rows to the subset first, so the same evaluators
  (accuracy especially) re-score on the subset — this is how the sample-set mask
  composes with a `score:` formula. (This *subsumes* the old `accuracy_over_samples`
  helper — it was the `accuracy` member of this subset; deleted.)
- **snapshot-only** (recall / cache / `*_shortfall` / `pipeline_compactness` /
  self-heal / `prompt_compactness`) — need the unpersisted `PipelineSchema` / `opt_sp`,
  so they come from the stored snapshot. A formula naming one that's genuinely absent
  (a `*_recall` on a pipeline with no such node, on an older record) resolves to
  honest-absence via the `value_with_mask_applied` `None` path above.

This is the **structural** reason there is no backfill: a newer row-derivable
evaluator is never "missing" from an old record — it's recomputed from rows that were
always on disk. (The one-off `backfill_output_compactness.py` that rewrote stored
round files / `dashboard.json` is **deleted** — it violated *Record unchanged* below.)

## First migration — the abort mask (a second verdict, same fold)

The sample-scoring loop stops measuring early via **two posterior-based abort
contributors** (`pobb/elimination/checks.py`): **A = ε-elimination**
(`pobb_should_stop` — drop a candidate whose posterior-of-being-best falls below ε)
and **B = lock-in** (`_leader_locked` — stop once the leader's `p_best` clears
`lock_in`). The realized run fires some variant; the recorded
`ScoredCandidate.elimination_context` = `{p_best, epsilon, leader_locked, …}` says,
per round, **which contributor fired** (`leader_locked` discriminates B from A).

**This is a real read-side mask, and the cleanest second consumer (BUILT).** The
abort only changes *what gets measured from the point it first fires* — so up to that
round, every variant took the *identical* measurements (**invariant**, zero re-runs).
The verdict is `make_abort_verdict(suppress)`: per round, did a *suppressed*
contributor fire (read off `MaskCandidate.abort`, which the loader classifies from
`elimination_context.leader_locked` — `lock_in` vs `epsilon`)? The first such round is
the divergence; the rest is **divergent**. It names **no** one-step alternative — the
suppressed-abort continuation was never measured. Exposed at the API edge as
`?lens=abort:<variant>` (`epsilon_off` / `lock_in_off` / `all_off`).

It rides the *same* `find_divergences` fold (per-round node stream), proving the shape
with a genuinely different verdict (a log-read, no value face). **Self-consistency
(abort):** `suppress = ∅` is the realized config ⇒ zero divergences; and the loader's
`abort` classification reproduces which contributor cut each candidate.

**The computable boundary (honest).** *Suppressing* a contributor that **did** fire is
fully record-computable (we know each firing's type). *Adding* a contributor the
realized run **lacked** (e.g. "what if lock-in too" on an ε-only run) is **not** — it
needs the per-step `p_best` trajectory (`PoBBStreamView`), not the per-candidate
`elimination_context` snapshot, because lock-in keys off the *leader's* posterior, and
the leader isn't eliminated. That case is the actual none/A/B/combo *run* — a real,
measured sibling cycle via the existing policy-scope config + branch-from-origin path
([R-22]) — **not** part of this read-side mask.

## Future (beyond this arc — the write-side)

- **Fork-from-divergence under a mask** — the write-side: mint a fork at the
  divergence point carrying the mask as the new criterion-of-record; it becomes a
  **Control-remote** command (declare schema in `m12-api-openapi.yaml` before the
  handler). **This is where persisted, addressable mask identity lands** (the fork
  records *which* mask — the first reader that justifies storing one; see
  decision #1). This is the one honest way to "follow the divergence": the alternative
  branch only ever materializes as a **real, measured** fork the operator chose to
  run — never a stored forecast tail (which would be counterfactual fiction).
  - *Substrate note (the deferred hard part).* The existing operator-fork seam
    roots at **round + candidate** (`ForkSpec.from_round`/`from_candidate_id`),
    requires `fork_from_round=0`, carries forward only the edited searchpoint
    (`CycleSeed`), and re-runs fresh — it inherits **no measurement
    order** (the order picker re-derives each run). So an *order*-mask
    fork-from-divergence ("measure sample B next instead of A") is **not on
    existing rails**: it needs a mid-scoring write-point + an order-seed the fork
    can replay. Evaluator/sample-set/constraint masks fork on existing rails (they
    re-select a *candidate*, which the seam already supports); only the order mask
    needs the substrate change.
  - *Prior art.* This shape was solved once: `events.jsonl` as a write-ahead fork
    substrate (`3c73dbb3`, Apr 15) — one append-only stream, forks as **pointers**
    into the shared log (common prefix stored once), addressing grammar
    `cycle:round:write_point[:i[:j]]`; and a decision-replay layer (`2597ff8d` /
    `d4c99b49`, Apr 20) that replayed each recorded decision and forked at the
    **first mismatch** (gated on `inputs_ref` + `outcome`). The difference that
    matters: those branches were all real (every fork re-ran), so nothing
    counterfactual was ever stored — the same honesty the mask must keep.
- **MCTS over the lineage** — backprop reuses the re-evaluation face; UCB
  re-selection reuses the divergence machinery ([roadmap far-horizon]).

## Seam + I/O buckets

- **Read = Display, read-time + on-request.** Like the existing `/lineage`
  endpoint: the API (presentation) calls a `application/` function that reads stored
  measurements and returns — **not** a ledger-subscriber `DerivedView` (writes no
  artifact, unlike `LiveDashboardView` / `AuditTrailView`). Never mutates the
  ledger, never runs. The layering is what forces this: an infrastructure projection
  could not call `value_with_mask_applied` in `application/scoring/` ([R-14]).
- **`score_search_point()` untouched** — masks read stored measurements; not a new
  scoring path ([R-12] holds). `value_with_mask_applied` reuses the scoring kernels, doesn't add
  a gateway.
- **Future write = Control-remote** (fork-from-mask) — schema-first when it lands.
- **New seams → conventions** (not tests — the structural suite was cut, see
  [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md); each fails loud):
  "re-evaluation math only in `value_with_mask_applied`"; "`find_divergences` is the only
  divergence fold"; "no mask state is persisted" (it's a pure function — decision #1).

## Invariants

- **Honest divergence.** A node lacking the masked input ⇒ compliance *unknown* ⇒
  **not** divergent. Never fabricate a divergence from absent data.
- **Matched-sample re-selection.** Re-selection compares on the common measured
  samples, else the divergence is a sampling artifact (above).
- **One-step counterfactual.** Past a divergence the alternative subtree is
  uncomputable; name the one measured alternative winner, claim nothing deeper.
- **Record unchanged.** The realized lineage + winners never change; a mask is a
  projection on top.
- **One scoring home.** No mask math in TypeScript ([R-36]).
- **Selection unchanged.** Divergent nodes stay clickable; the dimming is opacity +
  label, never a disabled interaction.
