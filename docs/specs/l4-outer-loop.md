# L4 — self-optimization

PromptPotter optimizing **its own optimizer prompts**. The outer cycle is a normal cycle — same loop,
escalation, PoBB, dashboard — and each outer *sample* runs a whole inner campaign on a pinned benchmark
seed. Connector `connectors/promptpotter.py`; dataset `datasets/promptpotter-self/`; CLI/headless only.
The goal is a **distributable `promptpotter-self`**: an operator runs `new`, watches the optimizer improve
its own prompts, at bounded and visible cost.

**This file says what is TRUE. How to run and read one is the `potter-self` skill's job**
(`.claude/skills/potter-self/`), and every knob value is the config's (`inner_tasks.yaml` +
`campaign.yaml`) — neither is restated here.

## The measurand

`mean_round_delta` — the MEAN, over the inner rounds, of the parent each round **adopted**, minus the
origin, in logits on one ability ruler (`exploration.py::parent_level_trajectory`). `campaign.yaml::scoring`
re-anchors it `(x+1)/3`: linear, clipping nothing in the banked range, so the paired estimator's effect × 3
IS the mean logit lift — a number to read, not merely to order by.

- **Adopted, not proposed.** A round's value is what it *crowns*; the arms it discards are the price of
  finding that. For any mutation operator with mass below the parent (all of them — that is why selection
  exists) `E[mean θ] < θ_parent`, so averaging proposals reads negative for an exploring generator and ≈0
  for an inert one: exactly backwards.
- **Mean, not endpoint.** On 39 banked cells, refitting `fd[arm,seed] = μ + α + β + ε`: endpoint gives arm
  SD 0.077 against residual 0.182, the mean 0.064 against 0.134 — a 26% quieter instrument at identical
  spend, agreeing with the endpoint's arm effects at r = +0.941. Peak (0.446) and per-round slope (0.405)
  beat neither. It also matches what a healthy search looks like: lifting early and holding scores above
  reaching the same place in the last round.
- **The denominator is the round BUDGET**, holding the last adopted level forward across rounds a cell
  never ran (`domain/l4/proxies.py::parent_level_series`). Dividing by the series length makes the denominator a
  per-cell quantity, and since `inner_lives` stops a *stalling* cell, the short series is the one that
  lifted early and went quiet — it would be divided by its own brake.
- **No difficulty denominator.** Every level is a θ on ONE δ ruler shared by every cell of the panel, so two
  levels already sit on one interval scale across seeds of different origin strength. Per-cell difficulty is
  modelled where it belongs: the round-winner election and PoBB, which fit an explicit per-cell δ.
  The sharing is what makes the sentence true and is not free — `application/runner/inner/ruler.py` fits the
  scale at the outer round boundary and hands it down. A cell left to derive its own saw only its own arms
  (its evidence epoch hides the rest), so the scale came out of the treatment: measured over 107 banked
  cycles, byte-identical origin rows read at θ spread up to 1.201 logits.
- **The row carries the seed's whole trajectory, and only ONE term of it scores.** An outer cell's
  `pipeline_data` holds `mean_round_delta` (the scored measurand) plus `InnerCellFacts`
  (`domain/l4/proxies.py`): that seed's origin level, where it ended, its peak, its round count,
  its stop reason and its own spend. Those are REPORTING channels — what `evidence`'s Compare read
  and any panel may ask about a cell — and none of them is a scoring term; the bullet below records
  that peak and endpoint were measured as candidates for the measurand and lost. They exist because
  they previously reached the row only inside `reasoning_trace`'s prose, where no reader could
  compute on them, so the outer surfaces could report the scored lift and nothing else about the
  run that produced it.

- **One term, not a basket — measured, not aesthetic.** `lift × cleanliness × diversity_health × efficiency`
  went to a full panel and every factor beside the lift core failed the candidate-gradient bar: `cleanliness`
  put twice as much variance into the SEED as the arm (it graded which data a cell drew), `diversity_health`
  never left its top fifth, `delta_per_dollar` correlated ~0.96 with the core and flipped no ordering,
  `rounds_improved_frac` flipped nothing. Each was a *multiplier*, so each held authority over an ordering it
  could not justify, and together they roughly doubled apparent significance by compressing the scale.
  **A term that cannot move with the candidate does not get a vote.**
- **No term divides by cost, and this is the argument any proposal to add one must answer.** Both caches are
  content-addressed and tenant-shared — which is what makes the inner origin identical across every arm, and
  therefore what lets the paired verdict cancel the inner loop's noise. But the arm that replays is the
  *origin* arm; a variant writes different prompts, so every hash is new and it pays full freight. A cost
  denominator measures how often we have run the candidate before. At the limit a replayed cell bills zero
  and is dropped with a warning that reads like a fluke — which is how it presented: an outer origin died on
  its first cell, three inner rounds in four seconds, no spend reported.
- **Deliberately absent.** A peak / lift-and-hold reading (a one-line derivation, but it changes the
  estimand — under a pure peak ruler the origin loses). `rounds_to_N` or any declared target (it asserts up
  front how much room the benchmark has; a task the inner model looks bad at is one it has not been tuned
  for yet). The quality *events* still act — they act once, structurally: an all-empty cycle goes to
  `floor_reason`, a collapsed arm is dropped from the election and eliminated at PoBB.
- **One estimator per subtraction.** Reading the two ends of one difference through different estimators
  makes the shrinkage on the anchor move with the arm — a bias, which unlike noise does not average out over
  a panel. `_calibrate_delta_ruler` reads θ_C0 through the same conditional estimator on both branches. The
  residual anchor *wander* is measured and not worth buying out: within-seed r = +0.75, ~2% of the delta's
  variance for a ~3% spend increase.

## What a panel may claim

**An interval that excludes zero is the evidence; the ordering alone is not.** A panel that cannot separate
arms still prints a leader, and reading that leader as a finding is the failure mode this phase is most
exposed to.

- **Served at zero spend** by `application/evidence.py` (`python -m promptpotter
  evidence promptpotter-self --ranking`, `GET /evidence?subject=campaign:…&ranking=`): each arm's
  anchor-to-origin paired
  effect with its own interval, plus `EditSpread` — how far apart those effects are. Beside them, and
  answering at round 0 where the ranking cannot: whether the campaigns' levels are comparable at all
  (`ruler_id`), which arms are replicates, the cell/subject/residual decomposition against the scatter a
  subject mean shows under the null, and whether run order is confounded with outcome. Its per-round peer `PanelPrecision`
  reports one round's estimation noise beside its observed between-cell spread, off `mean_parent_level_se`.
  **Two bars, never their ratio**: the ratio shipped once as `estimation_share`, and `min(1.0, …)` rendered a
  raw 5.55 — noise claiming to exceed the spread it is a component of — as a tidy "100% measurement noise".
- **There is no within-cell noise term, by design** — and the claim is about the ESTIMATOR as much as the
  rows, which is the half it silently did not cover. The inner instrument is content-addressed, so asking
  twice replays rather than re-measures; but a replayed row still had to be READ, and while each cell fit its
  own scale the reading moved even where the rows did not. Both halves are fixed now: same rows plus the
  shared ruler above is the same θ. Manufacturing a noise term measures how noisy an LLM is on an identical
  request, which is not a quantity the loop can act on. Depth on a specific candidate is `verify`'s job — it
  re-scores on MORE samples without touching the cycle.
- **A cell that failed is not a cell that scored zero** (`scoring/selection.py::_scoreable`). The election
  grades an errored row 0.0 on purpose — the overlap guard needs that — but a published interval may not: at
  L4 a floored cell does not read as "scored nothing", it reads as "drove the inner loop maximally down".
- **Absolute outer numbers never travel across runs.** Only a candidate's delta against its OWN run's origin
  is meaningful; within a run, comparisons are paired by seed under CRN, so draw difficulty cancels.

## Invariants — break one and the corpus is void, silently

- **`connectors/promptpotter.py::_identity_config` enumerates the inner-origin fingerprint.** Read it before
  assuming a file is safe to touch: a dispatch *renderer* and an *estimator* move it exactly as an inner
  node's prompt body does. It resolves once per init, so a mid-flight edit is invisible to the RUNNING cycle
  and lands on the next `resume` — the case that silently re-partitions a corpus.
- **No knob changes mid-run.** The baselines are read per inner mint; an edit splits the run into two
  fingerprint families.
- **`max_inner_rounds ≥ 2`.** At 1 the trajectory is length-1 and the formula's two weighted delta terms
  silently double-count one measurement.
- **`lives.start` sits well below `max_inner_rounds`.** Set near it, the bank cannot drain before the
  calendar cap: every inner runs full length regardless of quality, and the geometry loses its only brake.
- **Inner-task count > `elimination_n_min`, and ≥ 6** — below that θ_se exceeds the point-lift and the
  election correctly refuses to crown.
- **CRN is the variance control, and the only one.** No replication knob beside it; an identical cell replays.
- **`terminal_node` is the LAST outer node (`l3_plan`).** An inner campaign consumes the ENTIRE outer config
  at once, so a mid-chain stamp lets prefix-trust replay serve the ORIGIN's rows to a candidate that edits a
  later node. It is not a health signal and nothing may tally it — one panel counted it and the critique spent
  an arm fixing a stall that never happened.
- **A HIT/MISS panel stays silent at L4** (`panels._miss_is_placeholder`). The outer `predicted` carries a
  proxy suffix its `ground_truth` lacks, so no cell can ever be a hit; rendered as misses, the critique
  diagnosed the artifact and steered the inner loop off its only objective. A prompt clause telling the model
  to ignore the panel is NOT the fix — one was already there, and round 1 ignored it.
- **`L1Variant` is `extra="forbid"`.** A field a prompt set declares but the model lacks fails *every* outer
  variant at validation: the Pydantic model, both `answer_format`s and `resolved_schemas` move in ONE commit.
- **`token_budget` stays `null`.** The rollup lands each inner campaign's tokens on the outer ledger as
  backend cost, so a normal-campaign token default trips after a couple of cells while the USD budget sits
  untouched. `spend_budget_usd` is the meaningful cap.

## Cost

Geometric: one outer round is `(1 origin + n_variants) × n_inner_tasks` fresh inner campaigns, each a full
campaign whose optimizer calls are individually slow. **`spend_budget_usd` is a cap, not an estimate** — and
a cap too small to finish a round buys nothing, because an unclosed round scores no candidate. This page
quotes no figure; re-measure before quoting a price to anyone.

## Open

1. **A bounded, cheap default config** — the committed `inner_tasks.yaml` + `campaign.json` must let
   `new promptpotter-self` complete at a cost an evaluator tolerates.
2. **`proxy_lift_corr ≥ 0.6` over ≥4 paired branches** — a measurement to run, not a module to write. Itself
   gated on the panel being able to resolve one arm from another.
3. **The OUTER election is unmeasured.** Round 0 holds one arm, so `p_best` cannot leave its tie and no arm
   can go negative; a round-1 election costs ~14 further cells. Until one runs, every claim about outer
   *behaviour* is untested — the inner half is what has been measured, and the 2026-08-07 fixes are verified
   on the C0 panel only. Deferred on purpose while concurrency is built; re-check on the next
   `new promptpotter-self`, not before.
4. **The arms differ less than their own intervals.** `se ∝ 1/√n`, so the cell count needed to separate two
   arms is far below what a linear intuition suggests. Closing it needs more cells, or **candidates that
   differ more than they currently do** — the cheaper lever, and the untried one.
5. **Four optimizer-prompt edits the corpus REFUTED — do not re-propose them.** Slot-steering language, an anti-same-slot clause, and a reweight of the under-cited panels: same-slot pairs are two genuine ideas; slot choice carries no signal once variant width is controlled; and non-cited panels do not underperform enough to move at their n. Fourth, **a wire `maxLength` on `prompt_fields_override`** — the target prompt's `instruction` really does grow with the round (median 216c at round 0 to 410c by round 4, max 1220c over the banked rounds), but `OPTIMIZER_PROMPT_FIELD_MAX_CHARS` sets its ceiling far above that, so the declaration is prompt text that never binds. The ceiling already reaches the one place it DOES bind — an optimizer node's own `instruction`, on the param route, which is what L4 rewrites. Growth is therefore not what starves the evidence panels; the panels that grow with round count are, and that is where to look next. Each of the four looks obvious from the round traces, which is why the refutation is written down rather than left to be re-derived.
6. **Cross-sample terms, still unbuilt.** Area-under-lift-vs-budget (reconstruct cumulative spend from
   `TokenUsageRecord.round`). Panel aggregation `mean lift − λ·std`, where `std` is cross-seed **outcome
   dispersion** and never the θ estimation SE — penalizing `theta_se` resurrects the wide-posterior-discards-
   good-candidates pathology — routed through the P3 post-aggregate formula, never the election rank key.
   PoBB-decisive promotion over inner-campaign arms (`metrics.py::elimination_p_best`).
