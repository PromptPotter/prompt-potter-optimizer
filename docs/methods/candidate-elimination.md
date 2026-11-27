# Candidate Elimination

**Method:** stop a candidate when its posterior probability of being the round's best drops below ε, or clears the election's own admission bar.

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

## Why the comparison has to be paired

PoBB (Russo 2016) assumes i.i.d. observation; the shared order front-loads the seed's misses on purpose, so unpaired, a leader that ran the easy prefix reads as unbeatable. **PoBB priors are sample-keyed, and the leader is backfilled onto the candidate's upcoming samples before each comparison.**

- **Sample-keyed priors**: a prior holds a cell it SCORED, so an errored one is caught up again. A prior's caught-up cells are rows of its round reading too, so the race and the election read one sheet. The parent enters on its OWN rows, never the frontier.
- **Reactive backfill, per sample**, committed once the cell is taken, so a backfill for an untaken cell is paid and never written. Each (prior, cell) pair is measured once and lands in `measurements/` for every future round, stamped with the PRIOR's identity — never a cell of the panel walk that bought it.
- **Cost: roughly one extra candidate-equivalent of LLM spend per round** — the price of validity. It also removes lucky-prefix inflation mechanically.

## The θ rule

PoBB is a **block race** (`potter/race.py::PoBBRace`): every arm walks the panel at once and, at each checkpoint they share (potter's `RoundSamples.block_size` is one cell), the field — the parent and every arm still standing — is read together (`pobb.py::PoBBCheck.check`):

1. The cells read are those θ is read on (`rasch.py::theta_cells`, never rows walked — [`verdict-resolution.md`](verdict-resolution.md) § The round order) that every standing arm scored, or, under `pobb.shape: per_round`, that any did: the union, since each arm answers rows of its own. An arm is cut only once it answered `elimination_n_min` of them itself, and its ε(n) ramps on that own depth; under `per_candidate` every racer's depth is the shared count, as before. A rival with no row on a cell is **excluded there, never zero-filled**: the fit reads each member on the cells it answered.
2. One hierarchical Rasch fit of the whole field on those cells, θ and δ together (`rasch.py::fit_rasch`), and a Monte Carlo over its θ draws, seeded by the read (`pobb.py::field_p_best`): `p_best` is each arm's posterior probability of being the field's best. The shares sum to 1, so the multiplicity of a wide field is inside the statement and slot order enters nowhere. A pairwise "credibly worse than some rival" cut was slot-dependent (serial, 0.25 → 0.63 by slot) or, raced against every sibling, cut a planted better arm 11.8% of the time against serial's 7.1%; the joint fit cuts it 4.7% at ε 0.05, flat by slot.
3. Stop when `p_best < ε(n)` — eliminated (graded by depth: `epsilon_floor`; switch `epsilon_stop`). A round the parent and k arms open is tied at 1/(k+1), so an ε at or above it cuts a tied leader, and the config check names it (`couplings.py::POBB`, `epsilon_at_a_tied_share`). **An arm that ran its panel joins the priors** (`runner/round.py::_conclude_arm`), so it stays in the field of its top-up; an eliminated one never does.

The **origin panel's top-up is one more stretch of the same race** (`runner/round.py::read_origin_panel`): a cut there lands as the arm's `Cut`, which `runner/round.py::panel_triggers` reads, so the monitor never buys it back.

**The race and the election read one row set** (`results.py::RoundResult.raced`: the draw, then the origin-panel cells it lacks) **on one ruler**, the scale the round opened on; the round's cells join δ after its election, with what the origin panel's line bought after it (`results.py::RoundResult.closed_rows`), which no race read.

**The `posterior` bar** admits an arm on

a raw θ lift above `domain/ruler.py::FIT_SLACK`, more shared cells won than lost on VERDICTS, AND its
`P(θ_arm > θ_parent)` bounded by what the DISCORDANT pairs support (`shared/statistics.py::sign_posterior`; directional, so contradicting pairs collapse the claim to 0.5; **discordance counts VERDICTS, `Grade.fitness`, never the objective**) above potter's `theta_election` `admission` — never PoBB's ε, which
says when to stop BUYING an arm, not when to promote one. An `admission` below `sign_posterior(1, 0)`
= 0.75 is refused, so one discordant cell never promotes. Admission is bounded; the election RANKS on
the unbounded posterior (bounding it moved a banked crown across a 0.0007 gap). **A pick's crowning
cells are its discordant wins over its bar**: there its rows are the luck the selection kept, so they
stay out of every race it judges; a cell it tied on stays in the pool. They are read off the pairing
the round banked (`results.py::Election.deciding_pair`, `selection.py::crowning_cells`), on a crowned
round alone. **Evidence is bought in one
place, the round's race, up to the round's cap**: a leader short of the bar there is not crowned, a
false negative the bar accepts. A round ends one of three ways (`origin_panel.py::RoundEnding`, filed
as `results.py::Election.ending`; `run_standing.py::line_reference` reads it): crowned, moved onto its
uncrowned leader, or held on its parent, the declared pick staying the last crown. Potter's
`theta_election` `next_parent` picks between the last two:
`not_lost` (the default) steps onto the round's leader unless the parent beat it, a neutral move on
the fixed origin panel (`vote: overlap`, paired lifts add), so the line never falls below a held
crown; `leading` always steps onto the leader (comma selection); `crowned` keeps the parent (plus
selection). `RoundWinner` replays `[ending, leading]` (`potter/election.py::RoundWinner.decide`).

**Each selector owns its crown; the bench re-judges none.** capo, gepa and levi crown by their own
official rules. Potter's crown statistic is `theta_election` `crown`
(`domain/crowning_bar.py::CrowningBar`, potter's alone), read over every readable arm of the round at
once, the round's crown and lead the arm it ranks first (`CrowningBar.rank`, signed: zero is level
with the parent; `CrowningBar.lost` is `rank < 0`). Each arm's evidence is the bench's one pairing
(`scoring/paired.py::read_pair`) of the arm over its parent on the fitness verdict alone, on the
vote's cells (`potter/election.py::RoundWinner.pairings`): its lift is the green bar's point and its
`FlipCounts` the cells won and lost; an arm read on fewer than two shared cells is no subject.
`paired_point` (the default) crowns that lift above zero, ranked on it: its interval is
information, which the advance reads, so a point crown whose interval spans
zero closes `NOT_SEPARATED` and the line moves all the same. `posterior` is the bar above. The θ
crowns (`posterior`, `empirical_bayes`) elect on ability and floor coverage on θ cells, the others on
the fitness column and the pairing's scored cells (`ThetaElection.elects_on`). Ablation switches of potter's crown only:
`sign_test`, the exact
paired sign test on the discordant verdicts, Holm-corrected across the round's arms; `e_process`, a
uniform-mixture e-value on the same discordance at `arms / α`, valid wherever the race stops;
`empirical_bayes`, the θ lift shrunk toward the tenant's banked arm-vs-parent lifts on the dataset
(`selection.py::banked_lift_prior`: τ² = observed variance less mean noise variance, floored at 0),
its posterior carrying μ0's own error, so a τ at 0 never reads as certainty on μ0's sign
(`crowning_bar.py::LiftPrior.shrunk`). No reading crowns an arm its paired verdicts contradict. The
paper's earlier behaviour is `crown: posterior`, `next_parent: crowned`,
`optimization.not_separated_stall: resets`.

**Which rows the bar pairs on is potter's `theta_election` `vote`**, which the bench never reads:
the election banks the cells on its replay inputs (`RoundWinner.cells`), so the crown and the replay
read one cell set. `draw` pairs an arm with its parent on every cell raced.
`overlap` (the default) pairs them on the origin panel's cells alone, the rows both hold, and only an arm the
panel trigger seats there (`potter/election.py::seated`, `origin_panel.py::panel_trigger`) is electable: the panel, a monitor
under `draw`, is the verdict, and a PoBB stop, which it never seats, leaves the election. Its cells are bought before the election
(`runner/round.py::read_origin_panel`), the crowning cells included, since the panel stays fixed
at C0; θ, its lift, the statistic and `admission` are the same under both. The panel holds
`CampaignConfig.origin_panel_cells` of each benchmark's cells (unset: as many as a round walks),
cut from the origin's own round, which `sp_budget_origin` sizes and must cover.

## Who answers a block's calls

`pobb.shape` says it, through the row policy any eliminator hands its view to
(`row_policy.py::send_rows`, off its `RowKnobs` and the field its cut last fit; the race's one answer
to the loop is `race_protocol.py::Decider.decide`: the stops it cuts, each survivor's row order and
the calls it sends). `per_candidate` hands every survivor `block_size` calls on the same
next rows. `per_round` (the default) hands the block's `block_size × survivors` calls one at a time
(`row_policy.py::_hand_out`) to the walk whose next answer tells the decision most; a walk handed none
waits out the block, and the block closes once the calls it handed out are back.

The derivation, to first order. The decision on arm k is the cut, P_k < ε(n_k), and past it the
election, which reads the arm uncut. Write z_k = Φ⁻¹(P_k) − Φ⁻¹(ε(n_k)) for its probit distance
from the bar and v_k for its posterior variance in PoBB's last fit. One answer on row r carries
Fisher information I_k(r) = a²p(1−p) about θ_k (`rasch.py::expected_information`, at the θ
`refit` seats: under `per_round` refit every arm's is the round's). It removes about v_k²·I_k(r)
of θ_k's variance, and the chance that moves the cut's decision on k scales with the density of z_k
at the bar, φ(z_k)/φ(0). So a call goes to the arg-max of I_k(r)·v_k²·φ(z_k)/φ(0), and v_k shrinks
to 1/(1/v_k + I_k(r)) for each call handed, so a block spreads its calls. Before PoBB first fits,
v_k and the nearness are 1 and the information alone decides: at one θ on a cold ruler, every
pair ties, and a tie goes to the walk handed fewer, a round robin. An arm far past the bar either
way (a sure cut, a sure leader) draws few calls while a contested one remains; one alone draws
them all. Under `per_round` the parent catches up on every row a candidate takes it never answered,
so its bill grows with the union of the rows. After the race it is re-scored on the round's whole
draw plus each pool row an arm took (`nodes.py::RoundSamples.read_on`), never on the taken rows
alone: the next round's proposer reads the parent's own cells (its critique's misses, the failing
samples it is shown), so a round whose arms were all cut, or that walked none, still hands its
proposer fresh evidence.

`pobb.dispatch` says when the decision runs (`race_protocol.py::Trigger`). `batch` decides once
every call it sent is back, each decision closing a block. `rolling` (the default) decides on every
returned row through the same `decide`, with no block every survivor closes together (no readout
block line, no `block` on the arm). Each decision hands calls by the same arg-max, as many as § How
many calls a rolling decision sends derives, the calls out keeping their seats. `pobb.read_order`
says which returned row a decision reads next. `launch` reads them in the order they were sent, so
the record is the press's invariant. `arrival` reads them as they come, files that order on the
ledger (`ledger_scan.py::StandingRounds.reads`), and a resume reads the order filed
(`query_loop.py::run_walks`).

**The press sends ahead only under `batch`** (`Trigger.ahead`): there a call past the decided ones
lands on rows the next decision may still take, so a cut wastes at most the press. Under `rolling`
every read decides, so a call past the decided ones would be no decision's: it would file a send and,
under `arrival`, move the read order, so the press bounds the calls out and never adds one.

## How many calls a rolling decision sends

A rolling decision sends a walk calls only while the calls it has out cannot settle the cut on it
(`row_policy.py::_settles`), and none where they can. Calls out settle nothing before they reach the depth
the cut first reads, `elimination_n_min`, so a young walk is sent at least that far. Past it, the
I_out of information those calls carry moves z_k by about √(v_k·I_out / (1 + v_k·I_out)) in its
posterior's standard units (one preposterior sd). An arm whose z_k lies inside that reach can cross
the bar on what is already out, so a further call would be spent before the decision it serves
could use it. An arm outside the reach cannot, so the next call goes out now, by the arg-max above.
A sure leader therefore rides to its budget at once, as it would under `batch`, while a contested
arm waits on its calls out. The parent stands no bar, so its top-up is never settled. With
`epsilon_stop` off the cut never fires and every walk is sent its whole budget. The press caps what
is out at once, never what is committed, so a cut wastes at most the press.

## Two regimes

- **Early — high-signal.** A field member clearly behind loses its share in 3–5 queries (Wilcoxon needed ≥8 at α=0.2, being variance-agnostic).
- **Late — low-signal.** Gaps ≤0.02; near-ties share `p_best` evenly, ride to the cap, and the θ election picks.

## Tunable knobs

- potter's `pobb` `epsilon_stop` — the one stop's switch, shipped on (capo-pobb reads the same `pobb` config).
- potter's `pobb` `epsilon` (`0.05` in its manifest: the planted-arm bar holds there and misses at 0.10), under 0.5. A stop ends measurement; it is **not** a verdict and, under `vote: draw`, never removes the candidate from the election (`is_leader_eligible`). **A stopped arm buys no cell after its stop**: the election reads it on its draw (`runner/round.py::measure_population`) and no `PanelTrigger` seats it on the origin panel (`runner/round.py::panel_triggers`); only a stopped arm the election PICKS is topped up, as every line member is.
- potter's `theta_election` `admission` (`0.85` in its manifest) — the election's own bar (§ The θ rule).
- potter's `theta_election` `vote` (`overlap` in its manifest) — which rows that bar pairs on (§ The θ rule).
- potter's `pobb` `epsilon_floor` — ε at `elimination_n_min`, ramping linearly to `epsilon` over the next `elimination_n_min` cells read (`PoBBCheck.epsilon_at`). Equal to `epsilon` (as shipped) = a flat bar. **The bar ramps IN only, and nothing guards the tail** — else every late loser spends its budget; the price is that `PairedReading.reference_level` refuses an arm stopped short of a parent cell (`selected_by_stop`), so it ranks on θ alone. Aggression belongs here, never in `elimination_n_min`; a floor above `epsilon` goes flat and `epsilon_floor_inverted` reports it.
- `OptimizationConfig.elimination_n_min` (default `6`) — the one min-samples floor, gating PoBB **and** ruler warmth (δ≡0 ⇒ θ = logit-fitness until this many grade-A samples bank): difficulty and ability become trustworthy at one threshold.

## The full elimination ladder

Five mechanisms, fixed order, each owning its memory field and display annotation:

| # | Mechanism | Fires | `n_min` | Candidate fate | Memory | Source |
|---|---|---|---|---|---|---|
| 1 | **Validation skip** — a fatal `Proposal.validation_failures` (`Proposal.invalid`) | pre-score | — | opens no walk: no cell, no backend call, keyed by its slot | `ArmState.validation_failures` → `l1_wounds` | `runner/round.py::_open_arm` |
| 2 | **Stale-data protocol** — a cached result classifies infra or fatal (`is_deprecated`); a repaired, answered row replays | every degraded query | — | annotated + possibly re-measured / swapped | — | `scoring/sample_measurement.py::execute_stale_data_protocol` |
| 3 | **`DegradationCheck` — fatal fast-path** — latest query's `classify_result()` returns a fatal code | every query | **1** | eliminated; `RuntimeFailure` | `runtime_failures` | `scoring/classification.py` |
| 4 | **`DegradationCheck` — rate-based** — `degraded_rate >= threshold` | every query | **3** | eliminated; `RuntimeFailure` | `runtime_failures` | `scoring/classification.py` |
| 5 | **`PoBBCheck`** — the field's `P(best) < ε(n)`, behind `epsilon_stop` | every checkpoint | `n_min` | eliminated; records `elimination_cut` | — | `optimizers/potter/pobb.py` |

The first signal ends the candidate: fatal beats rate beats PoBB.

**Each rule answers how EARLY it could fire** — early, never late — so a look-ahead walk launches a cell only if no rule can fire before it, and a rule's cut discards at most one call. A `batch` decision bounds no launch: a walk has at most the press's cells out past the rows it sent, on the order the last decision ranked, each re-seated by its sample where the next decision moves its row, so a cut there discards at most the press; under `rolling` a walk sends no row past those its decisions sent, and a cut discards at most its calls out, the press. A pause, skip or budget stop keeps what is paid for and starts nothing; an in-flight call is cancelled only where that stops its bill; no cell starts that the spend book cannot hold at its bound.

**One comparator, one stop rule — do not add a sixth.** No unpaired answer-collapse cut: one label on a hardest-first `n_min` prefix is no reading against the parent, which the posterior already takes; one answer where the parent answered apart on the same cells is refused at the election instead (`domain/arms.py::is_electable`). No paired-margin futility gate: discordant binary wins are a second comparator beside the θ ruler, re-encode the election's bar, and go inert on a graded backend. Futility, if bought, is one gate **on the θ ruler**.

## CAPO's race — the `blocks` sampler and the `paired_t` eliminator

CAPO's survival selection (arXiv 2504.16005 §4, App. B) takes PoBB's row 5 for its own manifest. A **block race**, as PoBB's under `batch`: each decision cuts the arms its paired t loses together and sends every survivor its next block (`race_protocol.py::next_block`). `DegradationCheck` still runs per arm.

- **`blocks` (sampler)** — `block_size` (b), `max_blocks` (z_max): the first `max_blocks` whole blocks of the search split in bank order, the same every round, never shuffled (App. C.4); `RoundSamples.block_size` hands the boundaries over; a pool short of one block refuses.
- **`paired_t` (eliminator)** — `alpha`, `length_penalty` (γ), and μ = the `population` selector's `size` (one knob). At each block's close every live arm meets every other in a one-sided paired t (`shared/statistics.py::paired_mean_t`) on CAPO's objective over the same k blocks; arms that μ others significantly beat are cut together off pre-cut readings — App. B's `n_sig_better ≥ μ` (§4's prose says "more than") — with no multiple-test correction, as CAPO races. At μ or fewer the race is settled (`locked_in`), and only then or at `max_blocks`: a survivor set above μ walks the next block (App. B, `while |P| > μ and i < z_max`). A first block that cuts the set to μ settles the race there, as CAPO's does — with c offspring and μ parents a common case, since γ makes an offspring longer than a parent it ties on fitness significantly worse on every shared cell — so `max_blocks` binds only where cuts come slower. `Elimination.context.gate` reads `outscored` / `settled`, with its `block` and `raced_against`; the stream's `p_best` is the smallest `p_better`.
- **Departures.** A race starting at ≤μ arms still walks one block so the selector can rank (App. B races none). `paired_mean_t` floors the SE at `1/(4n)`. A budget-cut round is a holed panel under `optimization.panel_gate`: `strict` unwinds it (a raised-ceiling `resume` re-runs it); `off` elects on the blocks paid for (`Selector.elects_partial`) among arms at the coverage floor (`nodes.py::Selector.covered`, the selector's own count), the parent read on cells the archive holds, as the run's last round; no arm at the floor, or a parent with none of its cells, unwinds.
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
- **A repeated request samples afresh** (the paper's T = 1.0): each call is seeded by round, node and call off the run's `run_seed` (`Cycle.rng`), so only a resume or fork replays a reply; a clamped seed pins every call.

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
| Split D_train into D_feedback and D_pareto (Alg. 1 line 1) | `minibatch.pareto_size` 50, a row count as the paper states each benchmark's (App. E.1); the Pareto set rides the `pareto_set` slice (`gepa/members.py::ParetoSet`) |
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
- **Top-k 20** (App. E.2) is not carried (no llm-node knob).
- **A reply without a fenced block is one refused child**, as every text node's out-of-form reply (the reference takes it whole).
- **A repeated reflection samples afresh** at 0.6, seeded as § CAPO's population and operators states.
- **One model for reflection and target** is the campaign's overlay, as CAPO's.
- **A scored member carries a verdict on every Pareto-set cell**; one missing any is refused, a `candidate_refused` decision (ARCHIVAL); a round seating no one reflects on the incumbent again.
- **The draw moves to the close**: the next parent is drawn and banked at round close, so resume and fork re-seat candidates and draw together. Pareto-set scores bank at admission; a scorer change re-grades none, as LEVI's elites.
- **`budget.rounds` counts rounds and a GEPA round is one proposal**, so the cap is the child count wanted; the runaway guard (`runner/loop.py::HARD_CAP_ARMS`) counts arms raced.
- **The incumbent is re-scored** on the whole panel each round, minibatch included; under `lift_reference: parents` each child is read against its GEPA parent on the child's cells.

## On-disk shape and replay

**A cut record names every cell it read by address, once — no cross-round lookup, no backfill** — each side's cells the field was read on, the arm's and every rival's (`race.py::EliminationCut.cells`), whose union is the read. The round banks every side (the parent re-read on its cells), so replay re-reads each off the round's rows: bit-for-bit on the fixed δ ruler absent a scorer change, and under one every side is rescored alike. Why this family of stop rules: [`../research/landscape.md`](../research/landscape.md) § Why PoBB's family and not another. The fatal-classification rule table: `domain/results_health.py::classify_result`.
