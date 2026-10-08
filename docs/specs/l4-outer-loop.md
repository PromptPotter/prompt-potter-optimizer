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
origin, in logits on one ability ruler (`exploration.py::parent_level_trajectory`). `campaign.yaml::scoring.per_sample`
re-anchors it `(x+1)/3`: linear, clipping nothing in the banked range, so the paired estimator's effect × 3
IS the mean logit lift — a number to read, not merely to order by. `scoring.per_cell` then weighs each cell
by its wall clock against a fixed anchor; the reason is the comment beside it in `campaign.yaml`.

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
  per-cell quantity, and since a panel's `lives` (`inner_depth_nodes`) stops a *stalling* cell, the short series is the one that
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
  that peak and endpoint were measured as candidates for the measurand and lost.

- **One term, not a basket — measured, not aesthetic.** `lift × cleanliness × diversity_health × efficiency`
  went to a full panel and every factor beside the lift core failed the candidate-gradient bar: `cleanliness`
  put twice as much variance into the SEED as the arm (it graded which data a cell drew), `diversity_health`
  never left its top fifth, `delta_per_dollar` correlated ~0.96 with the core and flipped no ordering,
  `rounds_improved_frac` flipped nothing. Each was a *multiplier*, so each held authority over an ordering it
  could not justify, and together they roughly doubled apparent significance by compressing the scale.
  **A term that cannot move with the candidate does not get a vote.**
- **No term divides by dollar cost, and this is the argument any proposal to add one must answer.** Both caches are
  content-addressed and tenant-shared — which is what makes the inner origin identical across every arm, and
  therefore what lets the paired verdict cancel the inner loop's noise. But the arm that replays is the
  *origin* arm; a variant writes different prompts, so every hash is new and it pays full freight. A cost
  denominator measures how often we have run the candidate before. At the limit a replayed cell bills zero
  and is dropped with a warning that reads like a fluke.
- **Deliberately absent.** A peak / lift-and-hold reading (a one-line derivation, but it changes the
  estimand — under a pure peak ruler the origin loses). `rounds_to_N` or any declared target (it asserts up
  front how much room the benchmark has; a task the inner model looks bad at is one it has not been tuned
  for yet). The quality *events* still act — they act once, structurally: an all-empty cycle goes to
  `floor_reason`, a collapsed arm is dropped from the election and eliminated at PoBB.
- **One estimator per subtraction.** Reading the two ends of one difference through different estimators
  makes the shrinkage on the anchor move with the arm — a bias, which unlike noise does not average out over
  a panel. `calibrate_delta_ruler` reads θ_C0 through the same conditional estimator on both branches. The
  residual anchor *wander* is measured and not worth buying out: within-seed r = +0.75, ~2% of the delta's
  variance for a ~3% spend increase.

## What a panel may claim

**An interval that excludes zero is the evidence; the ordering alone is not.** A panel that cannot separate
arms still prints a leader, and reading that leader as a finding is the failure mode this phase is most
exposed to.

- **Served at zero spend** by `application/evidence/` (`python -m promptpotter
  evidence promptpotter-self --ranking`, `GET /evidence?subject=campaign:…&ranking=`): each arm's
  anchor-to-origin paired
  effect with its own interval, plus `EditSpread` — how far apart those effects are. Beside them, and
  answering at round 0 where the ranking cannot: whether the campaigns' levels are comparable at all
  (`ruler_id`), which arms are replicates, the cell/subject/residual decomposition against the scatter a
  subject mean shows under the null, and whether run order is confounded with outcome. Its per-round peer `PanelPrecision`
  reports one round's estimation noise beside its observed between-cell spread, off `mean_parent_level_se`.
  **Two bars, never their ratio**: a ratio clamped at 1.0 renders noise exceeding the spread it is a
  component of as a tidy "100% measurement noise".
- **There is no within-cell noise term, by design** — and the claim covers the ESTIMATOR as well as the
  rows. The inner instrument is content-addressed, so asking twice replays rather than re-measures, and a
  replayed row is READ on the shared ruler above: same rows, same θ. Manufacturing a noise term measures how noisy an LLM is on an identical
  request, which is not a quantity the loop can act on. Depth on a specific candidate is `verify`'s job — it
  re-scores on MORE samples without touching the cycle.
- **A cell that failed is not a cell that scored zero** (`domain/scoring.py::is_graded`). An outer cell carries no
  label, so an errored one has no verdict. The election grades it 0.0 on purpose — the overlap guard needs that —
  but a published interval may not: at L4 a floored cell reads as "drove the inner loop maximally down".
- **Absolute outer numbers never travel across runs.** Only a candidate's delta against its OWN run's origin
  is meaningful; within a run, comparisons are paired by seed under CRN, so draw difficulty cancels.

## Invariants — break one and the corpus is void, silently

- **The inner scorer, formula, seeds and held-out cells are unreachable by the outer loop**, and none can
  ever be a `pipeline_param` — no adoption, self-adopt included, carries the instrument. Why this is a
  stated invariant rather than a property of today's code, and the evidence that keeping the instrument
  out of reach is cheap and works: [`../research/external-constraints.md`](../research/external-constraints.md)
  § Ranked, item 1 and § L4.
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
- **`terminal_node` is the outer chain's last llm node (`runner/inner/tasks.py::InnerCells.terminal`).** An
  inner campaign consumes the ENTIRE outer config at once, and off-chain nodes lead `node_configs`, so only
  a stamp at the chain's end limits prefix-trust replay to a full match; any earlier one serves the ORIGIN's
  rows to a candidate that edits a later node. It is not a health signal and nothing may tally it: a panel
  that counts it sends the critique after a stall that never happened.
- **A HIT/MISS panel stays silent at L4** (`panels._no_labels`). The cell is verifier-graded, so
  `Sample.ground_truth` is `None` and no cell can ever be a hit; rendered as misses, the critique
  diagnoses the artifact and steers the inner loop off its only objective. A prompt clause telling the model
  to ignore the panel is NOT the fix — the model ignores the clause.
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

## What the banked corpus measured

State, not rule: each figure carries its corpus and date and moves as the corpus grows, so recompute
before citing one — `python -m promptpotter evidence promptpotter-self` answers the variance and
power half on demand. How to act on them is the `potter-self` skill's.

- **Panel precision is unresolved** (73 cells / 17 arms / 6 seeds, 2026-08-15, as are the next
  seven). Split-half reliability of the arm-level mean is ~0.18 over 11 arms while the parametric
  decomposition implies ~0.7; at that corpus size neither is resolvable, so no leader read off it
  is falsifiable.
- **Pairing is what makes a comparison possible.** Seed variance runs several times arm variance: a
  typical two-arm gap resolved at ~10 paired cells (9.7) against 22.9 un-paired, on a panel that
  runs 6. The arm effect roughly doubled as the corpus grew from 39 to 73 cells.
- **The lift shape is broken early.** Cells lifting per inner round: `r1 3/6 · r2 1/5 · r3 2/3 ·
  r4 1/3`. Round 1 lifts half the time and round 2 nearly flatlines, where a healthy search lifts
  most cells in round 1 and thins after. It is an `l1_generate` defect.
- **Semantic restatement is the defect behind it.** Ten edits each asked the target to reason
  further before answering — one hypothesis, ten wordings, every one +0.000. The generator
  re-proposed about a third of the time, and `idea_fingerprint` caught 0 of 15 of those pairs.
- **Generator mechanics are clean.** Parse failures, no-ops and verbatim duplicates: zero over 6
  inner campaigns / 17 L1 rounds, `l1_yield` 1.00.
- **Round-1 truncation is a wrong proxy.** It is 2.2x cheaper per verdict and passes the per-cell
  bar (correlation 0.663) while its arm-effect correlation is 0.371, ordering 13 of 21 pairs
  against 10.5 for a coin.
- **A win is small and granular.** A winning inner round buys 1–4 rows in 28: `+0.036 / +0.071 /
  +0.107 / +0.143` are the only positive matched-parent lifts recorded.
- **Lift lands anywhere in the budget.** The round carrying a campaign's best accuracy is spread
  uniformly across it, and the strongest run peaked on its last round, still climbing.
- **Nothing has accumulated across campaigns.** Every `promptpotter-self` campaign banked so far
  carries a different `inner_origin`, so none replayed another's cells and each re-measured its
  origin under the engine revision of its day. The fingerprint was narrowed on 2026-08-15 to what
  the inner optimizer nodes resolve to plus the estimator's own source (§ Invariants,
  `_identity_config`); the mint counts the prior campaigns a novel instrument matches before the
  spend (`jobs/mint.py::_warn_on_novel_instrument`). A test pinning the fingerprint's VALUE is not
  the guard — it moved on a third of all commits and was removed twice; the prompt half is walked
  (`registry.py::renderer_modules`), so only the estimator roster
  (`connectors/promptpotter.py::measurement_modules`) can lose a member quietly.
- **Seed retirement.** Of the first six seeds three were retired, one of them on the collapse
  criterion (`seed_screen.py::rewards_collapse`); `inner_tasks.yaml` records the grounds per seat.

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
5. **Four optimizer-prompt edits the corpus REFUTED — do not re-propose them.** Slot-steering language, an anti-same-slot clause, and a reweight of the under-cited panels: same-slot pairs are two genuine ideas; slot choice carries no signal once variant width is controlled; and non-cited panels do not underperform enough to move at their n. Fourth, **a wire `maxLength` on `prompt_fields_updates`** — the target prompt's `instruction` really does grow with the round (median 216c at round 0 to 410c by round 4, max 1220c over the banked rounds), but `OPTIMIZER_PROMPT_FIELD_MAX_CHARS` sets its ceiling far above that, so the declaration is prompt text that never binds. The ceiling already reaches the one place it DOES bind — an optimizer node's own `instruction`, on the param route, which is what L4 rewrites. Growth is therefore not what starves the evidence panels; the panels that grow with round count are, and that is where to look next. Each of the four looks obvious from the round traces, which is why the refutation is written down rather than left to be re-derived.
6. **Cross-sample terms, still unbuilt.** Area-under-lift-vs-budget (reconstruct cumulative spend from
   `TokenUsageRecord.round`). Panel aggregation `mean lift − λ·std`, where `std` is cross-seed **outcome
   dispersion** and never the θ estimation SE — penalizing `theta_se` resurrects the wide-posterior-discards-
   good-candidates pathology — routed through the P3 post-aggregate formula, never the election rank key.
   PoBB-decisive promotion over inner-campaign arms (`scoring/selection.py::elimination_p_best`).
7. **Self-adopt mode — the SIFT / Darwin Gödel Machine loop, with our statistics in its gates.** Proposal,
   unbuilt, off by default. [SIFT](../research/landscape.md#sift--self-improvement-via-fast-tree-search-paper)
   and DGM let the improved agent write the next improvement; ours keeps the outer optimizer fixed. The prior
   art beside them, and what each decides for this mode, is
   [`../research/external-constraints.md`](../research/external-constraints.md) § L4. The mode:
   when an outer election is **decisive**, the winner's overrides become the outer campaign's own
   `optimization.nodes` overlay and the outer cycle forks onto it — the improved optimizer proposes the next
   round. The overlay already binds per task (`runner/entry.py::run_optimization`, task-isolated from the
   inner binding), and inner cell
   identity keys on the candidate's overrides, not the outer's own set, so banked cells survive an adoption.
   What is new is the trigger and the fork. **What is never adopted is the instrument** (§ Invariants, first
   bullet; the shared ruler with it) — which is what makes a degraded optimizer visible instead of silent.

   Every gate they set by hand maps onto a mechanism we already run:

   | SIFT / DGM | Here | Why ours is the stronger form |
   |---|---|---|
   | fixed 4-task easy gate before the 50-task evaluation | PoBB over the shared hard-first order, from `elimination_n_min` | cut on a posterior, not a pass count; hard samples first, because an easy cell carries no information about which arm is better (fishtest's lesson, [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md)) |
   | Bradley-Terry over an LLM judge's pairwise opinions | the Rasch θ/δ fit over measured outcomes | Rasch *is* a Bradley-Terry of arm against item — on outcomes, difficulty-adjusted, with an SE |
   | unevaluated node inherits its parent's accuracy | excluded, never filled | an inherited score is a measurement nobody made |
   | rank + visit-penalty parent sampling | UCB1 over backpropagated θ (`mask/backprop.py`), [`roadmap.md`](roadmap.md) § Selector members | same role; see *take* below |
   | 50-task subset → 225-task full run | sequential measurement to decisiveness; `verify` for depth on the winner | the stop is a statistic, not a subset size |
   | adopt the best node | decisive election + the held-out gate below + anchor + rollback by fork | an adoption compounds, so it is the decision that most needs an interval — and the round's best is not the best lineage ([`../research/external-constraints.md`](../research/external-constraints.md) § Ranked, item 2) |

   **Take from them.** (a) The judge as a *zero-sample prior on measurement order* — which arm PoBB walks
   first — never as a score; gated on the predicted-vs-realized reading [`roadmap.md`](roadmap.md)
   § Selector members already asks for, with their ρ≈0.68 as the bar. At L4 it pays most: here one measurement is a whole
   inner campaign. (b) Rank-based rather than value-based parent sampling: a rank needs no min-max
   normalization across forks, which is that spec's open *Normalization across forks* item.
   Read ShinkaEvolve's sampling policy before designing this
   ([`../research/external-constraints.md`](../research/external-constraints.md) § L4). (c) Expansion
   overlapping measurement ("disaggregated"), once run admission and concurrency land. (d) Their cost split —
   expansion vs judge vs evaluation per step, plus wall clock — as a reading `SpendRollup` should serve.

   **Generality is the precondition, and today the panel cannot supply it.** The panel is one benchmark
   (`inner_tasks.yaml::inner_benchmark`), its seeds different row draws of it, so a winner is an optimizer for
   that task, and adopting it compounds the specialization. The mode does not ship before these hold:
   - **A held-out panel of other task families**, scored only by the adoption gate, never by the search, and
     reaching no outer evidence panel — the moment the proposer sees it, it is training data.
   - **Worst-case, not mean:** decisive on the search panel AND non-inferior within a margin on EVERY held-out
     family. A mean lets one large in-family win hide a regression.
   - **Anchor to C0, not to the parent.** Each generation within margin of its parent still drifts; every
     generation is re-read against the default set on the held-out panel, and one that falls behind rolls back
     to the last generation that did not — a resume on an earlier fork.
   - **A leakage lint on the adopted set**: reject text carrying dataset-specific material (label values,
     sample strings, the dataset's name). Deterministic and free, before any paid gate.
   - **The self-referential claim, tested directly and rarely:** every N generations a short L4 with the
     adopted set as the OUTER optimizer against one with C0. The only reading that says "better at
     self-improvement" rather than "better at improving this benchmark".

   **The experiment it enables** is the SIFT comparison run in our own measurand, with no reimplementation of
   their code: L4 default vs L4 self-adopt, one budget, one panel, one ruler, outer lift per round. It
   inherits #3 and #4 — until the outer election is measured and arms separate, no adoption can be decisive,
   and the mode correctly does nothing.
