# Candidate Elimination

**Method:** stop a candidate when its posterior probability of being the round's best drops below ε.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/round-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/round-light.svg">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/round-light.svg" alt="One round: a parent is mutated into three candidates, each is measured sample by sample, the third is cut after 7 of 34 samples, and the winner becomes the next parent." width="880">
  </picture>
</p>

Each generation an LLM mutates the parent into a small population; every individual walks the same samples, and after each one a posterior asks *what is the probability this candidate ends up the round's best?* Below the threshold, measurement stops; the survivor breeds the next generation.

## Setting

*N* individuals a round (default 5), each measured on a shared query set **Q** of size *K* (potter's `adaptive_queue.sp_budget_round`), per-sample score in `[0, 1]`, mean composite. *N* × *K* backend calls dominate wall clock; the population is pre-enumerated, so the job is comparison, not search. The model (Rasch θ/δ, graded response, `√φ`) and the **round order** are [`verdict-resolution.md`](verdict-resolution.md)'s; the one property used here is that the order is *shared, never re-ranked per candidate*.

## Prior art — fishtest

**Stockfish's fishtest has run this loop since 2013** (patch vs master, SPRT). Two correspondences to know before claiming novelty: their unbalanced opening book is our seed-misses-first order, and Elo is Bradley–Terry, Rasch's logistic family. Not theirs: an automated proposer over structure and text, N-arm best-arm identification, and measurement reuse across a changed instrument.

## Why the comparison has to be paired

PoBB (Russo 2016) assumes i.i.d. observation; the shared order front-loads the seed's misses on purpose, so unpaired, a leader that ran the easy prefix reads as unbeatable. **PoBB priors are sample-keyed, and the leader is backfilled onto the candidate's upcoming samples before each comparison.**

- **Sample-keyed priors**: `PoBBCheck.priors_by_sample: cid → sample_key → PairedGrade` (θ response and verdict); `register_completed` keeps scored `GradedCell`s only. The parent enters on its OWN rows (`Frontier.parent_own_rows`), never the frontier; the searchpoint a prior is caught up under rides `race.py::CatchUpPool`.
- **Reactive backfill, per sample.** `CatchUpPool` starts one prior's call on one cell and returns the commit that writes it; the cell's catch-ups commit once it is taken, before the checks read coverage, so a backfill for an untaken cell is paid and never written. Each (prior, cell) pair is measured once, recorded as a `RaceCatchUpRecord`, and lands in `measurements/` for every future round.
- **A backfill row is not a panel row**: stamped with the PRIOR's identity and `MeasurementRole.BACKFILL` (`shared/measurement_context.py`) — reusable for pairing, never as panel evidence (resume's hole repair re-measures it).
- **Cost: roughly one extra candidate-equivalent of LLM spend per round** — the price of validity. It also removes lucky-prefix inflation mechanically.

## The θ rule

The first candidate runs to completion. Each later one, once read on `elimination_n_min` cells its θ is read on (`selection.py::cells_read` over `rasch.py::theta_cells`, never rows walked — [`verdict-resolution.md`](verdict-resolution.md) § The round order), after every query:

1. Map each prior onto the candidate's exact sample ids; one that cannot be caught up is **excluded, never zero-filled**.
2. Solve each arm's `θ` and Laplace `se` on the cycle's fixed δ ruler (`rasch.py::fit_theta`; 1PL, or 2PL where graduated).
3. `P(θ_cand > θ_prior) = Φ(Δθ / √(se_c² + se_p²))`, closed form; `p_best = min` over priors.
4. Bound it by what the DISCORDANT pairs support — `shared/statistics.py::sign_posterior` over `selection.py::discordance`. Concordant cells move θ but cannot say which arm is better. Support is DIRECTIONAL: contradicting pairs collapse the claim to 0.5 (a bare magnitude would license 0.9375 for an arm that lost 3 of 3); it can only spare an arm. **Discordance counts VERDICTS (`Grade.fitness`), never the objective** — a cost term would make a verbose arm lose every tie; cost reaches the comparison through θ alone (`selection.py::paired_grades`).
5. Stop when `p_best < ε(n)` (graded by depth: `epsilon_floor`).

The **same ability the round election ranks by**, so the two cannot disagree, valid on partial prefixes. Code: `application/scoring/selection.py::elimination_p_best` under `::paired_p_best` (the one pairing, for live `check()` and the resume replayer), driven by `application/optimizers/potter/pobb/checks.py::PoBBCheck`. Cross-cycle comparison is the `ab` verb (`resume_and_fork/ab_replay.py`) — re-derived decisions, no new measurements.

## Two regimes

- **Early — high-signal.** `P(cand > prior)` turns lopsided in 3–5 queries (Wilcoxon needed ≥8 at α=0.2, being variance-agnostic).
- **Late — low-signal.** Gaps ≤0.02; a near-tie (`P(best) ≈ 0.5`) rides to the cap and the θ election picks.

`p_best` is a minimised pairwise comparison — a bound on P(best), not the joint posterior — so a cut spares rather than overreaches.

## Tunable knobs

- potter's `pobb` `epsilon` (`0.15` in its manifest). A stop ends measurement; it is **not** a verdict and never removes the candidate from the election (`is_leader_eligible`).
- potter's `pobb` `epsilon_floor` — ε at `elimination_n_min`, ramping linearly to `epsilon` over the next `elimination_n_min` cells read (`PoBBCheck.epsilon_at`; `lock_in_n_min` counts the same). Equal to `epsilon` (as shipped) = a flat bar. **The bar ramps IN only, and nothing guards the tail** — else every late loser spends its budget; the price is that `PairedReading.reference_level` refuses an arm stopped short of a parent cell (`selected_by_stop`), so it ranks on θ alone. Aggression belongs here, never in `elimination_n_min`; a floor above `epsilon` goes flat and `epsilon_floor_inverted` reports it.
- `OptimizationConfig.elimination_n_min` (default `6`) — the one min-samples floor, gating PoBB **and** ruler warmth (δ≡0 ⇒ θ = logit-accuracy until this many grade-A samples bank): difficulty and ability become trustworthy at one threshold.

## The full elimination ladder

Five mechanisms, fixed order, each owning its memory field and display annotation:

| # | Mechanism | Fires | `n_min` | Candidate fate | Memory | Source |
|---|---|---|---|---|---|---|
| 1 | **Validation skip** — `Proposal.validation_failures` non-empty | pre-score | — | synthetic `{accuracy: 0.0, invalid: True}` (no backend calls) | `ArmState.validation_failures` → `l1_wounds` | `runner/measurement.py::_open_arm` |
| 2 | **Stale-data protocol** — a cached result classifies infra or fatal (`is_deprecated`); a repaired, answered row replays | every degraded query | — | annotated + possibly re-measured / swapped | — | `scoring/sample_measurement.py::execute_stale_data_protocol` |
| 3 | **`DegradationCheck` — fatal fast-path** — latest query's `classify_result()` returns a fatal code | every query | **1** | eliminated; `RuntimeFailure` | `runtime_failures` | `scoring/classification.py` |
| 4 | **`DegradationCheck` — rate-based** — `degraded_rate >= threshold` | every query | **3** | eliminated; `RuntimeFailure` | `runtime_failures` | `scoring/classification.py` |
| 5 | **`PoBBCheck`** — three exits, in order: answer-collapse, leader lock-in, paired `P(best) < ε(n)` | every query | `n_min` | eliminated; records `elimination_cut` decision | — | `optimizers/potter/pobb/checks.py` |

**Per query:** cache lookup → stale-data protocol if degraded → `SampleScoredRecord` (`telemetry.py::emit_sample_scored`) → the cell's catch-ups commit → every check in `degradation_checks`, first signal ends the candidate (fatal beats rate beats PoBB).

**Each rule answers how EARLY it could fire** (`StopRule.earliest_stop`) — early, never late — so a look-ahead walk launches a cell only if no rule can fire before it, and a cut discards at most one call. The fatal fast-path reads one row's content and stays out (as do `scoring/query_loop.py`'s fault aborts). A pause, skip or budget stop keeps what is paid for and starts nothing; an in-flight call is cancelled only where that stops its bill (`Connector.cancel_stops_billing`); no cell starts that the spend book cannot hold at its bound (`infrastructure/llm/spend_book.py`).

**Candidates walk at once and decide in turn** (`runner/measurement.py::measure_population`, one loop `scoring/query_loop.py::run_walks`): a later candidate may measure ahead but is checked and cut only once every earlier one is decided, so its priors are a serial round's. That horizon, summed over walks and catch-ups, is the concurrency served (`run_phase_control.py::FlightGauge`).

**One comparator, one stop rule — do not add a sixth.** A paired-margin futility gate was removed: discordant binary wins are a second comparator beside the θ ruler, re-encode the election's bar, and go inert on a graded backend. Futility, if bought back, is one gate **on the θ ruler**.

## CAPO's race — the `blocks` sampler and the `paired_t` eliminator

CAPO's survival selection (arXiv 2504.16005 §4, App. B) takes PoBB's row 5 for its own manifest. A **block race**: the race answers `blocks` where PoBB answers `rule`; `run_walks(blocks=)` turns every live arm once per block and the race's `close` decides them together. `DegradationCheck` still runs per arm.

- **`blocks` (sampler)** — `block_size` (b), `max_blocks` (z_max): the first `max_blocks` whole blocks of the search split in bank order, the same every round, never shuffled (App. C.4); `Panel.block_size` hands the boundaries over; a pool short of one block refuses.
- **`paired_t` (eliminator)** — `alpha`, `length_penalty` (γ), and μ = the `population` selector's `size` (one knob). At each block's close every live arm meets every other in a one-sided paired t (`shared/statistics.py::paired_mean_t`) on CAPO's objective over the same k blocks; arms that μ others significantly beat are cut together off pre-cut readings — App. B's `n_sig_better ≥ μ` (§4's prose says "more than") — with no multiple-test correction, as CAPO races. At μ or fewer the race is settled (`locked_in`). `Elimination.context.gate` reads `outscored` / `settled`, with its `block` and `raced_against`; the stream's `p_best` is the smallest `p_better`.
- **Departures.** A race starting at ≤μ arms still walks one block so the selector can rank (App. B races none). `paired_mean_t` floors the SE at `1/(4n)`. A budget-cut round is a holed panel under `optimization.panel_gate`: `strict` unwinds it (a raised-ceiling `resume` re-runs it); `off` elects on the blocks paid for (`Selector.elects_partial`) among arms at the coverage floor (`selection.py::cells_read`), the parent read on cells the archive holds, as the run's last round; no arm at the floor, or a parent with none of its cells, unwinds.
- **Each cut is a ledger decision**, `paired_t_cut`, REPLAYED (arm, rows, rivals, γ, normaliser), so a changed scorer re-reads the same test.

**CAPO selects on its own objective; the bench scores it on the campaign's.** Per cell `fitness − γ · target_prompt_chars / length_norm` (§4), unclamped; γ = `capo_init`'s `length_penalty` (0.05, App. C.4). `capo_init` fixes γ and `length_norm` (the longest initial prompt) into the `capo_objective` slice that `paired_t` and `population` both read. The campaign's `scoring.per_cell` composite never takes CAPO's term — it is the ONE evaluator every optimizer's arms, election and bench headline are scored under.

## CAPO's population and operators

The manifest (`assets/optimizers/capo/pipeline.yaml`, every value cited there): `mating` draws two random parents per offspring (c per round), `capo_crossover` merges prompts, `shot_crossover` samples shots, `capo_mutate` rephrases, `few_shot` mutates shots, `population` keeps the race's survivors best-first by mean objective on cells all measured, cut to μ — a `population_kept` decision, REPLAYED. The population rides `optimizer_state` (resume and fork re-seat it); the round advances when its best is not the incumbent. Departures from the paper:

- **The prompt** is the individual's whole text: an operator reads its fields as rendered (`OptSearchPoint.render`) and its reply replaces them, riding `instruction` with every other field emptied (`rewritten`).
- **The shots** render the demo row's ground truth (paper: a reasoning chain, label as fallback, §4).
- **The task description** is the campaign's framing and the origin's `answer_format` (paper: hand-written, App. D.1).
- **The initial instructions** come from `capo_init`, the `initial_population` pipeline walked before round 1's `default` (`before_default`), on the campaign's optimizer model (paper: once, Claude Sonnet 3.7, App. D.2); `size` drawn, each with 0..`k_max` random shots (Alg. 1), raced beside round 1's offspring.
- **One model for optimizer and target** (§5) is an overlay; `max_tokens` is the model's floor (paper 2048, App. C.1); temperature 1.0 is vLLM's default (the paper states none).
- **The length** counts characters of the scored prompt, framing included (paper: tokens, §4).
- **The 5M-input-token budget** (§5) is `budget.tokens`, which counts output too.
- **A reply without `<prompt>` markers** is an invalid arm costing no cell.
- **The incumbent is re-scored** each round on `Selector.parent_cells`; from round 2 it races as a member, and round 1's (the origin) is read on the first block alone, so a round-1 lift pairs on one block.
- **A repeated request samples afresh** (the paper's T = 1.0): each call is seeded by round, node and call off the run's seed (the determinism clamp's, else the campaign id), so only a resume or fork replays a reply; a clamped seed pins every call.

## LEVI mapping and deviations

LEVI (arXiv 2605.09764) races nothing: its manifest (`assets/optimizers/levi/pipeline.yaml`, every value cited there) has no eliminator, and its selector is an archive. Round 1 is Phase 1 (Alg. 1), the **calibration round**; every later round is one paradigm-shift period (Alg. 2).

| Paper | Node · config |
|---|---|
| Seed pass `M_l.DIVERSESEED`, 4 seeds (Alg. 1, Table 5) | `levi_seed`, the `initial_population` pipeline the bench walks as round 1, `n_diverse_seeds: 4` |
| Calibration matrix on the discovery set, N_init = 5 (§3.3, App. A) | round 1 scores the origin (its parent) and the seeds on `proxy_css.discovery`, the pool's first 150 in bank order (App. I) |
| Proxy by greedy column subset, K_proxy = 30, (r, s, c) = (0.5, 0.5, 0.15) | `proxy_css`: `size`, `rank_weight`, `separation_weight`, `redundancy_weight`; `shared/statistics.py::greedy_column_subset`; a `proxy_selected` decision, REPLAYED |
| f restricted to the proxy (Alg. 1 line 11) | `proxy_css` draws the proxy, in one order, every round after calibration |
| Welford z-score + sigmoid, CVT with 50 centroids, TRYINSERT (§3, Alg. 1-2) | `map_elites`: `centroids: 50`; the statistics ride `LeviCalibration.stats` |
| Behavioural descriptors, input- and output-side (§3.1) | `map_elites.descriptors`, read by `optimizers/descriptors.py` |
| Refinement route: small model, ~90% of calls (§3.2) | `levi_refine`, `interval - 1` calls a round, `max_tokens` 16,384; the bench's one model, not the paper's Qwen3-8B (§4.2) |
| Parent ∝ exp(f / T), T ∈ {0.3, 0.7, 1.0, 1.2}; one inspiration, dropped 20% (App. B) | `levi_refine`: `sampler_temperatures`, `n_inspirations`, `inspiration_drop` |
| Paradigm shift every 10 evaluations, k = 3 clusters, `max_tokens` 4,096 (App. F) | `levi_paradigm_shift`: `interval`, `n_clusters`; the bench's one model, not the paper's Gemini 3 Flash (App. A) |
| Templates E.6 and E.7 | `resolved_prompts`, verbatim |
| Budget B; the best elite returned | the campaign's budget; the round selects the best elite unless it is the incumbent |

Departures from the paper:

- **Rounds, not workers.** A period's `interval - 1` refinements and its shift are measured together and inserted in walk order, so no refinement sees another's insert (LEVI's four workers do, App. B).
- **The seed pass sends E.7** (E.1 is for code) over the seeds so far, **starting from the origin** (Alg. 1 starts from none) — App. A's five calibration prompts of Table 5's four seeds. A reply without `<prompt>` markers is an invalid arm.
- **No variants and no meta-advice.** The variant bursts (Table 5, App. F) have only a code template (E.5) and 80 variants exceed the paper's own budget (Table 2); the meta-advice template is unpublished.
- **Unstated values, chosen:** descriptors = prompt length and each proxy cell's objective; `feedback_failures` 3 (GEPA's reflection minibatch); centroids by k-means over `cvt_samples` uniform draws in the calibration bounds (Figure 2); temperature 1.0; a one-sided tie worth ½ in rank faithfulness; separation normalised by the widest column; inspiration and failures drawn uniformly.
- **Alg. 1 as printed:** calibration prompts enter the running statistics at line 13 and again at insertion, line 17.
- **A calibration spanning no descriptor volume halts the run** — every centroid would land on one point; the paper's CVT has no answer either.
- **The artifact** is the whole prompt, and `{problem_description}` CAPO's task description (§ CAPO's population and operators); shown failures ride `fence_untrusted`.
- **f** is the mean per-cell objective on the proxy; the paper re-evaluates on the full set for late selection (§4.2), the bench scores on the bench set.
- **A repeated prompt samples afresh**, seeded as § CAPO's population and operators states.

## GEPA mapping and deviations

GEPA (arXiv 2507.19457) races children against their parents, not each other: its manifest (`assets/optimizers/gepa/pipeline.yaml`, every value cited there) runs one Alg. 1 iteration per round, its eliminator Alg. 1's acceptance test.

| Paper | Node · config |
|---|---|
| Split D_train into D_feedback and D_pareto (Alg. 1 line 1) | `minibatch.pareto_size` 50, a row count as the paper states each benchmark's (App. E.1); the Pareto set rides the `pareto_set` slice (`gepa/state.py::ParetoSet`) |
| P ← [Φ], Φ scored on D_pareto (lines 2-5) | round 1 mutates the incumbent, the origin; `pareto` seats it with its Pareto-set scores off the bench's re-score, which reads the Pareto set alone (`Selector.parent_cells`) |
| SELECTCANDIDATE (line 7, Alg. 2) | `pareto`, at each round's close: per-cell fronts, the dominated removed, the next parent drawn ∝ cells led — `ParetoScores.parent_id` |
| SELECTMODULE, round-robin (line 8, §3) | an individual renders one prompt, so the module is its whole prompt every round |
| A minibatch of b from D_feedback (line 9), b = 3 (App. E.4) | `minibatch.size` 3, the panel's first block |
| Feedback, scores and traces on M through μ_f (line 10) | `gepa_reflect` runs the parent on the minibatch through the scoring gateway, filed as a parent's reading; μ_f's text is `row_diagnostics.py::cell_feedback` |
| UPDATEPROMPT (line 11, App. C) | `gepa_reflect/1`, verbatim; the reply's fenced block is the child's instruction |
| σ′ improved on σ (lines 13-14) | `minibatch_gate`: a child not strictly above its parent's mean objective on the minibatch is cut — a `minibatch_gate` decision, REPLAYED off the parent's recorded scores |
| Φ′ added to P, scored on D_pareto (lines 15-18) | an accepted child walks the rest of the panel, the Pareto set; `pareto` admits it with its per-cell scores |
| Return the best average on D_pareto (line 21) | the round selects the best aggregate unless it is the incumbent; a tie holds |
| The reflection on the system's own model: Qwen3 8B at 0.6, top-p 0.95, a 16,384-token window (App. E.2) | `gepa_reflect`, `temperature` 0.6, `top_p` 0.95, `max_tokens` 16,384, on the bench's one model — Qwen3 8B is retired on OpenRouter |
| Budget B in rollouts (Eq. 2), matched to MIPROv2's per benchmark (App. E.4) | the campaign's round, spend and token ceilings |

Departures from the paper:

- **No merge.** On a one-module individual Alg. 4's child IS the other descendant: the bench runs the paper's GEPA row, not GEPA+Merge.
- **The prompt** is the whole text (§ CAPO's population and operators); round 1 reads the origin's fields as one instruction.
- **Unstated values, chosen:** the Pareto set is the pool's first `pareto_size` rows in bank order (50; the paper sizes it per benchmark); the minibatch is drawn uniformly each round by the run's seed (the reference implementation walks a once-per-epoch shuffle); dominance as the reference reads it (dominated when every cell it leads another survivor also leads, lowest aggregate removed first); examples as its `# Example` / `## Inputs` / `## Generated Outputs` / `## Feedback` markdown inside the untrusted-content fence.
- **The acceptance test** compares means over minibatch cells both graded (the reference: sums); a parent perfect on the minibatch still reflects, as Alg. 1 has it (the reference skips).
- **μ_f** is what the scorer can say about a cell — objective, correctness where the composite differs, expected answer, each judge's banked reason (paper: per benchmark, App. E.1).
- **Top-k 20** (App. E.2) is not carried (no llm-node knob); a reply without a fenced block is taken whole, as the reference does.
- **A repeated reflection samples afresh** at 0.6, seeded as § CAPO's population and operators states.
- **One model for reflection and target** is the campaign's overlay, as CAPO's.
- **A scored member carries a verdict on every Pareto-set cell**; one missing any is refused, a `candidate_refused` decision (ARCHIVAL); a round seating no one reflects on the incumbent again.
- **The draw moves to the close**: the next parent is drawn and banked at round close, so resume and fork re-seat candidates and draw together. Pareto-set scores bank at admission; a scorer change re-grades none, as LEVI's elites.
- **`budget.rounds` counts rounds and a GEPA round is one proposal**, so the cap is the child count wanted; the runaway guard (`runner/loop.py::HARD_CAP_ARMS`) counts arms raced.
- **The incumbent is re-scored** on the whole panel each round, minibatch included; under `lift_reference: parents` each child is read against its GEPA parent on the child's cells.

## On-disk shape and replay

Each `ELIMINATION_CUT` / `LEADER_LOCK_IN` record (in `rounds/round_NNNN.json`) carries under `data`: `p_best`, `leader`, `candidate_cells` (the GRADED cells the posterior was fit on, by `Sample.key`, in walk order) and `prior_histories[cid]` (each prior's `response` and `verdict` there, after backfill); a collapse cut carries none. `inputs_ref` records the gate parameters **and which `EliminationGate` (`pobb/checks.py`) fired** — only ε computed a posterior.

The replayer (`optimizers/potter/resume.py::_pobb_replay_snapshot`) feeds the rescored rows at `candidate_cells` and the recorded `prior_histories` to the same `paired_p_best` on the fixed δ ruler. **The record is the entire input — no cross-round lookup, no backfill** — so replay is bit-for-bit absent a scorer change; under one, the candidate side is rescored and priors stay recorded.

## Open questions

1. **Tie-breaking at budget cap.** Top 2–3 at similar `P(best)`: ship pick-by-point-estimate; design a cap-extension policy after seeing how often it fires.
2. **Small-*n* θ.** Few observations do NOT keep the gate conservative — ties narrow both `se` (`p_best` 0.124 at a low base rate, `sealqa/longseal-12`). Step 4 bounds that; the EB hyperprior on ability variance keeps the fit from collapsing.

## Why this family and not another

Mid-round abortion is **best-arm identification**; three choices place PoBB:

- **Every prior, not only the leader** — LUCB (Kalyanakrishnan 2012), Bayes-UCB (Kaufmann 2012) and Hoeffding Races (Maron & Moore 1993) are pairwise too but test against the leader alone, with frequentist bounds.
- **Bayesian over frequentist** — `P(c is best)` is one operator-readable number.
- **Fixed-confidence (ε) over fixed-budget** — broken candidates stop at the evidence floor; Successive Rejects (Audibert 2010) and Sequential Halving (Karnin 2013) run them to the phase boundary. "Fixed-confidence" is loose: ε is a bar on a posterior reading, not an anytime-valid error rate, so under optional stopping it carries no frequentist guarantee ([`../research/external-constraints.md`](../research/external-constraints.md) § Ranked, item 3). The anytime-valid form — a confidence sequence from test supermartingales ([Hsu & Shekhar, *Efficient Sequential Evaluation of LLMs*](https://arxiv.org/abs/2607.17409)) — is what the guarantee must take before it is claimed.

**It replaced Wilcoxon signed-rank + Holm-Bonferroni** (pairwise, variance-agnostic); Holm survives as a *reporting* correction only (`shared/statistics.py::holm_adjusted`). **OCBA** (Chen 2000) is the closest classical relative but allocates where this stops; PoBB likewise keeps Top-Two Thompson Sampling's stop rule and drops its allocation half.

## Comparison to MCTS

PromptPotter **is** AlphaZero-shaped MCTS over the lineage tree:

- **Selection.** L2/L3 emits `fork_proposal: {reason}` — *whether* to rewind; *where* is UCB1 over the backpropagated tree (`application/mask/backprop.py::select_rewind_round`). The layer does not name the round — it never sees the ancestors' fitness.
- **Expansion.** A round: L1 proposes a population from the selected node.
- **Simulation.** A deterministic forward pass, not a random rollout (AlphaZero's precedent); within a round PoBB prunes siblings on shared samples.
- **Backpropagation.** Each round's θ rolls up to every ancestor as visit count + value (`accumulate_node_stats`), every cycle read on ONE fit (`DifficultyView.level_series`); a caveated round is a visit with no value, and a rewind finding no valued ancestor is dropped as a `layer_rebase_dropped` warning.

**Value is θ, never accuracy, and a fork's inherited prefix is not a fresh visit** (`application/mask/backprop.py`) — both naive forms fail silently. A rollout is a full round of LLM calls, so exploration is closer to PUCT than vanilla UCT; it buys **recovery from dead-end branches**.

## References

- **Russo, D. (2016).** *Simple Bayesian Algorithms for Best Arm Identification.* COLT. — the PoBB / Top-Two Thompson Sampling family.
- **Maurer & Pontil (2009).** *Empirical Bernstein bounds and sample-variance penalization.* COLT.
- **Kalyanakrishnan et al. (2012).** *PAC subset selection in stochastic multi-armed bandits.* ICML. — LUCB; rejected for testing against the leader alone.
- **Audibert, Bubeck, Munos (2010).** *Best arm identification in multi-armed bandits.* COLT. — Successive Rejects; rejected for not adapting within-round.
- **Chen, C.-H. (2000).** *Optimal Computing Budget Allocation.* Operations Research. — the closest classical relative; allocation rather than a stop rule.

The `classify_result()` rule table: [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md#classify_result-fatal-classification). Operator framing: [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md#deprecated-samples).
