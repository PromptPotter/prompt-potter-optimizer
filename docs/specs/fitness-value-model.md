# Fitness value model — one tagged value, resolved once

> **Status:** design / plan. Sibling of [`fitness-comparability.md`](fitness-comparability.md)
> (that spec decides *which statistic gates* — θ; this one decides *how every
> fitness number is represented and resolved* — a single tagged value). They are
> complementary and non-overlapping: comparability is about the estimator, this is
> about the plumbing and the type. Prerequisite ordering is loose — the slices here
> are independently shippable and green.

## Why — fitness is a bare float with four hidden bases

A candidate's "fitness" is passed around as a naked `float` / `number | null`
everywhere. That bare number silently carries **one of four incompatible bases**,
distinguished only by the field name it happens to live in:

1. **subset** — per-round, signal-chased sample draw (`ScoredCandidate.accuracy` /
   `.composite_fitness`, `RoundSummary.accuracy`). Difficulty-blind; swings
   ±0.2–0.3 round-to-round under `per_round_resubset`.
2. **matched** — candidate ∩ origin cells (`matched_origin_accuracy` / `_composite`,
   `paired_fitness`). The apples-to-apples pair the `p_value`/PoBB run on.
3. **cumulative** — cross-round growing frontier (`cumulative_accuracy` /
   `cumulative_theta`, `CycleRoundState.best_*`). The honest trend series.
4. **ability θ** — difficulty-adjusted logit (`theta`, `origin_theta`, `best_theta`).
   **The actual decision basis** (election, elimination, promotion gate) per
   [`fitness-comparability.md`](fitness-comparability.md) — yet the *headline* numbers
   stored and served are subset/cumulative composite, so the displayed rank and the
   elected winner can legitimately disagree.

On top of the four bases sits the **composite-or-accuracy** question ("is a formula
active, or is this plain accuracy?"), which is re-answered ad hoc at every site.
The result is coherence held together by prose comments, not types.

### The concrete sprawl (evidence, file:line)

**Backend — the one composite-or-accuracy rule is resolved in five shapes:**

| Site | Shape | Verdict |
|---|---|---|
| `domain/rendering.py:28` `display_fitness` | `composite if composite is not None else accuracy` | **canonical** — the intended sole resolver |
| `presentation/views/live/phase.py:67` | hand-inlined re-implementation (does not import the helper) | duplicate |
| `application/optimization/cycle.py:667` | `subset_scores.get("composite_fitness", accuracy)` | `.get`-default variant |
| `presentation/api/routers/campaigns/lineage.py:207` | serves `composite_fitness` as `None`, defers resolution to the webapp | off-backend resolution |
| `verify.py:149,246` · `noise_floor.py:154,174` · `output/review.py:45,310` · `l1/stats.py:111,113` · `intelligence/indexes/axis.py:408` · `views/ingress.py:145` | `... or 0.0` (absent → **0.0/None**, the *opposite* of "→ accuracy") | see slice 1 audit note |

Second, unrelated composite-fallback (matched-vs-origin composite, not
composite-vs-accuracy): `presentation/views/render/text.py:177-181` — flag, don't
fold into the same resolver.

**The four bases are stored as sibling bare floats** on `RoundResult` /
`RoundSummary` / `ScoredCandidate` (`accuracy` + `matched_origin_accuracy` +
`cumulative_accuracy` + `theta`) — nothing but the field name says which pool each
sits on, so the trend chart, the "Best" tile, and the candidate rows each pick the
right sibling by convention (`domain/results.py:517-537` docstring).

**Webapp — seven independent "which number" axes, no `Fitness` type.** Every value
is a bare `number | null` (`types.generated.ts`, `lib/types/candidate.ts`). The
composite-or-accuracy fold is hand-written 5× and the winner/loser basis-fold 2×:

- `?? accuracy` sites: `lib/chat/activity.ts:77`, `whatif/FitnessRankSummary.tsx:56`,
  `dashboard/lineage/useLineage.ts:241` and `:252-253`.
  (`whatif/useFitnessBars.ts:61` is a *slice-mode* fallback, not the composite rule —
  keep distinct.)
- winner/loser cumulative-vs-subset basis fold: `lib/derivations/round-candidates.ts:97`,
  `useLineage.ts:240-244` & `:251-253`.
- `headline_metric` toggle (accuracy/composite/ability): `headline-stats.ts:23`,
  `poll.tsx:64`, `useLineage.ts:159-161`, `FamilyTree.tsx`, `Forest.tsx` — reaches
  only the lineage tree; the other six axes each re-pick a basis independently.

Good news, so we don't over-scope: the old client `displayFitness` re-implementation
is **genuinely gone** (one grep hit, a comment pointing at the backend), and the
frontend already consumes `evaluators_meta()` over the wire — the frontend is
*echoes* of the backend rule, not a parallel engine.

**Evaluator short-codes — the one genuine hand-synced duplication.**
`shared/composite.py` re-types evaluator names in **five** maps that no code derives
from the registry, and the file's own docstring (`:11-12`) admits "keep in sync by
hand":
- `_SHORT_DIRECT` (`:36`), `_SHORT_CODE_RE` regex (`:42`), the `H` aggregate name-list
  (`:80`), the `R` aggregate name-list (`:88`), `SHORT_NAMES` (`:136`) — **two
  disagreeing vocabularies** (`H`/`R` rolled-up vs `err`/`degr`/`rf` un-rolled), plus
  the `"accuracy"`/`"acc"` literals in `evaluators.py:505,513`.
- The registry→composite→gateway spine *above* this is clean (`_REGISTRY` is single-
  source; `materialize_*` / `compute_composite_fitness` / `compile_round_scorer` /
  `value_with_mask_applied` all resolve names dynamically, fail-loud on unknown).

**Mask / lens / what-if — already ~80% unified; one honest exception.**
`active` / `what-if` / `lens` / `measured-vs-all` are *one* mechanism wearing four
names: one value seam `value_with_mask_applied` (`metrics.py:560`), one resolver
(`display_fitness`/`round_winner_key`), one served overlay, one divergence fold
`find_divergences` (`mask/divergence.py:69`). They differ only in `(formula, basis)`.
**Do not touch the exception:** A/B **replay** (`ab_replay.py:96`,
`replayers.py:135`) re-derives *decisions* over per-sample measurements using the
**real** θ rules (`elect_round_winner`, `elimination_p_best`), produces a decision
diff not a value, and needs inputs the mask record deliberately discards. It has no
value face to tag; forcing it into the value model would make it lossy. Keep the two
divergence folds separate exactly as [`mask-projection.md`](mask-projection.md)
decision 7 already declines to merge them.

## Decision — a single tagged `Fitness`, resolved at the serving boundary

Model fitness as one frozen value object, named abstract and specialized by two
discriminants:

```python
# domain/rendering.py  (folded in beside display_fitness — a new module would have
# raised the complexity ledger; this is the declared home of the resolution rule)
class Metric(StrEnum):     # what statistic
    ACCURACY = "accuracy"
    COMPOSITE = "composite"
    ABILITY  = "ability"   # θ — a logit, not a %
class Basis(StrEnum):      # over which sample pool
    SUBSET = "subset"
    MATCHED = "matched"
    CUMULATIVE = "cumulative"

class Fitness(BaseModel, frozen=True):
    value: float
    metric: Metric
    basis: Basis

    @classmethod
    def resolve(cls, composite: float | None, accuracy: float, basis: Basis) -> "Fitness":
        """The composite-or-accuracy rule, in ONE place. An honest 0.0 composite
        survives; only None (no active formula) degrades to accuracy."""
        return (cls(value=composite, metric=Metric.COMPOSITE, basis=basis)
                if composite is not None
                else cls(value=accuracy, metric=Metric.ACCURACY, basis=basis))
```

`Fitness.resolve` **is** `display_fitness` — but now it is a constructor you cannot
forget to call, and the tag it returns makes the basis explicit so no downstream
consumer re-picks a sibling float. θ is built directly (`metric=ABILITY`); the
render layer switches on `metric` to choose `%` vs logit — exactly what
`fmtHeadlineValue` (`headline-stats.ts:54`) already does implicitly, made explicit.

**Resolve once, own the rule once.** `resolve_fitness_value` (`domain/fitness.py`) is
the single implementation of composite-or-accuracy; the backend resolves at its
boundary (Slice 1) so `lineage` serves settled numbers, and the client picks fields
through one `pickFitness(metric, basis)` projection (Slice 3) — **no consumer runs
`?? accuracy` again.** The tag is the *vocabulary* that names each cell; it is not
nested per-number over the wire (see Slice 2 root-finding — the basis fields are
distinct data, not copies).

This is not a new abstraction bolted on — it is the abstraction the five backend
resolvers and seven webapp axes are each a partial, untyped copy of. The pass moves
the total concept count **down**: five resolution shapes → one rule-owner; the
implicit "which cell?" at every site → one named `(metric, basis)` vocabulary; five
short-code maps → one `SHORT_NAMES` table. What it deliberately does NOT do is delete
the distinct-basis scalar fields — those are real, differently-based data.

## Slices (each independently shippable + green)

### Slice 0 — evaluator short-codes: five maps → one source *(SHIPPED)*
Isolated, pure-display, **zero behavior change** — the proving ground for
"derive, don't duplicate."

**As-built note — the single source is `shared/composite.py`, not a field on
`Evaluator`.** The plan first proposed `short_code` on the `Evaluator` dataclass,
but the vocabulary has three caller layers (application `views`, presentation
`live`, infrastructure `live_dashboard`) and **infrastructure must not import the
application-layer registry** (verified: `infrastructure/` imports nothing from
`application/`). Putting the codes on `Evaluator` would force exactly the backward
seam this rework removes. So the vocabulary stays in `shared/` — the one leaf all
three layers already import — as data, and `evaluators.py` *imports down* into it.

- `SHORT_NAMES` (`shared/composite.py`) is the one full→short table; `_SHORT_DIRECT`,
  `_SHORT_CODE_RE`, and the duplicate `SHORT_NAMES` block are gone — the inversion,
  the code regex, and the direct-code map all derive from it.
- `H` (health) / `R` (recall) are declared once as `AGGREGATES` (member list +
  reduce direction) — **display aggregates over the evaluator dict, not `_REGISTRY`
  evaluators** (adding them to the registry would materialize and serve them =
  behavior change, violating the zero-change constraint).
- `default_per_round_formula_short` derives via `to_short_formula(default_per_round_formula(…))`
  — no hand-synced `"acc"` literal.
- **Done:** adding/renaming an evaluator's short code is one edit in `SHORT_NAMES`;
  both renderers + the inliner read that one table; the two vocabularies are one.
  Gate green (ruff/mypy/pytest); output byte-identical (`H`/`R`/short verified).

### Slice 1 — backend single resolver: five shapes → one *(SHIPPED)*
`display_fitness` (`domain/rendering.py`) is the one scalar resolver everything routes
through; `Fitness.resolve` (Slice 2) wraps it, not replaces it.

- **Routed to `display_fitness`** (the composite-or-accuracy question — absent → accuracy):
  `phase.py:67` (was a hand-inlined copy), `cycle.py:667` (`.get`-default variant),
  `lineage.py:207` (now resolves **server-side** — the wire carries a settled number;
  verified webapp-safe: `usesComposite` keys off the headline toggle, no `composite ===
  null` branch), and the diagnostic fitness fields `noise_floor.py:154,174` +
  `verify.py:149,246` + `axis.py:408` (leaderboard sort key). All were dormant in
  practice (default formula `accuracy` always yields a composite key) so behavior is
  unchanged; the win is intent + robustness (a present-`None` composite no longer 0.0s).
- **Kept as `or 0.0`** — genuine absent-is-zero, *not* the composite-or-accuracy question,
  so they must NOT route: `l1/stats.py:111,113` (lift baseline — the spec's canonical
  keep), `output/review.py:45,310` (raw-composite render + origin-baseline for lift),
  `ingress.py:145` + `writers.py:280` (view-field defaults). The disambiguation is now
  structural: `display_fitness(...)` = the rule; a bare `or 0.0` = a zero default.
- `text.py:177-181` (matched-vs-origin composite) left as its own named thing.
- **Done:** exactly one function answers "composite or accuracy?"; the None→accuracy vs
  None→0.0 contradiction is gone (each intent is now visible in the call shape); `lineage`
  serves a resolved number. Gate green (ruff/mypy/pytest/deptry).

### Slice 2 — the tagged type + the one rule-owner *(SHIPPED)*

**Root-finding that reshaped this slice: the sibling floats are NOT redundancy, so
the tag is NOT nested over the wire.** `RoundSummary`'s own docstring settles it —
top-level `accuracy`/`composite_fitness` are the winner's **subset**, `cumulative_*`
is the cross-round **frontier**; a candidate legitimately carries a subset accuracy
AND a matched-origin accuracy AND a cumulative frontier AND θ. These are distinct
`(metric, basis)` cells the trend chart / "best" tile / paired PoBB each need — not
copies. Nesting a `Fitness` object per number would *add* verbosity and delete no
field (fails the surface-ledger test). So the served models keep their scalar basis
fields; **no TS regen, no wire change.**

What landed:
- `Metric` (accuracy/composite/**ability** θ), `Basis` (subset/matched/cumulative),
  `Fitness{value,metric,basis}` + `Fitness.resolve`, and `resolve_fitness_value`
  which **owns the one composite-or-accuracy rule** — all **folded into
  `domain/rendering.py`** beside `display_fitness` (a dedicated module would have
  raised the complexity ledger; rendering.py already declares itself the home of "the
  one composite-or-accuracy resolution").
- `display_fitness` (`domain/rendering.py`) is now a thin alias delegating to
  `resolve_fitness_value` — exactly one implementation of the rule, under the
  widely-imported name.
- Silent-harm guard in `test_numerics.py` §3 (`test_fitness_resolve_keeps_honest_zero_composite`):
  0.0 composite survives, only None degrades, tag is COMPOSITE/ACCURACY/ABILITY.
- **The real payoff is vocabulary:** docs + code can now say "(composite, subset)"
  by name instead of re-explaining "subset-relative, swings round-to-round" prose at
  every field. Gate green (ruff/mypy/pytest).

### Slice 3 — webapp: the picks in one place *(SHIPPED)*
Client-side consolidation, not a wire migration (no served tag — Slice 2). Slice 1
already made `lineage` serve a resolved composite, so the echoes were inert.
- Added `webapp/lib/fitness.ts` with the two picks: `resolveComposite` (mirror of
  `resolve_fitness_value`) and `accuracyBasisValue` (winner→cumulative, loser→subset).
  **Did NOT add a `Metric`/`Basis` type** — `HeadlineMetric`
  (`headline-stats.ts`) is already single-source; a parallel type would be the
  redundancy this rework removes.
- Routed the 4 `?? accuracy` echoes (`activity.ts`, `FitnessRankSummary.tsx`,
  `useLineage.ts` ×2) through `resolveComposite`, and the 2 winner/loser basis-folds
  (`useLineage.ts` ×2) through `accuracyBasisValue`. Left the slice-mode fallback
  (`useFitnessBars.ts`) and the chart null-default (`FitnessChart.tsx`) — not the rule.
  `round-candidates.ts:97` stays: it *populates* the `cumulative_accuracy` field, it
  doesn't re-pick the value.
- **Done:** no client re-implements composite-or-accuracy or re-picks the winner/loser
  basis inline. Gate green (`tsc --noEmit` clean, `next build` clean).

### Slice 4 — mask Family A: already one seam; kill the last redundant path *(SHIPPED)*
Family A was already unified in code — `active`/`what-if`/`lens`/`measured-vs-all` all
route through the one value seam `value_with_mask_applied` (formula over the stored
evaluator namespace) + `round_winner_key` (the point-estimate ordering) + the one fold
`find_divergences`. So there was no value-projection sprawl to collapse — but there was
a **redundant import path**: `round_winner_key` (defined once in `domain/rendering.py`)
was re-exported through `l1/score/winner.py` → `l1/score/__init__.py`, and `mask/verdicts.py`
tapped the middle of that chain while two presentation sites already imported it direct.
Collapsed: `mask/verdicts.py` now imports from `domain.rendering`; the two dead
re-exports (+ their `__all__` entries) are gone.
- Family B (replay — `ab_replay.py`/`replayers.py`) stays separate by design: decisions
  over per-sample measurements under the real θ rules, no value face. Documented here and
  in [`mask-projection.md`](mask-projection.md) decision 7 (the two divergence folds stay
  unmerged).
- **Done:** one `round_winner_key`, imported direct from its definition; the four
  value-projection names already resolve through one seam; replay is the documented
  outlier. Gate green.

## Non-goals

- **Replay / Family B collapse** — out of scope by design (see the exception above).
- **Changing which statistic gates** — that is [`fitness-comparability.md`](fitness-comparability.md)'s
  domain (θ). This spec changes *representation*, not the estimator.
- **Changing the scoring backend** — read-only, unchanged.

## Named seams (verified against the tree; not edited by this spec)

| Concern | File |
|---|---|
| Canonical resolver (becomes `Fitness.resolve`) | `domain/rendering.py:28` `display_fitness` / `:38` `round_winner_key` |
| Duplicate/ad-hoc resolvers to fold | `presentation/views/live/phase.py:67`; `application/optimization/cycle.py:667`; `presentation/api/routers/campaigns/lineage.py:207` |
| `... or 0.0` family to classify | `verify.py`, `noise_floor.py`, `output/review.py`, `l1/stats.py`, `intelligence/indexes/axis.py`, `views/ingress.py` |
| The four bases stored as sibling floats | `domain/results.py` `RoundResult`/`RoundSummary`/`ScoredCandidate`/`RoundSummaryCandidate`; `application/optimization/cycle.py::CycleRoundState` |
| Composite compute (writer of served composite) | `application/scoring/metrics.py:249` `compute_composite_fitness` |
| Evaluator registry + short-code source | `application/scoring/evaluators.py` `_REGISTRY`, `Evaluator` (`:288`), `default_per_round_formula_short` (`:513`) |
| Short-code maps to collapse | `shared/composite.py` `_SHORT_DIRECT` (`:36`), `_SHORT_CODE_RE` (`:42`), H/R lists (`:80`,`:88`), `SHORT_NAMES` (`:136`) |
| Mask value seam (Family A) | `application/scoring/metrics.py:560` `value_with_mask_applied`; `application/mask/verdicts.py:23`; `mask/divergence.py:69` `find_divergences` |
| Replay (Family B — keep separate) | `application/optimization/resume_and_fork/ab_replay.py:96`; `replayers.py:135` |
| Webapp `?? accuracy` echoes | `lib/chat/activity.ts:77`; `whatif/FitnessRankSummary.tsx:56`; `dashboard/lineage/useLineage.ts:241`,`:252-253` |
| Webapp headline toggle | `lib/derivations/headline-stats.ts:23`; `useLineage.ts:159-161`; `FamilyTree.tsx`; `Forest.tsx` |
| Webapp bare-float types | `lib/api/types.generated.ts`; `lib/types/candidate.ts` |

## Validation

A wrong fitness is invisible (silent-harm). Guard the collapse with: (a) a backend
test that `Fitness.resolve` keeps an honest `0.0` composite (does not degrade to
accuracy) and only degrades on `None`; (b) a test that the four bases round-trip
through the tag without a consumer needing the field name; (c) the existing webapp
derivation tests (`headline-stats.test.ts`, `sample-set.test.ts`) stay green after the
`?? accuracy` deletions — if they pass with the fallbacks removed, the fallbacks were
inert (the claim the comments make, now proven not asserted).
