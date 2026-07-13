# Optimizer of the optimizer

> **Audience:** Developer reference. Operators see [`../manual/`](../manual/) for usage docs.

PromptPotter can optimize **its own meta-prompts**. The four optimizer LLM
calls — `l1_generate`, `l1_critique`, `l2_context`, `l3_plan` — are
themselves prompts driven by the eight-field `PromptTemplate` scheme. Expose
those template fields as a connector's `pipeline_params`, point an outer
PromptPotter cycle at them, and you get **PromptPotter optimizing
PromptPotter**.

This is the headline self-referential capability of M12. Connector:
`promptpotter/connectors/promptpotter.py`. Demo dataset:
`datasets/promptpotter-self/`. Spec:
[`../specs/roadmap.md`](../specs/roadmap.md) § Connectors + L4 inner-cycle execution.

## Why it's interesting

Most prompt-optimization work treats the *meta-prompt* as fixed — the L1
and critique templates are written by humans and frozen. But the
meta-prompts are prompts; they're as optimizable as any task prompt.
Self-optimization tests two beliefs:

1. The mutation-and-elimination loop is general enough to improve any
   prompt — including its own scaffolding.
2. The connector boundary is genuinely backend-agnostic. If
   "PromptPotter" can be a backend in the same registry as "TermNorm",
   the abstraction holds.

## The composed outer fitness

Each outer "sample" runs an inner PromptPotter cycle on a cheap proxy benchmark. The
connector reports a **vector** of bounded, subset-invariant signals per inner cycle
(`inner_recursion._compute_proxies`), and `campaign.json::scoring` composes them so a
signal-rich campaign is never distilled to one number — **lift core × quality modulator ×
efficiency**:

- **Lift core** — `normalized_gain` (best discovered depth as a fraction of the room available
  to move it, `after_n / max(origin, 1 − origin)`, removing origin-strength bias), recentred
  `(x+1)/2` so regression < no-op < improvement stay distinct. Bounded in `[-1, 1]` **by
  construction** — levels live in `[0,1]`, so the denominator never drops below `0.5`. Its
  predecessor divided by `(target − origin)`, an *upward* room, while a regression falls
  *toward zero*; on a strong-origin seed a mild regression therefore saturated a `-1` clamp,
  and because the lift core is multiplicative that zeroed the whole cell — quality and
  efficiency signal with it. Those cells scored 0.0 for every meta-prompt: holes in the panel,
  not measurements. `target_score` no longer reaches the lift core; it survives only as the
  `rounds_to_N` threshold.
- **Quality modulator** — `cleanliness · diversity_health`, floored at 0.6: discounts a
  campaign with unscoreable/degraded inner samples, malformed candidate output, or mode
  collapse — **without** diluting the lift core (a floor, not an additive term).
- **Efficiency** — `delta_per_dollar`, floored at 0.7: rewards cheap lift.

**Governing law — every term must carry a candidate gradient** (vary across the meta-prompt
candidates being compared). Two terms were retired for lacking one: `rounds_to_N` is a constant
(`len(levels)+1`) when the inner target sits above round-budget reach, so it cancels in the
candidate election (still narrated; re-add once the target is reachable); and a raw per-seed
cost multiplier measured the *seed*, not the candidate. Efficiency's `delta_per_dollar` passes
the law precisely because its numerator is candidate-specific — a verbose meta-prompt burns more
for the same lift. Emitted but held out of the formula until a validation run confirms their
gradient here: `rounds_improved_frac`, `delta_per_candidate`, `delta_per_second`,
`after_N_rounds_delta`, and `first_round_delta` (collinear with `normalized_gain` — `max(levels)`
includes `levels[0]`, so whenever round 1 is the best round the two terms double-count one
number).

Acceptance is empirical: the composed fitness must hold `proxy_lift_corr ≥ 0.6` — a term that
degrades it is cut, not kept for tidiness.

## Cost realism

Each outer sample is at minimum a partial inner cycle. Cost compounds:

```
outer_cost ≈ outer_n_samples × outer_n_candidates × inner_n_samples × inner_n_rounds × per_call_cost
```

Every factor is a live config value — read them off `datasets/promptpotter-self/campaign.json`
(`n_variants`, `spend_budget_usd`) and `inner_tasks.json::inner_benchmark_config`
(`n_samples_per_inner_round`, `max_inner_rounds`, which must stay ≥ 2), never off this page.
Size a run before launching: one outer round is `(1 origin + n_variants) × n_inner_tasks` fresh
inner campaigns, and `spend_budget_usd` is the hard ceiling that halts it. A cap too small to
finish a round buys nothing — it stops mid-round, and an unclosed round scores no candidate.

Outer dashboard's `dashboard.json::spend` block (see
[`../adr/0003-spend-and-tenancy.md`](../adr/0003-spend-and-tenancy.md))
surfaces accumulated cost; check it before extending runs.

## What stays the same on the outer cycle

The outer cycle is just a PromptPotter cycle — same loop, escalation,
PoBB elimination, dispatch hub, dashboard. From the outer L1's
perspective it's evolving prompts whose "score" happens to be "did this
prompt make the inner loop converge faster"; the self-reference is only
visible when you read what the prompts are about. By design, the
connector boundary keeps the outer cycle provider-agnostic: today
TermNorm or PromptPotter-self, and M12's registry expansion doesn't
change the outer cycle.

## Status

**The recursion is SHIPPED & live-validated (2026-06-30).** `new promptpotter-self`
runs real inner campaigns: each outer "sample" (an inner task in `inner_tasks.json`)
mints + runs a full inner PromptPotter cycle **in its own asyncio task** under a
**flat `<workspace>/.inner/<spawn_cycle_id>/` sandbox** (NOT physically nested —
that overflows Windows `MAX_PATH`; flat keeps it re-entrant at any depth), and the
composed fitness vector flows into the outer scoring formula. **The sandbox holds
campaign STATE only.** The two content-addressed caches (`MeasurementArchive`,
`OptimizerCallCache`) stay tenant-global via `Stores.shared_root`: their keys are
content hashes, so a hit is the same measurement by construction. Sandboxing them
made every outer cycle re-score every inner origin — and an inner origin is
stochastic, so each cycle subtracted a freshly-redrawn baseline (one searchpoint,
one sample set, scored 0.375 in seven cycles and 0.417 in two) from the lift it was
trying to measure. The shared `in_process`
seam also yields the in-process `llm_only` connector (no TermNorm server for the
basic case). Implementation: `promptpotter/application/runner/inner_recursion.py`.

**The project is now in its closing phase: ship a *distributable* `promptpotter-self`.**
The remaining work and the live-run learnings (gsm8k retired as the inner benchmark —
no headroom; `justlogic` high-depth chosen; the specialized `_optimizer_meta/` outer
prompt set is the gating slice; inner-spend rollup; bounded cheap default config) are
the **living finish-line plan** in [`../specs/l4-outer-loop.md`](../specs/l4-outer-loop.md)
§ Finish line — the single SoT an AI agent driving L4 reads first.
