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
(`domain/l4/proxies.py::compute_outer_proxies`), and `campaign.json::scoring` composes them so a
signal-rich campaign is never distilled to one number — **lift core × quality modulator ×
efficiency**:

- **Lift core** — `after_N_rounds_delta` (how far the inner search climbed above where it
  started, on one ability ruler), recentred `(x+1)/2` so regression < no-op < improvement stay
  distinct. **Its definition, its bound and why it needs no denominator are the type's, not
  this doc's — read
  `domain/l4/proxies.py`.** There is no `target_score` any more, anywhere; see *No declared
  headroom*, below.
- **Quality modulator** — `cleanliness · diversity_health`, floored at 0.6: discounts a
  campaign with unscoreable/degraded inner samples, malformed candidate output, or mode
  collapse — **without** diluting the lift core (a floor, not an additive term).
- **Efficiency** — `delta_per_dollar`, floored at 0.7: rewards cheap lift. It divides by the
  **incurred** cost, not the bill — see *The bill is not the cost*, below.

**No declared headroom.** The system holds **no target score, and no expectation of how much
room a benchmark has.** It used to: `target_score` sat in `inner_tasks.json`, and a
`rounds_to_N` proxy counted rounds to reach it. Both are deleted. The concept was wrong twice
over. Mechanically it was dead weight — `rounds_to_N` carried no candidate gradient (it was the
constant `len(levels)+1` whenever the target sat beyond round-budget reach), so it cancelled in
the election and reached no decision; the scoring formula had already dropped it. And
epistemically it was backwards: **a task the inner model looks bad at is a task it has not been
tuned for yet, not a task with a low ceiling.** Declaring a target bakes a pessimistic guess
about the ceiling into config and then measures against the guess. The default assumption is
that the room is large; evidence has to be unambiguous to say otherwise. The lift core is the
raw climb on the ability ruler and divides by nothing at all — so nothing was lost by deleting
the target, and one whole class of assumption went with it.

**Governing law — every term must carry a candidate gradient** (vary across the meta-prompt
candidates being compared). Two terms were retired for lacking one: the rounds-to-target counter
above; and a raw per-seed cost multiplier, which measured the *seed*, not the candidate.
Efficiency's `delta_per_dollar` passes the law precisely because its numerator is
candidate-specific — a verbose meta-prompt burns more
for the same lift. Emitted but held out of the formula until a validation run confirms their
gradient here: `rounds_improved_frac`, `delta_per_candidate`,
and `first_round_delta` (largely collinear with the lift core — `max(levels)` includes
`levels[0]`, so whenever round 1 is the best round the two terms double-count one number).

Acceptance is empirical: the composed fitness must hold `proxy_lift_corr ≥ 0.6` — a term that
degrades it is cut, not kept for tidiness.

## The bill is not the cost

There is a second law hiding behind the first, and it is easy to miss because it only shows up
once the system has a history. **A fitness term must be blind to what we have already measured.**

PromptPotter's two caches — the measurement archive and the optimizer-call cache — are keyed by
content hash and shared across the whole tenant. That is deliberate and load-bearing: it is what
makes the inner *origin* identical across every outer arm (same prompt, same hash, same rows), and
therefore what lets the paired outer verdict cancel the inner loop's own noise. But it has a
consequence. Re-run an inner cycle we have run before and it **replays**: the same rounds, the
same trajectory, the same conclusion — and no money spent.

That saving is not distributed evenly across the arms, and this is the whole problem. The arm that
replays is the **origin** arm: it is the meta-prompt we have been running all along, on the seeds
we have been running it on. A variant meta-prompt, by construction, writes different prompts, so
every one of its content hashes is new and it pays full freight. So a cost term denominated in the
*bill* does not measure the candidate at all — it measures how often we have run the candidate
before, and it hands the incumbent an advantage on precisely the cells we know best.

Nothing about this announces itself. The replayed cell simply scores as extraordinarily efficient;
at the limit it bills exactly zero, and a divisor of zero is not "infinitely efficient" but
unmeasurable, so the cell is dropped from the panel with a warning that reads like a fluke. That
is how it presented in practice: an outer origin died on its first cell because that cell had run
three inner rounds in four seconds and reported no spend at all.

So the system keeps **two costs**, and they answer different questions:

- **The bill** — money that actually left the account. Cache hits contribute nothing to it. This
  is the headline, and it is what the spend budget caps. It has to stay this way: billing a replay
  would halt a run that cost nothing to make.
- **The incurred cost** — what the search would cost to run against a cold cache, with cache hits
  priced from the tokens they recorded (the cached payloads carry them, so nothing is estimated).
  This is what a *measurement of a candidate* has to divide by.

On a cold cache the two are equal — which is exactly why this could sit undetected until the
archive got deep enough for an arm to start free-riding on it.

One term could not be saved this way. **Wall-clock has no notional twin**: a replayed cycle really
did take four seconds instead of five minutes, and there is nothing to substitute for the time it
did not spend. A lift-per-second term therefore measures the cache, and unlike cost there is no
honest divisor to swap in — so it is gone. Lift-per-unit-of-work survives as `delta_per_candidate`,
which counts candidates and is cache-invariant by construction.

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

## Sizing the panel — the two levers are not priced the same

An outer round is expensive because each cell is a whole inner campaign. There are two obvious
ways to make it cheaper — run **fewer cells**, or let each cell run **fewer rounds** — and it is
tempting to treat them as interchangeable knobs. They are not. One is nearly free and the other
is the single most expensive thing you can do to the experiment. Knowing which is which is the
difference between a cheaper run and a run that can no longer tell you anything.

**Cutting the inner round cap is close to free.** The instinct is that truncating the inner
search must bias the result — but it does not, because the truncation is *common*. The outer
verdict never asks "how good is this meta-prompt in absolute terms". It asks "how does this
meta-prompt compare to the origin, **on the same cell**", and the origin is re-measured under
whatever geometry is currently declared (changing `inner_benchmark_config` re-keys the
measurement identity, so a banked origin from the old geometry is invalidated rather than reused).
Both arms are cut off at the same round, so the comparison stays exactly fair.

What you give up is *ceiling*, and this is a real loss, not a free one. **A meta-prompt whose
virtue is that it keeps compounding past the cap can no longer show you that virtue — and
compounding is arguably the most valuable thing a meta-prompt can do.** Capping the inner search
is a trade we have made deliberately, to get the outer round down to a length that can be
iterated on at all; it is not a claim that the truncated rounds were worthless. It should be
revisited the moment the round is cheap enough to afford them, and it must be stated whenever a
result is reported, because a panel run under a cap cannot distinguish "this meta-prompt stops
improving at round N" from "this meta-prompt was stopped at round N".

So the cap should sit just past the round where improvement actually stops — a question you
answer by **measuring where `best_round` lands across the inner cycles already on disk**, not by
guessing. Set it where the tail you are cutting off is small and you know how small; never set it
because a smaller number is convenient.

**Cutting the panel is not free**, and this is the part people get wrong. The verdict is a
*paired* comparison: for each cell you take (variant − origin), and then you ask whether the
average of those differences is reliably away from zero. The precision of that answer improves
with the number of cells in two compounding ways — the average of more differences is steadier,
*and* you are more confident about how noisy the differences are in the first place (with few
cells you must widen the interval to account for not really knowing the spread; that is the
Student-t correction, and it bites hard at small n). The practical consequence is the
**minimum detectable effect**: the smallest true improvement the verdict can distinguish from
noise. Halving the panel does considerably worse than doubling that threshold. A panel too small
does not give you a wrong answer — it gives you `inconclusive` forever, which is worse, because
you pay full price for it and learn nothing.

**Which cells you keep matters more than how many.** The cells are seeds of the inner benchmark,
and they are not interchangeable: each seed draws a different sample of the underlying task, so
each has its own difficulty — its *origin strength*, how well the inner model does before any
optimization. A panel is only informative if it spans that range. A strong-origin seed is a hard
cell, not a broken one: it is where a meta-prompt has least room to work, and therefore where
weak ones are exposed. So when you shrink the panel, do not take the first N seeds — measure each
seed's origin (it is deterministic per seed, because the inner run is seeded), sort them, and
**drop the redundant twins**: two seeds with the same origin strength are, for panel purposes,
close to the same cell measured twice. Dropping a duplicate costs you almost nothing. Dropping
the only strong-origin cell costs you the ability to see a meta-prompt fail.

**One coupled knob, easy to forget.** `elimination_n_min` is the number of cells a candidate must
run before PoBB is allowed to eliminate it — the floor that stops a variant being cut on one
unlucky draw. If you shrink the panel and leave this floor where it was, you throw away most of
the saving, because a *losing* variant still has to run nearly the whole (now smaller) panel
before it can be killed. It has to come down with the panel. The floor must also stay strictly
below the panel size, or nothing can ever be eliminated and every variant pays for every cell.

**How to re-derive all of this**, rather than trusting the numbers that happen to be in config
today: read `best_round` and the rounds actually run across the inner cycles under
`.promptpotter/.inner/` to price the round cap, and read each seed's origin accuracy off its
inner `round_0000.json` to choose which cells to keep. The live geometry is in
`inner_tasks.json::inner_benchmark_config` and `campaign.json` — those files are the source of
truth, and this page deliberately quotes none of their values.

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
basic case). Implementation: `promptpotter/application/runner/inner/`.

**The project is now in its closing phase: ship a *distributable* `promptpotter-self`.**
The remaining work and the live-run learnings (gsm8k retired as the inner benchmark —
no headroom; `justlogic` high-depth chosen; the specialized `_optimizer_meta/` outer
prompt set is the gating slice; inner-spend rollup; bounded cheap default config) are
the **living finish-line plan** in [`../specs/l4-outer-loop.md`](../specs/l4-outer-loop.md)
§ Finish line — the single SoT an AI agent driving L4 reads first.
