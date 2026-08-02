# Optimizer of the optimizer

> **Audience:** Developer reference. Operators see [`../manual/`](../manual/) for usage docs.

PromptPotter can optimize **its own optimizer prompts**. The four optimizer LLM
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

Most prompt-optimization work treats the *optimizer prompt* as fixed — the L1
and critique templates are written by humans and frozen. But the
optimizer prompts are prompts; they're as optimizable as any task prompt.
Self-optimization tests two beliefs:

1. The mutation-and-elimination loop is general enough to improve any
   prompt — including its own scaffolding.
2. The connector boundary is genuinely backend-agnostic. If
   "PromptPotter" can be a backend in the same registry as "TermNorm",
   the abstraction holds.

## The outer fitness

Each outer "sample" runs an inner PromptPotter cycle on a cheap proxy benchmark. The connector
reports **one** bounded, subset-invariant signal per inner cycle
(`domain/l4/proxies.py::compute_outer_proxies`), and `campaign.yaml::scoring` re-anchors it into
`[0,1]`:

- **`mean_round_delta`** — the MEAN, over the inner rounds, of the incumbent each round
  ADOPTED, minus the origin, in LOGITS on one ability ruler. Reading the round's *proposals*
  instead priced exploration as damage and inverted the whole metric — see
  `exploration.adopted_level_trajectory`. **Its definition, its bound and why it needs no
  denominator are the type's, not this doc's — read `domain/l4/proxies.py`.**

**Why the mean and not the last step.** Measured on the 39 banked cells of
`promptpotter-self__af6252`, refitting `fd[arm,seed] = μ + α + β + ε` under each read: the
endpoint gives arm SD 0.077 against residual 0.182; the mean gives 0.064 against **0.134** — a
26% quieter instrument for the same spend, agreeing with the endpoint's arm effects at
**r = +0.941** (19 of 21 pairwise orderings). Peak (ratio 0.446) and per-round slope (0.405)
were measured alongside and beat neither. It is also the read that matches what a healthy
search looks like: a cell that lifts early and holds scores above one that reaches the same
place in its final round, which the endpoint cannot separate at all.

The re-anchor is linear, so the paired estimator's reported effect times the window width IS the
mean logit lift — a number to read, not merely to order by.

**It used to compose four factors over eight emitted proxies** — lift core × sustained discovery
× bounded quality × efficiency. The first complete 39-cell panel then measured each of them, and
the composition did not survive contact:

- `cleanliness` put **twice** as much of its variance into the SEED as into the arm (30.9% vs
  15.4%). It was grading which data a cell drew, not which optimizer prompt ran it. This spec had
  flagged exactly that as open and left it charging pending "a run with a real optimizer prompt
  contrast"; that run happened.
- `diversity_health` never left the top fifth of its range — no candidate gradient at all.
- `delta_per_dollar` correlated **0.958** with the lift core and flipped no ordering: it was the
  lift counted a second time.
- `rounds_improved_frac` flipped nothing.

Each was a *multiplier*, so each held authority over an ordering it could not justify — and
together they roughly doubled the apparent significance of the run's conclusion by compressing
the fitness scale (pooled paired t of −4.62 against the raw term's −2.38, on identical data).

**The quality EVENTS still act; they simply act once, structurally.** A cycle whose every round
lost its candidates to an empty optimizer response goes to the FLOOR (`floor_reason`); a
collapsed arm is dropped from the inner election and eliminated at PoBB. Charging them a second
time, graded, inside the fitness was a second mechanism for a job the loop already does.

**No declared headroom.** The system holds **no target score, and no expectation of how much
room a benchmark has.** It used to: `target_score` sat in `inner_tasks.yaml`, and a
`rounds_to_N` proxy counted rounds to reach it. Both are deleted. The concept was wrong twice
over. Mechanically it was dead weight — `rounds_to_N` carried no candidate gradient (it was the
constant `len(levels)+1` whenever the target sat beyond round-budget reach), so it cancelled in
the election and reached no decision. And epistemically it was backwards: **a task the inner
model looks bad at is a task it has not been tuned for yet, not a task with a low ceiling.**
Declaring a target bakes a pessimistic guess about the ceiling into config and then measures
against the guess. The lift term is the raw climb on the ability ruler and divides by nothing at
all — so nothing was lost by deleting the target, and one whole class of assumption went with it.

**Governing law — every term must carry a candidate gradient** (vary across the optimizer prompt
candidates being compared). That law is what removed the four factors above, and it is why there
is one term rather than a tidy-looking basket.

**Deliberately not added: a peak / lift-and-hold reading.** `mean_round_delta` reads the ADOPTED
incumbent, so a peak the inner search later walked away from scores as nothing — on the banked
panel that is 17 of 39 cells, mean gap +0.052. It is a one-line derivation, and it is out because
it changes the ESTIMAND: under a pure peak ruler the origin loses. That is a decision to take on
measurement, not in passing.

Acceptance is empirical: the composed fitness must hold `proxy_lift_corr ≥ 0.6` — a term that
degrades it is cut, not kept for tidiness.

## The bill is not the cost

> **No fitness term divides by cost any more** — `delta_per_dollar` is gone (above). The two-cost
> split it forced is NOT gone: `incurred_usd` and the bill are both still tracked and both still
> reported, because they answer different operator questions. Keep this section: it is the reason
> a cost term was never salvageable as a *measurement of a candidate*, and it is the argument any
> future proposal to reintroduce one has to answer.

There is a second law hiding behind the first, and it is easy to miss because it only shows up
once the system has a history. **A fitness term must be blind to what we have already measured.**

PromptPotter's two caches — the measurement archive and the optimizer-call cache — are keyed by
content hash and shared across the whole tenant. That is deliberate and load-bearing: it is what
makes the inner *origin* identical across every outer arm (same prompt, same hash, same rows), and
therefore what lets the paired outer verdict cancel the inner loop's own noise. But it has a
consequence. Re-run an inner cycle we have run before and it **replays**: the same rounds, the
same trajectory, the same conclusion — and no money spent.

That saving is not distributed evenly across the arms, and this is the whole problem. The arm that
replays is the **origin** arm: it is the optimizer prompt we have been running all along, on the seeds
we have been running it on. A variant optimizer prompt, by construction, writes different prompts, so
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

Every factor is a live config value — read them off `datasets/promptpotter-self/campaign.yaml`
(`n_variants`, `spend_budget_usd`) and `inner_tasks.yaml::inner_benchmark_config`
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
verdict never asks "how good is this optimizer prompt in absolute terms". It asks "how does this
optimizer prompt compare to the origin, **on the same cell**", and the origin is re-measured under
whatever geometry is currently declared (changing `inner_benchmark_config` re-keys the
measurement identity, so a banked origin from the old geometry is invalidated rather than reused).
Both arms are cut off at the same round, so the comparison stays exactly fair.

What you give up is *ceiling*, and this is a real loss, not a free one. **An optimizer prompt whose
virtue is that it keeps compounding past the cap can no longer show you that virtue — and
compounding is arguably the most valuable thing an optimizer prompt can do.** Capping the inner search
is a trade we have made deliberately, to get the outer round down to a length that can be
iterated on at all; it is not a claim that the truncated rounds were worthless. It should be
revisited the moment the round is cheap enough to afford them, and it must be stated whenever a
result is reported, because a panel run under a cap cannot distinguish "this optimizer prompt stops
improving at round N" from "this optimizer prompt was stopped at round N".

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
cell, not a broken one: it is where an optimizer prompt has least room to work, and therefore where
weak ones are exposed. So when you shrink the panel, do not take the first N seeds — measure each
seed's origin (it is deterministic per seed, because the inner run is seeded), sort them, and
**drop the redundant twins**: two seeds with the same origin strength are, for panel purposes,
close to the same cell measured twice. Dropping a duplicate costs you almost nothing. Dropping
the only strong-origin cell costs you the ability to see an optimizer prompt fail.

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
`inner_tasks.yaml::inner_benchmark_config` and `campaign.yaml` — those files are the source of
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
runs real inner campaigns: each outer "sample" (an inner task in `inner_tasks.yaml`)
mints + runs a full inner PromptPotter cycle **in its own asyncio task** under a
**flat `<workspace>/.inner/<key>/` sandbox**, keyed on the owning (tenant, campaign,
cycle) (NOT physically nested —
that overflows Windows `MAX_PATH`; flat keeps it re-entrant at any depth), and the
composed fitness vector flows into the outer scoring formula. **The sandbox holds
campaign STATE only.** The two content-addressed caches (`MeasurementArchive`,
`OptimizerCallCache`) stay tenant-global via `Stores.shared_root`: their keys are
content hashes, so a hit is the same measurement by construction. Sandboxing them
made every outer cycle re-score every inner origin — and an inner origin is
stochastic, so each cycle subtracted a freshly-redrawn origin measurement (one searchpoint,
one sample set, scored 0.375 in seven cycles and 0.417 in two) from the lift it was
trying to measure. Implementation: `promptpotter/application/runner/inner/`.

**The project is now in its closing phase: ship a *distributable* `promptpotter-self`.**
The remaining work and the live-run learnings (gsm8k retired as the inner benchmark —
no headroom; `justlogic-d234` (iid mix of depths 2-4) chosen; the specialized outer prompt set's
prompt set is the gating slice; inner-spend rollup; bounded cheap default config) are
the **living finish-line plan** in [`../specs/l4-outer-loop.md`](../specs/l4-outer-loop.md)
§ Finish line — the single SoT an AI agent driving L4 reads first.
