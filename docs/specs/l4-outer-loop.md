# L4 — self-optimization

PromptPotter optimizing **its own optimizer prompts**. The outer cycle is a normal cycle — same loop,
escalation, PoBB, dashboard — and each outer *sample* runs a whole inner campaign on a pinned benchmark
seed. Connector `application/runner/inner/connector.py`; dataset `promptpotter/assets/datasets/promptpotter-self/`; CLI/headless only.
The goal is a **distributable `promptpotter-self`**: an operator runs `new`, watches the optimizer improve
its own prompts, at bounded and visible cost.

**This file says what is TRUE. How to run and read one is the `potter-self` skill's job**
(`.claude/skills/potter-self/`), and every knob value is the config's (`dataset.yaml`'s `panel` +
`campaign`).

## The measurand

`mean_bench_lift` — the MEAN, over the inner rounds, of the headline accuracy of the prompt the search
had **adopted** by that round on the cell's **held-out** rows, minus the origin prompt's on the same rows
(`domain/l4/proxies.py::bench_level_series`, read off the cell's `each_round` bench passes). The held-out
rows are drawn beside the origin bank under the cell's seed (`application/diagnostics/seed_screen.py::draw_bench`),
marked `bench_only`, and reached by no inner node. `dataset.yaml` `campaign.scoring.per_sample` re-anchors it
`x + 0.5`, so the paired estimator's effect IS the mean held-out lift; `scoring.per_cell` may weigh each cell by
its wall clock (the reason is beside it in `dataset.yaml`).

`mean_round_delta` — the same trajectory on the SEARCH split, in logits on one ability ruler
(`application/bench/difficulty.py::DifficultyView.level_series`) — is an `InnerCellFacts` fact and scores
nothing; it is null, with `series_reason`, wherever that series differences nothing.

- **Held out, not in-sample.** The inner optimizer promotes on a point estimate over a small panel, so a
  variant that restates its parent is promoted on retest churn and moves the search split's θ a full step.
  On fresh rows that promotion is ≈0 in expectation; within-cell selection bias is inner behaviour and may
  not score.
- **Adopted, not proposed.** A round's value is what it *crowns*. Every mutation operator has mass below the
  parent, so averaging proposals reads negative for an exploring generator and ≈0 for an inert one.
- **Mean, not endpoint.** Measured as the quieter instrument at identical spend, agreeing with the endpoint's
  arm effects; peak and per-round slope beat neither. Lifting early and holding scores above reaching the same
  place in the last round.
- **The denominator is the round BUDGET**, holding the last adopted level forward across rounds a cell never
  ran (`bench_level_series`, `parent_level_series` for θ). A per-cell length would divide an early-lifting cell
  stopped by `inner_lives` by its own brake. A round whose selection pass read nothing is a hole, and a cell with
  a hole is EXCLUDED (`no_evidence_reason`), never held forward.
- **No difficulty denominator; θ is paired on one ruler.** Every θ level is on its dataset's δ ruler, shared by
  every cell measuring it (`domain/ruler.py::RulerScope`); each cell reads the tenant ruler at the version its
  outer round opened on (`infrastructure/store/archive_queries.py::CellOf`), less its own parent line. Per-cell
  difficulty is modelled in the election and PoBB. The held-out lift needs none of this.
- **The row carries the seed's whole trajectory; ONE term scores.** An outer cell's `pipeline_data` holds
  `mean_bench_lift` plus `inner`, one `InnerCellFacts` (`domain/l4/inner_facts.py`) that the recursion alone
  writes. Its fields — levels, peak, `mean_round_delta` and SE, round count, stop reason, held-out ends,
  `sent_usd` (provider bill, else our rate, so no send reads as free) — are REPORTING channels, never terms.
- **A term that cannot move with the candidate does not get a vote.** `cleanliness`, `diversity_health`,
  `delta_per_dollar` and `rounds_improved_frac` each failed the candidate-gradient bar on a full panel, and as
  multipliers they inflated apparent significance.
- **No term divides by dollar cost.** Both caches are content-addressed and tenant-shared, so the origin arm
  replays and every variant pays full freight: a cost denominator measures how often the candidate ran before,
  and a replayed cell bills zero.
- **Deliberately absent:** a peak reading (it changes the estimand), `rounds_to_N` or any declared target (it
  asserts the benchmark's room up front). Quality events act once, structurally: an all-empty cycle goes to
  `floor_reason`, a collapsed arm is dropped from the election.
- **One estimator per subtraction.** Two estimators on the two ends of one difference make the anchor's
  shrinkage move with the arm — a bias. `DifficultyView.level_series` reads θ_C0 and every later level in one fit
  on one stamp; the residual anchor wander is measured and not worth buying out.

## What a panel may claim

**An interval that excludes zero is the evidence; the ordering alone is not.** A panel that cannot separate
arms still prints a leader.

- **Served at zero spend** by `application/evidence/` (`python -m promptpotter evidence promptpotter-self --ranking`,
  `GET /evidence?subject=campaign:…&ranking=`): each arm's anchor-to-origin paired effect with its interval, plus
  `EditSpread`; beside them, answering at round 0, comparability (`ruler.id`), replicates, the
  cell/subject/residual decomposition against its null scatter, and run-order confounding. `PanelPrecision`
  reports one round's estimation noise beside its between-cell spread (`mean_parent_level_se`). **Two bars,
  never their ratio** — a ratio clamped at 1.0 renders noise as a tidy "100%".
- **There is no within-cell noise term, by design.** The inner instrument is content-addressed, so asking twice
  replays, and a replayed row reads the same θ on the shared ruler. Depth on a candidate is `verify`'s job.
- **A cell that failed is not a cell that scored zero** (`domain/scoring.py::Scorer.grade`). The election grades
  it 0.0 for the overlap guard; a published interval may not.
- **Absolute outer numbers never travel across runs.** Only a candidate's delta against its OWN run's origin
  means anything; within a run, seeds pair under CRN.

## Invariants — break one and the corpus is void, silently

- **The inner scorer, formula, seeds and held-out cells are unreachable by the outer loop**, and none can ever be
  a `pipeline_param` — no adoption, self-adopt included, carries the instrument
  ([`../research/external-constraints.md`](../research/external-constraints.md) § Ranked, item 1 and § L4).
- **`application/runner/inner/connector.py::_identity_config` enumerates the inner-origin fingerprint.** A dispatch
  renderer and an estimator move it exactly as an inner prompt body does. It resolves once per init, so a
  mid-flight edit lands on the next `resume` and silently re-partitions the corpus. Run init's preflight counts
  the prior campaigns a novel instrument matches (`application/preflight.py::_check_instrument_replays`); the
  estimator roster (`application/runner/inner/connector.py::measurement_modules`) is the half that can lose a member quietly.
- **No knob changes mid-run** — the baselines are read per inner mint; an edit splits two fingerprint families.
- **The solver's SPEND AXES are the instrument, and the inner search writes prompt text alone**
  (`application/runner/inner/tasks.py::inner_instrument_config`): model and provider are the panel's
  ENVIRONMENT axis; `reasoning_effort`, `max_tokens` and `temperature` are rails. An arm reaching one is a
  generator defect, never a finding.
- **The held-out rows are unreachable by the inner search and identical across arms**, drawn from the rows the
  origin bank left under the cell's seed. A dataset must hold `n_samples_origin + n_samples_bench` rows or its
  cells are refused before any send.
- **A held-out ENVIRONMENT is the outer bench.** `panel` marks a task `held_out: true` and the connector seats it
  as a `bench_only` outer sample (`application/runner/inner/connector.py::_extract_experiment`); `bench_trigger: each_round` grades the
  origin and each new selection there. The seat stays out of the sample's pin, and the pool declares
  `dataset_split: {bench: 0}` (`partition_bank` refuses otherwise). The lift a pool REPORTS is that held-out series.
- **The instrument is decided per CELL** (`domain/cells.py::Seat`), each running its own manifest; two cells
  running one node under different overlays refuse (`tasks.py::InnerCells.owners`).
- **θ never crosses a dataset; a lift pairs on shared cells.** A cross-dataset arm serves
  `ThetaCaveat.DISJOINT_SCOPES`, C0 is topped up per dataset (`domain/origin_panel.py::origin_panel`), the bench is read
  per dataset (`BenchScore.scopes`), and `adaptive_queue`'s `strata` draws per dataset.
- **A cell runs the optimizer its own dataset names** (`tasks.py::InnerCell`); the outer pipeline is the union of
  their nodes (`InnerCells.pipeline`), and an edit reaches a cell only on its own optimizer's nodes (`InnerCells.own`).
- **`max_inner_rounds ≥ 2`**; **`lives.start` well below `max_inner_rounds`**, or the bank cannot drain before the
  calendar cap and the geometry loses its only brake.
- **Cells per round > `elimination_n_min`, and ≥ 6** — below that θ_se exceeds the point-lift. The count is the
  round DRAW (`sp_budget_round`): equal to the task list it is a census, below it a pool.
- **A cell is keyed by its own instrument alone** (`application/runner/inner/connector.py::_instrument`), so adding a dataset or task re-keys
  no banked cell.
- **CRN is the variance control, and the only one**; an identical cell replays.
- **`terminal_node` is the cell's own optimizer chain's last llm node** (`InnerCells.terminal`): only a stamp at the
  chain's end limits prefix-trust replay to a full match. It is not a health signal and nothing may tally it.
- **A HIT/MISS panel stays silent at L4** (`panels._no_labels`): the cell is verifier-graded, so no cell can be a hit.
  A prompt clause telling the model to ignore the panel is not the fix.
- **`L1Variant` is `extra="forbid"`**: the Pydantic model, both `answer_format`s and `resolved_schemas` move in ONE commit.
- **`budget.tokens` stays unbounded** — inner tokens land on the outer ledger as backend cost; `budget.usd` is the cap.

## Cost

Geometric: one outer round is `(1 origin + n_variants) × n_inner_tasks` fresh inner campaigns. **`budget.usd` is a
cap, not an estimate**, and a cap too small to finish a round buys nothing, because an unclosed round scores no
candidate. Re-measure before quoting a price; `python -m promptpotter evidence promptpotter-self` answers
variance and power on demand.

## Open

1. **A bounded, cheap default config** — the committed `dataset.yaml` must let `new promptpotter-self` complete at a
   cost an evaluator tolerates.
2. **`proxy_lift_corr ≥ 0.6` over ≥4 paired branches** — a measurement, gated on the panel resolving one arm from another.
3. **The OUTER election is unmeasured.** Round 0 holds one arm, so `p_best` cannot leave its tie; until a round-1
   election runs, every claim about outer *behaviour* is untested.
4. **The arms differ less than their own intervals.** More cells, or **candidates that differ more** — the cheaper,
   untried lever.
5. **Four optimizer-prompt edits are REFUTED — do not re-propose them:** slot-steering language, an anti-same-slot
   clause, a reweight of under-cited panels, and a wire `maxLength` on `prompt_fields_updates`
   (`OPTIMIZER_PROMPT_FIELD_MAX_CHARS` already sits above real growth and binds where L4 rewrites). The panels that
   grow with round count are where to look next.
6. **Cross-sample terms, unbuilt:** area-under-lift-vs-budget (from `TokenUsageRecord.round`); `mean lift − λ·std` with
   `std` the cross-seed outcome dispersion, never `theta_se`, through the post-aggregate formula, never the election
   rank key; PoBB-decisive promotion over inner arms (`application/scoring/selection.py::elimination_p_best`).
7. **Self-adopt mode (unbuilt, off by default)** — [SIFT](../research/landscape.md#sift-self-improvement-via-fast-tree-search-paper)
   / DGM's loop with our statistics in its gates; prior art in
   [`../research/external-constraints.md`](../research/external-constraints.md) § L4. On a **decisive** outer election
   the winner's overrides become the outer campaign's `optimization.nodes` overlay and the outer cycle forks onto it.
   The overlay already binds per task (`application/runner/entry.py::run_optimization`), and cell identity keys on the
   candidate's overrides, so banked cells survive. **The instrument is never adopted.** Their gates map onto ours:
   pass-count gate → PoBB hard-first; LLM-judge Bradley-Terry → Rasch θ/δ on outcomes; inherited parent score →
   excluded; visit-penalty sampling → UCB1 over backpropagated θ ([`roadmap.md`](roadmap.md) § Selector members);
   adopt-best → decisive election + held-out gate + anchor + rollback by fork. Take from them: a judge as a
   zero-sample prior on measurement ORDER (never a score), rank-based parent sampling, expansion overlapping
   measurement, a per-step cost split. **Preconditions:** a held-out panel of other task families scored only by the
   adoption gate; worst-case non-inferiority on every family; anchor to C0 with rollback; a leakage lint on the
   adopted set; every N generations an L4 with the adopted set as the OUTER optimizer against C0. It inherits #3 and #4.
8. **Environment admission inside the run** — a survey round admits seats on a served band verdict:
   [`survey-round.md`](survey-round.md).
