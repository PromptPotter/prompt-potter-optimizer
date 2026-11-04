# L4 — self-optimization

PromptPotter optimizing **its own optimizer prompts**. The outer cycle is a normal cycle; each outer
*sample* runs a whole inner campaign on a pinned benchmark seed. Connector
`application/runner/inner/connector.py`; dataset `promptpotter/assets/datasets/promptpotter-self/`;
CLI/headless only. The goal is a **distributable `promptpotter-self`** an operator runs with `new` at
bounded, visible cost.

**This file says what is TRUE. How to run and read one is the `potter-self` skill's**
(`.claude/skills/potter-self/`); every knob value is `dataset.yaml`'s.

## The measurand

`mean_bench_lift` — the MEAN over the inner rounds of the headline accuracy of the prompt the search had
**adopted** by that round on the cell's **held-out** rows, minus the origin's on the same rows
(`domain/l4/proxies.py::bench_level_series` over `application/runner/bench.py::line_bench_series`, a
rebase's discarded rounds included). The held-out rows are drawn beside the origin bank under the cell's
seed (`application/diagnostics/seed_screen.py::draw_bench`), marked `bench_only`, reached by no inner
node. `campaign.scoring.per_sample` re-anchors it `x + 0.5`, so the paired estimator's effect IS the mean
held-out lift.

`mean_round_delta` — the same trajectory on the SEARCH split, in logits on one ability ruler
(`application/bench/difficulty.py::DifficultyView.level_series`) — is an `InnerCellFacts` fact and scores
nothing; null with `series_reason` where the series differences nothing.

- **Held out, not in-sample**: the inner optimizer promotes on a point estimate over a small panel, so
  within-cell selection bias is inner behaviour and may not score.
- **Adopted, not proposed**: a round's value is what it crowns; averaging proposals reads negative for an
  exploring generator.
- **Mean, not endpoint**: the quieter instrument at identical spend; lifting early and holding scores
  above reaching the same place last.
- **The denominator is the line's round BUDGET**, holding the last adopted level forward across rounds a
  cell never ran; a round whose selection pass read nothing is a hole, and a cell with a hole is EXCLUDED
  (`no_evidence_reason`), never held forward.
- **θ is paired on one ruler** (`domain/ruler.py::RulerScope`); each cell reads the tenant ruler at the
  version its outer round opened on (`infrastructure/store/archive_queries.py::CellOf`), less its own
  parent line.
- **ONE term scores.** An outer cell's `pipeline_data` holds `mean_bench_lift` plus `inner`, one
  `InnerCellFacts` (`domain/l4/inner_facts.py`) the recursion alone writes — levels, peak,
  `mean_round_delta` and SE, round count, stop reason, `sent_usd` — REPORTING channels, never terms. A
  term that cannot move with the candidate gets no vote; no term divides by dollar cost (both caches are
  content-addressed, so a cost denominator measures how often the candidate ran before); no peak reading
  and no declared target.
- **One estimator per subtraction**: `DifficultyView.level_series` reads θ_C0 and every later level in
  one fit on one stamp.

## What a panel may claim

**An interval that excludes zero is the evidence; the ordering alone is not.**

- **Served at zero spend** by `application/evidence/` (`python -m promptpotter evidence promptpotter-self
  --ranking`, `GET /evidence?subject=campaign:…&ranking=`): each arm's paired effect with its interval,
  `EditSpread`, comparability (`ruler.id`), replicates, the cell/subject/residual decomposition and
  run-order confounding. `PanelPrecision` reports estimation noise beside between-cell spread
  (`mean_parent_level_se`) — two bars, never their ratio.
- **No within-cell noise term**: the inner instrument is content-addressed, so asking twice replays.
  Depth on a candidate is `verify`'s job.
- **A cell that failed is not a cell that scored zero** (`domain/scoring.py::Scorer.grade`): the election
  grades it 0.0 for the overlap guard; a published interval may not.
- **Absolute outer numbers never travel across runs**; within a run, seeds pair under CRN.
- **A HIT/MISS panel stays silent at L4** (`panels._no_labels`): the cell is verifier-graded, so no cell can be a hit. A prompt clause telling the model to ignore the panel is not the fix.

## Invariants — break one and the corpus is void, silently

- **The inner scorer, formula, seeds and held-out cells are unreachable by the outer loop**, and none can
  be a `pipeline_param` ([`../research/external-constraints.md`](../research/external-constraints.md) § Ranked, item 1).
- **`application/runner/inner/connector.py::_identity_config` enumerates the inner-origin fingerprint**; a
  dispatch renderer or an estimator moves it exactly as an inner prompt body does. It resolves once per
  init, so a mid-flight edit lands on the next `resume` and silently re-partitions the corpus. Run init
  counts the prior campaigns a novel instrument matches (`application/preflight.py::_check_instrument_replays`).
- **The solver's SPEND AXES are the instrument; the inner search writes prompt text alone**
  (`application/runner/inner/tasks.py::inner_instrument_config`): model and provider are the panel's
  ENVIRONMENT axis; `reasoning_effort`, `max_tokens` and `temperature` are rails.
- **The held-out rows are identical across arms**; a dataset must hold `n_samples_origin + n_samples_bench`
  rows or its cells are refused before any send. A held-out ENVIRONMENT (`panel` `held_out: true`) is
  seated as a `bench_only` outer sample (`application/runner/inner/connector.py::_extract_experiment`) and declares
  `dataset_split: {bench: 0}` (`partition_bank` refuses otherwise).
- **The instrument is decided per CELL** (`domain/cells.py::Seat`); two cells running one node under
  different overlays refuse (`tasks.py::InnerCells.owners`). A cell runs the optimizer its own dataset
  names (`tasks.py::InnerCell`); the outer pipeline is the union of their nodes. A cell is keyed by its
  own instrument alone (`application/runner/inner/connector.py::_instrument`).
- **θ never crosses a dataset; a lift pairs on shared cells.** A cross-dataset arm serves
  `ThetaCaveat.DISJOINT_SCOPES`; C0 is topped up per dataset (`domain/origin_panel.py::origin_panel`).
- **`max_inner_rounds ≥ 2`; `lives.start` well below it**, or the bank cannot drain before the calendar cap.
  **Cells per round > `elimination_n_min`, and ≥ 6** — below that θ_se exceeds the point-lift.
- **CRN is the variance control, and the only one**; an identical cell replays.
- **`terminal_node` is the cell's chain's last llm node** (`InnerCells.terminal`); it is not a health signal.
- **`budget.tokens` stays unbounded** — inner tokens land on the outer ledger; `budget.usd` is the cap.

## Cost

Geometric: one outer round is `(1 origin + n_variants) × n_inner_tasks` fresh inner campaigns. **`budget.usd`
is a cap, not an estimate**, and one too small to finish a round buys nothing. `python -m promptpotter
evidence promptpotter-self` answers variance and power on demand.

## Open

1. **A bounded, cheap default config** — `new promptpotter-self` must complete at a cost an evaluator tolerates.
2. **`proxy_lift_corr ≥ 0.6` over ≥4 paired branches** — gated on the panel resolving one arm from another.
3. **The OUTER election is unmeasured**: round 0 holds one arm, so every claim about outer *behaviour* is untested.
4. **The arms differ less than their own intervals.** More cells, or candidates that differ more — the cheaper lever.
5. **Four optimizer-prompt edits are REFUTED — do not re-propose them:** slot-steering language, an anti-same-slot
   clause, a reweight of under-cited panels, and a wire `maxLength` on `prompt_fields_updates`
   (`OPTIMIZER_PROMPT_FIELD_MAX_CHARS` already sits above real growth and binds where L4 rewrites). The panels that
   grow with round count are where to look next.
6. **Cross-sample terms, unbuilt:** area-under-lift-vs-budget; `mean lift − λ·std` through the post-aggregate
   formula, never the election rank key; PoBB-decisive promotion over inner arms
   (`application/scoring/selection.py::elimination_p_best`).
7. **Self-adopt mode (unbuilt, off by default)** — SIFT's ([`../research/landscape.md`](../research/landscape.md))
   loop with our statistics in its gates: on a **decisive** outer election the winner's overrides become
   the outer campaign's `optimization.nodes` overlay and the outer cycle forks onto it; the instrument is
   never adopted. Preconditions: a held-out panel of other task families scored only by the adoption gate,
   worst-case non-inferiority per family, anchor to C0 with rollback by fork, a leakage lint on the
   adopted set. It inherits #3 and #4.
8. **Environment admission inside the run** — [`survey-round.md`](survey-round.md).
