# Optimizer of the optimizer

> **Audience:** Developer reference. Operators see [`../manual/`](../manual/) for usage docs.

PromptPotter can optimize **its own optimizer prompts**. The four optimizer LLM calls — `l1_generate`, `l1_critique`, `l2_context`, `l3_plan` — are themselves prompts driven by the eight-field `PromptTemplate` scheme. Expose those template fields as a connector's `pipeline_params`, point an outer PromptPotter cycle at them, and you get **PromptPotter optimizing PromptPotter**.

This is the headline self-referential capability of M12. 
Connector: `promptpotter/connectors/promptpotter.py`. 
Demo dataset: `datasets/promptpotter-self/`. 
Spec: [`../specs/roadmap.md`](../specs/roadmap.md) § Connectors + L4 inner-cycle execution.

## Why it's interesting

Most prompt-optimization work treats the *optimizer prompt* as fixed — the L1 and critique templates are written by humans and frozen. But the optimizer prompts are prompts; they're as optimizable as any task prompt.

## What stays the same on the outer cycle

The outer cycle is just a PromptPotter cycle — same loop, escalation,
PoBB elimination, dispatch hub, dashboard. From the outer L1's
perspective it's evolving prompts whose "score" happens to be "did this
prompt make the inner loop converge faster"; the self-reference is only
visible when you read what the prompts are about. By design, the
connector boundary keeps the outer cycle provider-agnostic: today
TermNorm or PromptPotter-self, and M12's registry expansion doesn't
change the outer cycle.

## The outer fitness

Each outer "sample" runs an inner PromptPotter cycle on proxy benchmarks. 

**Still under exploration is the tuning of the measure (fitness) that quantifies the performance of the inner rounds.** A rudimentary measure could be improvements per searchpoint collected, or something derived from lift × quality × efficiency + the candidate-gradient law.

We are currently working with `mean_round_delta`, because for the inner campaign we use seeds and do not evolve the inner optimization prompts in the current setting; once the setting no longer needs that hack for statistics, I want to retire it.
- **`mean_round_delta`** — the MEAN, over the inner rounds, of the incumbent each round ADOPTED, minus the origin, in LOGITS on one ability ruler. Reading the round's *proposals* instead priced exploration as damage and inverted the whole metric — see `exploration.adopted_level_trajectory`. **Its definition, its bound and why it needs no denominator are the type's, not this doc's — read `domain/l4/proxies.py`.**

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

**One term, not a basket — and the reason is measured, not aesthetic.** A composition of
lift core × sustained discovery × bounded quality × efficiency was put to a full panel and every
factor beside the lift core failed the candidate-gradient bar: `cleanliness` put twice as much of
its variance into the SEED as into the arm (it graded which data a cell drew, not which optimizer
prompt ran it), `diversity_health` never left the top fifth of its range, `delta_per_dollar`
correlated ~0.96 with the lift core and flipped no ordering, and `rounds_improved_frac` flipped
nothing. Each was a *multiplier*, so each held authority over an ordering it could not justify —
and together they roughly doubled the apparent significance of a conclusion by compressing the
fitness scale. **A term that cannot move with the candidate does not get a vote.**

**The quality EVENTS still act; they simply act once, structurally.** A cycle whose every round
lost its candidates to an empty optimizer response goes to the FLOOR (`floor_reason`); a
collapsed arm is dropped from the inner election and eliminated at PoBB. Charging them a second
time, graded, inside the fitness was a second mechanism for a job the loop already does.

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


**How to re-derive all of this**, rather than trusting the numbers that happen to be in config
today: read `best_round` and the rounds actually run across the inner cycles under
`.promptpotter/.inner/` to price the round cap, and read each seed's origin accuracy off its
inner `round_0000.json` to choose which cells to keep. The live geometry is in
`inner_tasks.yaml::inner_benchmark_config` and `campaign.yaml` — those files are the source of
truth, and this page deliberately quotes none of their values.
