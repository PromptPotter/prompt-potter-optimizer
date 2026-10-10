# Candidate Elimination

**Method:** stop a candidate when its posterior probability of being the round's best drops below ε.

## Setting

Each round evolves *N* individuals (default *N* = 5) via an LLM optimizer prompt. Each is measured on a shared query set **Q** of size *K* (potter's `adaptive_queue.sp_budget_round`, 20 in its manifest), producing a per-sample score in `[0, 1]` aggregated as a mean composite. The scoring budget per round is *N* × *K* backend calls, dominating wall-clock. Population is pre-enumerated — there's no parameter space to search, only a fixed set to compare.

The statistical model underneath — Rasch θ/δ, the graded response, the `√φ` SE correction — is owned by [`verdict-resolution.md`](verdict-resolution.md), and so is the **round order** this page depends on one property of: it is *shared, never re-ranked per candidate*, so shared prefixes keep the paired stats comparable. A per-candidate re-rank front-loads the seed's own hit set and blinds every gate here until the tail.

## Prior art — fishtest

**Stockfish's fishtest has run this loop since 2013**: a proposed patch, thousands of noisy games against the current master, SPRT deciding accept/reject, winner becomes the master. Know two correspondences before claiming novelty — their opening-book curation (unbalanced positions, because a draw carries almost no information about relative strength) is our seed-misses-first ordering, and Elo is Bradley–Terry, so the same logistic family as [`verdict-resolution.md`](verdict-resolution.md)'s Rasch model. What is *not* theirs: an automated proposer over structure and text, N-arm best-arm identification rather than a two-arm test, and reuse of measurement across a changed instrument.

## Why the comparison has to be paired

PoBB (Russo 2016) assumes every arm is observed on an i.i.d. sample of one distribution. The shared round order deliberately violates that: it front-loads the decision-relevant samples (the seed's misses — the only place a candidate can win) so a dead candidate is abandoned within a handful of queries. Unpaired, two arms measured on near-disjoint sets get compared as though they were iid — a leader that ran only the easy prefix reads as unbeatable, and every later candidate, measured hard-first, is eliminated against a rate the leader never had to earn.

The fix is one design choice with several call-site consequences: **PoBB priors are sample-keyed, and the leader is backfilled onto the candidate's upcoming samples before each comparison.**

- **Priors are stored sample-keyed**, not as flat vectors: `PoBBCheck.priors_by_sample: cid → sample_id → graded fitness`. `register_completed` ingests full `QueryMeasurement`s and keeps the graded ones alone. The stop rule holds grades and nothing else; the searchpoint a prior is caught up under rides the round's `race.py::CatchUpPool`.
- **Backfill is reactive, per sample.** `CatchUpPool` takes the bench's catch-up function, which starts one prior's call on one cell and returns the commit that writes it; the scoring phase commits a cell's catch-ups once the cell is taken, before the checks read prior coverage. Under look-ahead the call may START as the cell launches, in a slot of its own, but its row reaches `measurements/` only at that commit — a backfill for a cell no candidate takes is paid and never written. A (prior, cell) pair is measured once however many candidates reach it — priors already covering the id are skipped, and the telemetry event suppresses itself when no prior gained the cell. Priors are caught up sample-by-sample as the candidate measures them, so paired comparison always sees current priors without a full-dataset upfront wall, and a candidate eliminated early never pays for coverage it won't reach. New pairs land in `measurements/` and are reusable by every future round.
- **A backfill row is not a panel row.** It is stamped with the PRIOR's identity and `MeasurementRole.BACKFILL` (`shared/instrument.py`) — the closure receives the prior's id precisely so it cannot inherit the foreground candidate's. Reuse for a *paired comparison* is the point; reuse as some candidate's own **panel** evidence is not, because a backfill is measured outside the round's shared order — resume's hole repair refuses it and re-measures.
- **What it costs.** Backfill runs per candidate over its sample order (~6–10 hard samples). The
  round's first candidate pays a few fresh leader measurements; later candidates' orders overlap
  heavily and hit the cache, and by round 3+ the leader has near-full coverage. Net: roughly **one
  extra candidate-equivalent of LLM spend per round** — the price of statistical validity.
- **Lucky-prefix inflation is self-correcting.** Backfill forces a locked leader onto the candidate's hard-first order, so its recorded mean deflates toward its true rate before the comparison. No threshold to lower, no display patch: the inflation was a mechanical consequence of unpaired comparison and paired comparison mechanically removes it.

## The θ rule

Individuals are evaluated sequentially on **Q** in the shared round order. The first candidate runs to completion, establishing a reference. Each subsequent candidate is measured query by query; once `elimination_n_min` is reached, after every query:

1. Build the paired comparison set — each prior mapped onto the candidate's exact sample ids. Priors that cannot be caught up are **excluded, never zero-filled**.
2. Each arm's ability `θ` and its Laplace `se` are solved separately on the cycle's fixed δ ruler (`fit_theta_given_delta`) — fixed δ decouples the arms, and the ruler is 1PL or, where the dataset graduated, 2PL.
3. `P(θ_cand > θ_prior) = Φ(Δθ / √(se_c² + se_p²))` — closed-form, no Monte Carlo. `p_best = min` over priors (bounded above by the hardest prior).
4. Bound it by what the DISCORDANT pairs support — `shared/statistics.py::sign_posterior` over `discordant_counts`, the width `exact_paired_reading` already refuses a verdict below. Concordant cells move θ but cannot say which arm is better, so unbounded a prefix holding one discordant cell reads as decisive. Support is DIRECTIONAL — the posterior's mass on the side θ read — so pairs that CONTRADICT θ collapse the claim to 0.5 rather than widen it; a bare magnitude would do the opposite, licensing 0.9375 for an arm that lost 3 of 3. Sign-preserving and toward 0.5, so it can only ever spare an arm, never cut one.
5. Stop when `p_best < ε(n)` — ε is graded by depth, not scalar (see `epsilon_floor`).

This is the **same difficulty-adjusted ability the round-winner election ranks by**, so mid-round elimination and end-round election cannot disagree about what "better" means — and because it is difficulty-adjusted it stays valid across partial prefixes, where a raw hit-rate would crown whoever banked the easy samples. The pairing still earns its keep: backfill guarantees priors have outcomes on the candidate's *contested* samples, which is exactly where the θ comparison gets its information.

Code: `application/scoring/selection.py::elimination_p_best` (the one θ rule) under `paired_p_best` beside it (the one PAIRING: graded rows alone, against the priors covering them — what live `check()` and the resume replayer both call), driven by `application/optimizers/potter/pobb/checks.py::PoBBCheck`. Cross-cycle comparison is the deterministic A/B replay engine (`resume_and_fork/ab_replay.py`, the `ab` verb) — it re-derives recorded decisions under the current engine, no new measurements.

## Two regimes

**Both manifest** over a campaign.

- **Early — high-signal.** LLM-generated prompts differ a lot; some clearly dominate. The θ posteriors separate fast and `P(cand > prior)` becomes lopsided within 3–5 queries. Wilcoxon needed ≥8 queries at α=0.2 because it is variance-agnostic.
- **Late — low-signal.** L2/L3 escalation has narrowed the population and true gaps are ≤0.02. The Bayesian *best*-test cannot confidently abort a near-tie (`P(best) ≈ 0.5`), so a tie rides to the sample cap and the winner is picked by the θ election.

PoBB does not sample a joint posterior. `p_best` is a pairwise θ comparison against every prior, minimised — a bound on P(best), not the joint posterior — and since P(best) can only sit below it, a cut on it spares rather than overreaches.

## Tunable knobs

- potter's `pobb` node `epsilon` (`0.15` in its manifest) — smaller = more conservative. The one ε: "stop measuring a candidate whose probability of being the round's best is below ε". A stop ends measurement; it is **not** a verdict, and never removes the candidate from the election (`is_leader_eligible`).
- potter's `pobb` node `epsilon_floor` — the ε applied at `elimination_n_min`, ramping linearly up to `epsilon` over the next `elimination_n_min` cells and holding it to the panel's last cell (`PoBBCheck.epsilon_at`). Equal to `epsilon` — as the manifest ships it — leaves the bar flat and elimination bit-identical, so grading exists only where ε was deliberately raised above it: a raised ε then bites as cells accumulate rather than on the thinnest reading. **The bar ramps IN only, and nothing guards the tail.** An arm clearly behind is cut however few cells remain, because a bar that sank again near the end — or a guard that stopped cutting there — would confine PoBB to an early band and let every late loser spend its whole budget. The price is the matched-parent reading: `metrics.py::matched_parent_stats` refuses an arm that stopped short of any cell the parent measured, so a late cut, like an early one, is ranked on θ alone. Aggression belongs here and never in `elimination_n_min`, which also gates ruler warmth. A floor set above `epsilon` would grade the bar downward; the ramp goes flat at `epsilon` instead and the `epsilon_floor_inverted` coupling reports it.
- `OptimizationConfig.elimination_n_min` (default `6`) — the single min-samples floor. It gates PoBB (below it a candidate has too few cells to act on — cells, which are not the width a cut needs; step 4 above is) **and** the difficulty-ruler warmth: the per-cycle δ ruler stays flat (δ≡0 ⇒ θ = logit-accuracy) until at least this many grade-A samples are banked. Difficulty and ability become trustworthy at the same evidence threshold — one knob, no separate ruler-only constant.

## The full elimination ladder

Five independent mechanisms can end a candidate's evaluation early or annotate a query. Fixed order; each owns its own memory field and display annotation.

| # | Mechanism | Fires | `n_min` | Candidate fate | Memory | Source |
|---|---|---|---|---|---|---|
| 1 | **Validation skip** — `CandidateProposal.validation_failures` non-empty | pre-score | — | synthetic `{accuracy: 0.0, invalid: True}` (no backend calls) | `wounds.validation_failures` | `runner/measurement.py::_open_candidate` |
| 2 | **Stale-data protocol** — a cached result classifies infra or fatal (`is_deprecated`); a repaired, answered row replays | every degraded query | — | annotated + possibly re-measured / swapped | — | `scoring/sample_measurement.py::execute_stale_data_protocol` |
| 3 | **`DegradationCheck` — fatal fast-path** — latest query's `classify_result()` returns a fatal code | every query | **1** | eliminated; `RuntimeFailure` | `runtime_failures` | `scoring/classification.py` |
| 4 | **`DegradationCheck` — rate-based** — `degraded_rate >= threshold` | every query | **3** | eliminated; `RuntimeFailure` | `runtime_failures` | `scoring/classification.py` |
| 5 | **`PoBBCheck`** — three exits, in order: answer-collapse, leader lock-in, paired `P(best) < ε(n)` | every query | `n_min` | eliminated; records `elimination_cut` decision | — | `optimizers/potter/pobb/checks.py` |

**Ordering inside a walk.** For each query: (1) prior-result cache lookup; (2) if degraded → `execute_stale_data_protocol`; (3) `on_sample_scored` fires → display renders the line; (4) the cell's PoBB catch-ups are committed; (5) iterate every enabled check in `degradation_checks`; first to return a signal ends the candidate. Mechanisms 3–5 co-exist in that final list — fatal beats rate beats Bayesian PoBB.

**Each rule also answers how EARLY it could fire** (`StopRule.earliest_stop`), over every way the cells still out can resolve, and that answer is what lets a look-ahead walk launch past the next cell while a cut still discards at most one call: a cell is launched only if no rule can fire before it. The answer may come early, never late. PoBB builds it from the reading's two inputs instead of trying completions — the θ gap's extremes are exact, since the MAP rises with every grade; its noise has a closed-form floor; the sign bound is extreme at its corners — and the rate check counts every unknown cell as degraded. The fatal fast-path fires on one row's content, so no rule can foresee it; it stays out of the answer, like the fault aborts in `scoring/query_loop.py`, and those stops discard whatever was out. An operator's pause, skip or budget stop first keeps what is already paid for — the results already back and the catch-ups already started — and starts nothing while it does. A call already sent is cancelled only where that stops what it bills (`Connector.cancel_stops_billing`): otherwise a pause or a spent ceiling waits for it, and what the stopped walks were sure to take is banked, so the resumed round replays it; a skip is replayed there too. No cell starts that the run's spend book cannot hold at its bound beside every cell out (`infrastructure/llm/spend_book.py`).

**A round's candidates walk at once and decide in turn** (`runner/measurement.py::measure_population`) — under PoBB; a block race decides them together, below. One loop drives the whole phase (`scoring/query_loop.py::run_walks`): it launches every candidate's cells, and only the candidate whose turn it is takes a cell, answers a skip and is decided. Slots go to that candidate's catch-ups, then its cells, then the candidates ahead of it, which leave one slot free. A later candidate may measure ahead of the ones before it, but is taken, checked and cut only once every candidate before it is decided — so its priors are exactly a serial round's. While it measures ahead, each undecided candidate before it may or may not become a prior, so its horizon reads each as a prior it cannot count on, graded where that candidate's returned cells say and unknown everywhere else. The same horizon, taken over the whole remaining panel and summed over every walk plus the catch-ups, is what the round serves as the concurrency its rules allow (`scoring/query_loop.py::FlightGauge`) — the depth an `auto` arming actually runs at, beneath the backend's ceiling.

**One comparator, one stop rule — do not add a sixth.** A paired-margin futility gate ran here and was removed. Anything that counts discordant binary wins is a second comparator beside the θ ruler the election actually ranks on, so the two disagree by construction; it re-encodes the election's bar a second time; and it goes inert on a graded backend, where a per-sample fitness of 0.63 is neither a win nor a loss. Buying futility back means one gate **on the θ ruler**.

## CAPO's race — the `blocks` sampler and the `paired_t` eliminator

CAPO's survival selection (arXiv 2504.16005 §4, App. B) is a second eliminator, for its own
manifest; it takes PoBB's row 5 in the ladder above and leaves potter's untouched. It is a **block
race**: its race answers `blocks` where PoBB answers `rule`, and `run_walks(blocks=)` then turns
every live arm once per block and hands the block's rows to the race's `close`, which decides the
arms together. `DegradationCheck` still runs on each arm's own rows beside it, and its standings
reach the racing stream under `paired_t`.

- **`blocks` (sampler)** — `block_size` (b), `max_blocks` (z_max). The panel is the first
  `max_blocks` whole blocks of the search pool in the bank's order: the same cells every round,
  never shuffled (App. C.4). `Panel.block_size` hands the boundaries to the eliminator; a pool
  short of one block refuses the round.
- **`paired_t` (eliminator)** — `alpha`, `length_penalty` (γ), and μ, which is the `population`
  selector's `size`: one knob, so the race cannot keep a different count than the population
  carries. Every live arm
  walks block k in walk order; at its close each is tested against every other live arm: a
  one-sided paired t (`shared/statistics.py::paired_reading`) on CAPO's objective (below), over the
  cells both measured — the same k blocks, since every arm walks one order. The arms that μ others
  significantly beat are cut together, off the readings taken before any cut — App. B's
  `n_sig_better ≥ μ`, where §4's prose says "more than" — with no multiple-test correction, as
  CAPO races. Once μ or fewer arms are left the race is settled and stops them where they stand,
  `locked_in`; an arm that took the whole panel completes instead. A cut stamps
  `elimination_context.gate` `outscored`, a settled arm `settled`, each with the `block` of
  `blocks` that decided it and the arms it `raced_against` there; the stream's `p_best` is the
  smallest `p_better`, the t fiducial P(arm beats that rival).
- **Where it departs from the paper.** A race that starts with μ arms or fewer still walks its
  first block, so the selector has rows to rank; App. B races none. `paired_reading` floors the SE
  at `1/(4n)`, which moves a p only where the paired differences are nearly constant. And a round
  the spend or token budget cuts is elected on the k of z_max blocks it paid for
  (`Selector.elects_partial`): the population is kept among the arms that reached the coverage
  floor, its parent is read on the cells the archive already holds — round 1's origin on what
  run init scored — and the round is the run's last. Where no arm reached the floor, or the
  parent holds none of its cells, the round is unwound.
- **Each cut is a ledger decision**, `paired_t_cut`, REPLAYED: its record names the arm, the rows
  it was cut at, the arms it raced against and the objective's γ and normaliser, so a resume under
  a changed scorer re-reads the same test off the rescored round.

**CAPO selects on its own objective; the bench scores it on the campaign's.** Per cell, CAPO's
objective is `fitness − γ · target_prompt_chars / length_norm` (§4): the per-sample correctness,
less γ times the scored prompt's length over the longest initial prompt's, unclamped. γ is
`paired_t`'s `length_penalty` (0.05, App. C.4) — declared on that node once, and read by the
`population` selector too, so the race and the population cannot rank on two objectives.
`length_norm` is measured when `capo_init`'s population is drawn and rides CAPO's
`optimizer_state`, so every round and every replay divides by the same number. The campaign's
`scoring.per_cell` composite is not in it: that formula is the ONE evaluator every optimizer's
arms, its election and its bench headline are scored under, and a campaign never writes CAPO's
term into it.

## CAPO's population and operators

The rest of CAPO's manifest (`assets/optimizers/capo/pipeline.yaml`, every value cited there):
`capo_crossover` merges two parents drawn at random from the population (c per round),
`capo_mutate` rephrases each child, `few_shot` mutates its shots, and the `population` selector
keeps the race's survivors, best first by mean objective on the cells all of them measured, cut to
μ — a `population_kept` decision, REPLAYED. The population rides `optimizer_state` on every round
document, so a resume or a fork re-seats it; the round advances when its best is not the
incumbent. Where the bench runs CAPO differently from the paper:

- **The prompt** is the individual's whole text: an operator reads an individual's fields as they
  render (`OptSearchPoint.render`) and its reply replaces them, riding `instruction` with
  every other field emptied (`rewritten`). So the evaluated prompt is CAPO's instruction and shots
  inside the campaign's framing, and the origin's own fields are a starting text, never scaffolding
  a child keeps.
- **The shots** render the demo row's ground truth; the paper asks the evaluation model for a
  reasoning chain and falls back to the label only when it answers wrong (§4).
- **The task description** is the campaign's framing and the origin's `answer_format`, where the
  paper's is hand-written per dataset (App. D.1).
- **The initial instructions** are generated by `capo_init` in the run's first round, by the
  campaign's optimizer model, where the paper generates its pool once with Claude Sonnet 3.7
  (App. D.2); μ of them are drawn, each with 0..k_max random shots (Alg. 1).
- **One model for optimizer and target** (§5) is the campaign's choice: the manifest names the
  optimizer model, and matching it to the target's is an overlay. Its `max_tokens` is that model's
  floor, where the paper caps output at 2048 (App. C.1); the paper states no temperature, and the
  manifest's 1.0 is vLLM's sampling default.
- **The length** is counted in characters of the scored prompt, the campaign's framing
  included, where the paper counts the prompt's tokens (§4) — no tokenizer ships.
- **The 5M-input-token budget** (§5) is the campaign's `token_budget`, which counts output tokens
  too.
- **A reply without `<prompt>` markers** makes an invalid arm that costs no cell.
- **The bench re-scores its incumbent** each round on the cells `population` names
  (`Selector.parent_cells`), which every arm's lift pairs on. From round 2 the incumbent races as
  a population member, so those are the cells it raced and the re-score replays them. Round 1's is
  the origin, no member: it is read on the first block alone — cells CAPO's budget leaves out,
  billed in the same book — so a round-1 lift pairs on one block.
- **A repeated request samples afresh**, as the paper's T = 1.0 draw does, though the optimizer
  reuse cache keys on the request: each call carries a seed drawn by round, node and call off the
  run's seed — the determinism clamp's where one pins it, else the campaign's id — so only a resume
  or a fork re-running a round replays a reply, and two unseeded campaigns draw their parents,
  shots and minibatches apart. A clamped seed pins every call to itself, so a repeat within that
  campaign replays, as the clamp asks.

## LEVI mapping and deviations

LEVI (arXiv 2605.09764) races nothing: its manifest (`assets/optimizers/levi/pipeline.yaml`, every
value cited there) has no eliminator, and its selector is an archive. Round 1 is its Phase 1
(Alg. 1), the **calibration round**; every later round is one paradigm-shift period (Alg. 2).

| Paper | Node · config |
|---|---|
| Seed pass `M_l.DIVERSESEED`, 4 seeds (Alg. 1, Table 5) | `levi_paradigm_shift` in round 1, `n_diverse_seeds: 4` |
| Calibration matrix on the discovery set, N_init = 5 (§3.3, App. A) | round 1 scores the origin (its parent) and the seeds on `proxy_css.discovery`, the pool's first 150 in bank order (App. I) |
| Proxy by greedy column subset, K_proxy = 30, (r, s, c) = (0.5, 0.5, 0.15) | `proxy_css`: `size`, `rank_weight`, `separation_weight`, `redundancy_weight`; `shared/statistics.py::greedy_column_subset`; a `proxy_selected` decision, REPLAYED |
| f restricted to the proxy (Alg. 1 line 11) | `proxy_css` draws the proxy, in one order, every round after calibration |
| Welford z-score + sigmoid, CVT with 50 centroids, TRYINSERT (§3, Alg. 1-2) | `map_elites`: `centroids: 50`; the statistics ride `LeviCalibration.stats` |
| Behavioural descriptors, input- and output-side (§3.1) | `map_elites.descriptors`, read by `optimizers/descriptors.py` |
| Refinement route: small model, ~90% of calls (§3.2) | `levi_refine`, Qwen3-8B (§4.2), `interval - 1` calls a round, `max_tokens` 16,384 |
| Parent ∝ exp(f / T), T ∈ {0.3, 0.7, 1.0, 1.2}; one inspiration, dropped 20% (App. B) | `levi_refine`: `sampler_temperatures`, `n_inspirations`, `inspiration_drop` |
| Paradigm shift every 10 evaluations, k = 3 clusters, `max_tokens` 4,096 (App. F) | `levi_paradigm_shift`: `interval`, `n_clusters`; Gemini 3 Flash (App. A) |
| Templates E.6 and E.7 | `resolved_prompts`, verbatim |
| Budget B; the best elite returned | the campaign's budget; the round selects the best elite unless it is the incumbent |

Where the bench runs LEVI differently from the paper:

- **Rounds, not workers.** A period's `interval - 1` refinements and its one shift are measured
  together and inserted in walk order, so the ratio holds exactly while no refinement sees another's
  insert; LEVI's four workers do (App. B). Its worker, process and timeout counts are the bench's.
- **The seed pass sends E.7** — E.1 is written for code — over the seeds so far, **starting from
  the origin** where Alg. 1 starts from none: the origin is measured on the discovery set anyway, and makes
  App. A's five calibration prompts of Table 5's four seeds. A seed reply without `<prompt>`
  markers is an invalid arm; nothing of it is fed forward.
- **No variants and no meta-advice.** The variant bursts (Table 5, App. F) have only a code
  template (E.5), and 80 variants on the discovery set exceed the paper's own prompt budget
  (Table 2); the
  meta-advice template is unpublished, so its section stays empty.
- **Unstated values, chosen:** the descriptors (prompt length and each proxy cell's objective);
  `feedback_failures` 3, GEPA's reflection minibatch; centroids by k-means over `cvt_samples`
  uniform draws inside the calibration descriptors' bounds (Figure 2); decoding temperature 1.0;
  a one-sided tie worth ½ in rank faithfulness; separation normalised by the widest column; the
  inspiration and the failures drawn uniformly.
- **Alg. 1 as printed:** the calibration prompts enter the running statistics at line 13 and again
  at their insertion, line 17.
- **A calibration that spans no descriptor volume halts the run** — one prompt placed, or every
  one on one point: the uniform draws, and so every centroid, would land there and the archive
  hold one cell. The paper's CVT has no answer for a zero-width bound either.
- **The artifact** is the individual's whole prompt and `{problem_description}` CAPO's task
  description, as § CAPO's population and operators states for CAPO; the failures shown ride
  `fence_untrusted`.
- **f** is the mean of the campaign's per-cell objective on the proxy — the task's scoring
  function, which LEVI takes as given. The paper re-evaluates on the full set for late-stage
  selection (§4.2); the bench scores its selection on the bench set instead.
- **A repeated prompt samples afresh**, as the paper's does, its call seeded as § CAPO's population
  and operators states for CAPO.

## GEPA mapping and deviations

GEPA (arXiv 2507.19457) races its children against their parents, not against each other: its
manifest (`assets/optimizers/gepa/pipeline.yaml`, every value cited there) runs one iteration of
Alg. 1 per round, and its eliminator is Alg. 1's acceptance test.

| Paper | Node · config |
|---|---|
| Split D_train into D_feedback and D_pareto (Alg. 1 line 1) | `minibatch.pareto_size` 50, a row count as the paper states each benchmark's (App. E.1); the Pareto set rides `GepaRoundState.pareto_set` |
| P ← [Φ], Φ scored on D_pareto (lines 2-5) | round 1 mutates the incumbent, the origin; `pareto` seats it with its Pareto-set scores off the bench's re-score, which reads the Pareto set alone (`Selector.parent_cells`) |
| SELECTCANDIDATE (line 7, Alg. 2) | `pareto`, at each round's close: per-cell fronts, the dominated removed, the next parent drawn ∝ cells led — `GepaRoundState.parent_id` |
| SELECTMODULE, round-robin (line 8, §3) | an individual renders one prompt, so the module is its whole prompt every round |
| A minibatch of b from D_feedback (line 9), b = 3 (App. E.4) | `minibatch.size` 3, the panel's first block |
| Feedback, scores and traces on M through μ_f (line 10) | `gepa_reflect` runs the parent on the minibatch through the scoring gateway, filed as a parent's reading; μ_f's text is `row_diagnostics.py::cell_feedback` |
| UPDATEPROMPT (line 11, App. C) | `gepa_reflect/1`, verbatim; the reply's fenced block is the child's instruction |
| σ′ improved on σ (lines 13-14) | `minibatch_gate`: a child not strictly above its parent's mean objective on the minibatch is cut — a `minibatch_gate` decision, REPLAYED off the parent's recorded scores |
| Φ′ added to P, scored on D_pareto (lines 15-18) | an accepted child walks the rest of the panel, the Pareto set; `pareto` admits it with its per-cell scores |
| Return the best average on D_pareto (line 21) | the round selects the best aggregate unless it is the incumbent; a tie holds |
| The reflection on the system's own model: Qwen3 8B at 0.6, top-p 0.95, a 16,384-token window (App. E.2) | `gepa_reflect` on `qwen/qwen3-8b`, `temperature` 0.6, `top_p` 0.95, `max_tokens` 16,384 |
| Budget B in rollouts (Eq. 2), matched to MIPROv2's per benchmark (App. E.4) | the campaign's round, spend and token ceilings |

Where the bench runs GEPA differently from the paper:

- **No merge.** Alg. 3 admits a pair only where one descendant kept the ancestor's module, and
  Alg. 4 then hands the child the other descendant's module — on a one-module individual that
  child IS the other descendant. The bench runs the paper's GEPA row, not GEPA+Merge.
- **The prompt** is the individual's whole text, as § CAPO's population and operators states for
  CAPO: round 1's reflection reads the origin's fields as one instruction.
- **Unstated values, chosen:** the Pareto set is the pool's first `pareto_size` rows in the bank's
  order, 50 where the paper sizes it per benchmark — every accepted child walks it whole; the
  minibatch is drawn uniformly each round by the run's seed, where the reference implementation
  walks a once-per-epoch shuffle; dominance is read as the reference implementation reads it — a
  candidate is dominated when every cell it leads another survivor also leads, lowest aggregate
  removed first; the examples are laid out as its `# Example` / `## Inputs` /
  `## Generated Outputs` / `## Feedback` markdown, inside the untrusted-content fence.
- **The acceptance test** compares means over the minibatch cells both runs graded, on the
  campaign's per-cell objective, so an errored cell leaves both sides; the reference implementation
  compares sums. A parent perfect on the minibatch still gets its reflection, as Alg. 1 has it;
  the reference implementation skips that iteration.
- **μ_f** is what the scorer can say about a cell — its objective, its correctness where the
  composite differs, the expected answer and each judge's banked reason — where the paper's
  feedback functions are written per benchmark (App. E.1).
- **Top-k 20** (App. E.2) is not carried, an llm node's call config having none, and the context
  window stands in for the output cap. A reply without a fenced block is taken whole, as the
  reference implementation takes it.
- **A repeated reflection samples afresh** at 0.6, as the paper's does — the same parent on the
  same minibatch included — its call seeded as § CAPO's population and operators states for CAPO.
- **One model for reflection and target** is the campaign's choice, as it is CAPO's: matching
  `gepa_reflect`'s model to the target's is an overlay.
- **A pool member carries a verdict on every Pareto-set cell**, the seat and a child alike, so no
  aggregate averages fewer cells than another. One missing any — an errored or ungraded cell — is
  refused, a `pool_refused` decision (ARCHIVAL) naming the cells; a round seating no one leaves the
  pool empty, and the next reflects on the incumbent again.
- **The draw moves to the close.** Alg. 2 runs at an iteration's start; the bench draws the next
  parent when the round closes and banks it, so a resume or a fork re-seats the front and the
  draw together. A member's Pareto-set scores are banked at admission, and a scorer change
  re-grades none of them, as LEVI's elites.
- **`max_rounds` counts rounds, and a GEPA round is one proposal**, so a campaign sizes its round
  cap as the child count it wants — LEVI's as `interval` evaluations a round. The bench's runaway
  guard (`runner/loop.py::HARD_CAP_ARMS`) counts arms raced, so it binds one-child GEPA no sooner
  than a five-arm optimizer.
- **The bench re-scores its incumbent** on the whole panel each round, minibatch included — cells
  GEPA's rollout count leaves out, billed in the same book. Under `lift_reference: parents` each
  child's lift is read against its GEPA parent on the child's cells, which the proposer already
  measured.

## On-disk shape and replay

Each `ELIMINATION_CUT` / `LEADER_LOCK_IN` decision record (in `rounds/round_NNNN.json`) carries the paired snapshot under `data`: `p_best`, `leader_id`, `candidate_sample_ids` (the cells the posterior was fit on, in walk order — the candidate's GRADED cells, so a cell that errored is not among them) and `prior_histories[cid]` (each paired prior's grades on exactly those cells, after backfill). A collapse cut fit no posterior and carries none of them. `inputs_ref` records the gate parameters in force **and which `EliminationGate` (`pobb/checks.py`) fired** — only ε computed a posterior, so a replayer re-deriving a collapse cut under the ε rule tests a real `p_best` against a bar nobody set.

That makes the divergence replayer self-contained (`optimizers/potter/resume.py::_pobb_replay_snapshot`): it takes the rescored rows at `candidate_sample_ids`, hands `prior_histories` over as the priors, and calls the same `paired_p_best` on the cycle's fixed δ ruler. **No cross-round "find R1_winner in prior rounds" logic and no backfill during replay** — the decision record is the entire input, and the θ rule is closed-form and deterministic, so replay is bit-for-bit when no scorer change moved the candidate's grades. When the active scorer differs the candidate side is rescored and the prior side stays at the recorded grades; a scorer change that materially shifts priors surfaces as divergence via the candidate side.

Recorded booleans from pre-graded decisions coerce to 0.0/1.0 — the identical values the live path fed.

## Open questions

1. **Tie-breaking at budget cap.** When the round cap is reached and the top 2–3 candidates have similar `P(best)`, no test declares a clean winner. Ship pick-by-point-estimate; design a cap-extension policy after observing how often this fires.
2. **Small-*n* θ edge cases.** Few observations do NOT keep the gate conservative on their own — the ties narrow both `se`, so at a low base rate `p_best` sat at 0.124 rather than near 0.5 and every arm was cut at `n_min` (`sealqa-longseal-12`, four arms, identical to six decimals). Step 4 bounds that; the EB hyperprior on the ability variance is what keeps the small-*n* fit itself from collapsing.

## Why this family and not another

Mid-round abortion is an instance of **best-arm identification** in stochastic multi-armed bandits: given a fixed population of arms and a per-pull noisy reward, identify the highest mean at minimum sample cost. The literature splits on fixed-budget vs fixed-confidence, frequentist vs Bayesian, and pairwise vs population; PoBB sits in one cell, and three design choices put it there.

- **Every prior, not only the leader** — the question is "is this the round winner?", and the code answers it with a pairwise θ comparison against every prior, minimised: a bound on P(best), not the joint posterior. The comparison is pairwise, so this does not set PoBB apart from LUCB (Kalyanakrishnan 2012), Bayes-UCB (Kaufmann 2012) or Hoeffding Races (Maron & Moore 1993) on structure. What still does: each of those tests against the current leader alone, and they are frequentist bounds rather than the posterior reading below.
- **Bayesian over frequentist** — `P(c is best)` is one operator-readable number ("c042 73% probability of winning round"); a Holm-corrected p-value or a Hoeffding bound is not.
- **Fixed-confidence (ε) over fixed-budget** — broken candidates stop as soon as the evidence floor is met, indistinguishable ones run to the cap. Phased fixed-budget algorithms cannot do the first: Successive Rejects (Audibert 2010) and Sequential Halving (Karnin 2013) both run a clearly-broken candidate to the phase boundary. "Fixed-confidence" is loose here: PoBB's ε is a bar on a posterior reading, not an anytime-valid error rate, so under optional stopping it carries no frequentist guarantee ([`../research/external-constraints.md`](../research/external-constraints.md) § Ranked, item 3). The anytime-valid alternative is a confidence sequence built from test supermartingales, which stays valid at any stopping time ([Hsu & Shekhar, *Efficient Sequential Evaluation of LLMs*](https://arxiv.org/abs/2607.17409)); that is the form the stop rule's guarantee would have to take before it is claimed.

**Wilcoxon signed-rank + Holm-Bonferroni is what this replaced**, and it was pairwise with no joint distribution and variance-agnostic by construction. Holm survives in the codebase as a *reporting* correction only (`shared/statistics.py::holm_adjusted`) and reaches nothing in the loop. **OCBA** (Chen 2000) is the closest classical relative — same population-aware Bayesian family, but it addresses budget *allocation* where this is a stop rule; PoBB likewise drops Top-Two Thompson Sampling's allocation half, since the loop iterates candidates deterministically, and keeps the stop rule.

## Comparison to MCTS

PromptPotter **is** AlphaZero-shaped MCTS over the lineage tree, and all four phases are present.

- **Selection.** When L2/L3 judges the current subtree exhausted it emits `fork_proposal: {reason}` — *whether* to rewind, a judgment no rule makes well. *Where* is then decided by UCB1 over the backpropagated tree (`application/mask/backprop.py::select_rewind_round`): each ancestor's mean ability plus an exploration bonus for how little it has been tried. The layer deliberately does *not* name the round, because no panel ever enumerated the ancestors and their fitness, so a free-form offset was an unanchored guess carrying the loop's most expensive decision.
- **Expansion.** A round: L1 proposes a population from the selected node.
- **Simulation.** A deterministic forward pass on the eval set rather than a random rollout — AlphaZero is the published precedent for exactly that swap, which is why the determinism does not make this not-MCTS. Within a round, PoBB prunes losers before they consume the budget, a sharper instrument than UCB1 for the *sibling* comparison because a round's arms are measured on shared samples.
- **Backpropagation.** Each round's Rasch ability θ is rolled up to every ancestor as visit count + value (`accumulate_node_stats`), so an ancestor's statistics answer what re-expanding from there actually yielded, including in branches it never ran itself.

**Value is θ, never accuracy, and a fork's inherited prefix is not a fresh visit** — owned by `application/mask/backprop.py`'s docstrings; both naive forms fail silently, since the fold still returns a plausible number, so the fold keeps only each cycle's own new rounds and re-attaches its spine to the branch-point.

Rollout cost is where we stay deliberately conservative: a "rollout" here is a full round of LLM calls per candidate, so exploration is sample-efficient by design — closer to AlphaZero's PUCT than to vanilla UCT over free rollouts. What it buys is **recovery from dead-end branches**: a trajectory that exhausts itself no longer just ends the cycle.

## References

- **Russo, D. (2016).** *Simple Bayesian Algorithms for Best Arm Identification.* COLT. — the PoBB / Top-Two Thompson Sampling family.
- **Maurer & Pontil (2009).** *Empirical Bernstein bounds and sample-variance penalization.* COLT.
- **Kalyanakrishnan et al. (2012).** *PAC subset selection in stochastic multi-armed bandits.* ICML. — LUCB; rejected for testing against the leader alone.
- **Audibert, Bubeck, Munos (2010).** *Best arm identification in multi-armed bandits.* COLT. — Successive Rejects; rejected for not adapting within-round.
- **Chen, C.-H. (2000).** *Optimal Computing Budget Allocation.* Operations Research. — the closest classical relative; allocation rather than a stop rule.

The `classify_result()` rule table and its three load-boundary effects: [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md#classify_result-fatal-classification). Operator framing: [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md#deprecated-samples).
