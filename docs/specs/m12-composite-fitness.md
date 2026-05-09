# M12: Composite Fitness Function — Multi-Objective Scoring

**Version:** 0.1.0 (spec only)
**Date:** 2026-05-09
**Status:** Spec — Phase D of the strategic-flaws milestone allocation. **No code in this phase.**
**Depends on:** `m12-promptpotter-as-connector.md` (must land first; composite fitness is a cross-connector concern)
**Cross-ref:** `m11-spend-tracking.md` (cost infra already in place)

---

## Context

Today's fitness function is one-dimensional: composite score from
`compile_scorer` (`promptpotter/application/scoring/formula.py:340`)
returns a `[0,1]`-clamped float per sample. PoBB elimination ranks
candidates on this single number.

The problem from a prompt-engineer perspective: **the score formula
ignores cost.** A prompt that's 4000 tokens vs 400 tokens with the same
score is *worse* — slower, more expensive, more brittle. Without a
multi-objective fitness function, the optimizer drifts toward verbose
hedge-everything prompts because they marginally improve recall.

Three primary axes the user wants weighted:

1. **Accuracy** (big weight) — the existing composite score.
2. **Money** — `cost_usd` rolled up per candidate.
3. **Time** — wall-clock latency rolled up per candidate.

This spec sketches the design. Implementation begins **only after**
PromptPotter-as-connector lands, because:

- Composite fitness is cross-connector (TermNorm + PromptPotter-self
  must both feed it cleanly). Designing it on a single connector bakes
  in TermNorm-shaped assumptions about cost and latency.
- The PromptPotter-as-connector inner cycles produce per-candidate cost
  and time data at the outer level — exactly the shape needed to
  validate the composite formula end-to-end.

## What's already in place

Cost and time data are **already tracked**. From the audit during the
strategic-flaws planning (`/.claude/plans/1-i-have-no-steady-beacon.md`):

- `TokenUsageRecord` (`promptpotter/domain/run_records.py:115-141`) —
  one record per LLM call with `cost_usd`, `input_tokens`,
  `output_tokens`, `duration_s`, `kind` (`optimizer` | `backend`),
  `node`, `model`, `round`.
- `dashboard.json::spend` — two-bucket rollup
  (`spend.loop.used_usd`, `spend.backend.used_usd`,
  `spend.total_used_usd`).
- `shared/spend.py` — token → USD resolver with multi-source fallback
  (wire override → LiteLLM rate cache → bundled floor).
- Recent extract `ccf7984e` (m10-pass3) consolidated spend bookkeeping
  into `infrastructure/projections/live_state.py`.

What's **missing** for composite fitness:

- **Per-candidate cost rollup.** Records are per-call today; need
  aggregation across all `TokenUsageRecord` rows tagged with a given
  candidate's evaluation history. Spans optimizer LLM calls (loop) and
  backend pipeline calls (backend) over a candidate's lifetime.
- **Per-candidate latency rollup.** Same shape over `duration_s`. Note
  per-sample wall-clock is per-LLM-call, not end-to-end pipeline; for
  a true sample-level latency, `pipeline_data.step_tokens` + a
  per-sample timer is needed (verify against existing instrumentation
  before extending).
- **A way for the scoring formula to reference these aggregates.**
  Today's `compile_scorer` namespace is built per-sample; a candidate
  rollup is one level higher.

## Design

### Per-candidate aggregates

New projection field on `LiveStateCore` (or sibling thereof) — one row
per `(candidate_id, cycle_id)` accumulating:

```
cost_usd_total      — sum of TokenUsageRecord.cost_usd for this candidate
input_tokens_total  — sum of TokenUsageRecord.input_tokens
output_tokens_total — sum of TokenUsageRecord.output_tokens
duration_s_total    — sum of TokenUsageRecord.duration_s
n_calls             — count of LLM calls scoped to this candidate
```

Source: `TokenUsageRecord` already carries `round` and (implicitly via
ledger ordering) candidate context. Need to verify the candidate id is
emitted with the record or derivable from ledger position.

### Scoring scope extension

Two layers of scoring scope:

1. **Per-sample formula** (existing) — `compile_scorer` consumes
   `result` dict. Stays as-is for accuracy.
2. **Per-candidate post-aggregate formula** (new) — runs after a
   candidate's PoBB evaluation completes. Has access to:
   - per-sample composite score (mean over the candidate's samples) —
     today's `composite_fitness`
   - `cost_usd_total`, `duration_s_total`, etc. (from above)
   - Returns a single multi-objective fitness float.

### Example operator-facing formulas

```
# Accuracy-only (today's behavior — no change for ops who don't opt in)
fitness = composite_fitness

# Cost-aware: penalize 1 cent per percentage-point reduction
fitness = composite_fitness - 0.01 * cost_usd_total

# Time-aware: 0.5x penalty when candidate exceeds 60s total LLM time
fitness = composite_fitness * (1.0 if duration_s_total < 60 else 0.5)

# All three (the user's stated weighting intent)
fitness = 0.7 * composite_fitness - 0.2 * (cost_usd_total / cost_budget) - 0.1 * (duration_s_total / time_budget)
```

`cost_budget` and `time_budget` come from `campaign.json::optimization`
(`spend_budget_usd` already exists; `time_budget_s` is new).

### Pareto-aware PoBB (longer-term — designed not committed)

PoBB today eliminates candidates whose posterior P(best) drops below
ε. With multi-objective fitness, the right elimination rule is
**Pareto-dominance**: a candidate is eliminated if there's another
candidate that's strictly better on at least one axis and no worse on
any other.

Sketch:

- Replace the scalar `score` in `posterior_best_probabilities` with a
  vector `(accuracy, -cost, -time)` (negate cost/time so larger is
  always better).
- Compute Pareto rank per posterior sample (1 = non-dominated).
- A candidate is eliminated when its posterior probability of being on
  Pareto rank 1 falls below ε.

This is M12+ work — substantially harder than the linear-combination
formula above. Linear combination delivers most of the value with no
PoBB changes.

### Visualization

Dashboard score-vs-cost-vs-time scatter (in `webapp/components/`):

- One point per candidate.
- x = `cost_usd_total`, y = `composite_fitness`, color/size = `duration_s_total`.
- Pareto frontier highlighted (candidates not dominated by any other).
- Lineage colors so children of the same parent are visually grouped.

`webapp/lib/poll.ts::DashboardSnapshot` extends to surface
`current_round.candidates[].rollup` (the per-candidate aggregates).

## Phases

### Phase 1 — surface the data (M11 wrap)

Already covered by `m11-spend-tracking.md`. Confirm
`dashboard.json::spend` continues to surface the two-bucket rollup. **No
code change for this spec; just verify the foundation is ready.**

### Phase 2 — per-candidate rollup (post-PromptPotter-as-connector)

- New projection field on `LiveStateCore` for per-candidate aggregates.
- Verify `TokenUsageRecord` carries candidate identity (or derive from ledger).
- Surface aggregates on `dashboard.json::current_round.candidates[].rollup`.
- React webapp picks up the new shape — no scoring change yet, just visualization.

### Phase 3 — multi-objective formula (post Phase 2)

- New `compile_post_aggregate_fitness(formula)` callable scope-aware of
  `composite_fitness`, `cost_usd_total`, `duration_s_total`, etc.
- New `campaign.json::scoring_post_aggregate` field (optional —
  defaults to `composite_fitness` for back-compat behavior).
- PoBB consumes the post-aggregate fitness instead of the per-sample
  composite when set.

### Phase 4 — Pareto-aware PoBB (M12+)

- Vectorize PoBB internals.
- Pareto-rank-based posterior.
- Operator opt-in via `campaign.json::optimization.pobb_mode: "pareto"`.

## Verification (when implementation begins)

- Per-candidate rollup matches sum of source `TokenUsageRecord` rows for
  any one candidate.
- Operator can write a `cost_usd_total - 0.01 * X` formula, see
  candidates eliminated more aggressively when costs balloon.
- Score-vs-cost scatter on the dashboard renders the Pareto frontier
  visibly.
- TermNorm dataset and PromptPotter-self dataset both populate the
  rollup correctly (no connector-specific assumption leak).

## Cross-references

- `m12-promptpotter-as-connector.md` — must land first; composite
  fitness is cross-connector
- `m11-spend-tracking.md` — cost data already flows
- `archive/m10-pass3` (commit `ccf7984e`) — recent spend extract that
  consolidated the bookkeeping site
- `promptpotter/domain/run_records.py:115-141` — `TokenUsageRecord` shape
- `promptpotter/application/scoring/formula.py:340` — `compile_scorer` (per-sample)
- `promptpotter/infrastructure/projections/live_state.py` — projection home
