# Benchmark Methodology

What we measure on, what it scored, and what we refuse to measure on. Model + `reasoning_effort`
pins → [`../operations/dataset-reasoning-matrix.md`](../operations/dataset-reasoning-matrix.md).
Selection criteria and the recon detail behind each verdict →
[`../operations/dataset-selection-rationale.md`](../operations/dataset-selection-rationale.md).
The BBEH head-to-head protocol →
[`bbeh-comparison/README.md`](bbeh-comparison/README.md).
Who the peers are and how we compare on capability rather than score →
[`related-work.md`](related-work.md).

> **Nothing here is a result, and that is a gate rather than an omission.** Every figure below is a
> 25-sample admission screen — what an *origin* scored, used to decide whether a dataset is worth
> wiring at all. No head-to-head number is published because the optimizer is still being tuned to
> produce lift a benchmark could measure, and because the peers and PromptPotter do not yet call the
> same model ([`bbeh-comparison/README.md`](bbeh-comparison/README.md) owns both blockers). Read the
> roster as a screening log with a date, not as a scoreboard.

## The admission bar — `gpt-oss-20b` must not already solve it

Every candidate is screened against **`openai/gpt-oss-20b @ reasoning_effort: low`**: the cheapest
model we would ship a campaign on, and weak enough that a prompt still has work to do.

**Saturated** means the model already scores near its ceiling, so no prompt can produce a lift larger
than the measurement's own error bar. Every optimizer ties and the comparison measures noise. A
dataset a cheap instruction-tuned model already solves is saturated by definition and is **not tested
here at all** — not benchmarked once for completeness, not carried as a weak row. So **an absent
dataset is usually saturated, not overlooked**; the roster below records what each one scored, which
makes the omission checkable rather than asserted.

Two ways to misread the bar:

- **A low score is not admission either.** A dataset the model scores near-zero on has no reachable
  headroom and ties just as hard — the floor and the ceiling produce the same symptom from opposite
  directions.
- **The constant-answer floor is invisible to the bar** — owned by
  [`../operations/dataset-selection-rationale.md`](../operations/dataset-selection-rationale.md)
  § Selection criteria, criterion 1b. Screen it before reading any origin in the roster below.

## Order of use

| # | Dataset | Role |
|---|---|---|
| 1 | **BBEH** | Headline benchmark — carries the head-to-head against peer optimizers. Its 28% was measured at `gpt-oss-20b @ low`, **not the model the dataset runs** — that model is owned by [`bbeh-comparison/README.md`](bbeh-comparison/README.md) § The protocol; re-measure before quoting it. |
| 2 | **HotpotQA** | Wired 2026-10-14 (tenant definition `hotpotqa`, distractor validation, `em_match`), measured at the bar and seated in the L4 pool under both solvers (§ Every dataset we measured). Most valuable head-to-head addition after BBEH: MIPROv2 / GEPA / adv-CoT all use it. |
| 3 | **AIME 2025** | In band at 30%, wired. Limited by size (30 problems, no split), not by headroom. |
| 4 | **Chess puzzles (imec)** | Candidate, **not wired and unmeasured at the bar**. 1,118 Lichess puzzles in a published 559 / 559 development / test split, stratified by puzzle length (one to five moves) and rating (400–2,800), scored by exact match on the move — [arXiv 2610.00416](https://arxiv.org/abs/2610.00416), code and renewal scripts at [`imec-ailabs/Automatic-Prompt-Optimization-with-Chess`](https://github.com/imec-ailabs/Automatic-Prompt-Optimization-with-Chess) (Apache-2.0; the Lichess export is CC0). What earns it the slot: six DSPy optimizers (GEPA, MIPROv2, SIMBA, COPRO, BootstrapFewShot, BootstrapRandomSearch) are already published on that split across eight models, for about $800 in total, so one more arm lands beside existing rows. Two things to settle before wiring: `gpt-oss-20b` is not in their panel and their weakest models stay far behind, so the risk at the bar is the **floor**, which a rating cut can move; and a multi-move puzzle counts only when every move is right, which the scorer has to reproduce. Abstract, setup and section takeaways read 2026-10-11; the results tables not. |
| — | **`justlogic/d234`** | Not a publication benchmark — the **focus instrument**, where the optimizer's own behaviour is measured round over round. By far the most-measured dataset here. |
| — | **`justlogic/d234-held`** | The head-to-head's bench instrument: that dataset's search and demo pools under a wider bench (`justlogic/d234-held/README.md`). Unmeasured. |
| — | **`reactome-typeql-42`** | TypeDB's query-writing benchmark, run by their own harness (`reactome-typeql-42/README.md`): the model writes TypeQL, the query is executed and its result graded. Outside the head-to-head — the comparison is against TypeDB's published rows, on the model the operator picks. Development screens only so far, recorded in that tenant definition's `README.md`. |
| — | **GSM8K** | **Measured at the bar 2026-10-12 and saturated**: 90–100% at origin on 30 rows under both `gpt-oss-20b` and `gpt-oss-120b` (`promptpotter-self/pool/dataset.yaml::panel`), so the literature's 78% was a weaker solver's origin, not this one's. CAPO's published run moves it 78.1 → 93.7, which makes it the opponent's home turf and the strongest form of a head-to-head — on a solver that sits in band there (the literature's 7B class), never on ours. `gsm_hard`, its harder re-cut, is the seat instead. |

BBEH is the headline but the wrong *iteration* target: at the bar every cycle ties at noise and PoBB
cannot separate candidates. That is why the focus role exists separately, and why it collapses back
into the headline role once L1 produces measurable lift on BBEH.

## Every dataset we measured

Default conditions unless noted: **`gpt-oss-20b @ low`, `temperature: 0.0`**, 25-sample slice. These
are *measurements*, not projections — the trail's one systemic finding is that literature anchors
from older proxies **underpredict `gpt-oss-20b @ low` by 10–20pp** (five recons out of five), so bias
every sub-20B-class prior upward before trusting it. Reasoning benchmarks designed for the GPT-3.5 /
Llama-3-8B era are ceiling-prone for this model; the one measured exception was language transfer
(Swahili), where reasoning strength in English does not carry across the language barrier.

**Rows dated 2026-10-14 are per ENVIRONMENT, not per dataset**: 30 rows, the dataset's shipped
origin prompt (`origin.yaml`), graded under the last-line fallback (`extract_last_bold`),
under `gpt-oss-20b @ low` AND `meta-llama/llama-3.1-8b-instruct`, both at `temperature 0.0`. A
dataset above the bar on the first can be a seat on the second — the L4 pool's cheap tier — and
the verdict names the solver. Floor = the best constant answer on the same 30 rows; a seat needs
the origin inside 15–60% AND a settled gap over it. Sidecars:
`.promptpotter/projects/default/diagnostics/seed-screen-<dataset>[-<solver>]-<date>.json`. Rows dated
2026-10-15 follow the same protocol.

| Dataset | Origin | Verdict |
|---|---|---|
| `gsm_hard` (2026-10-14) | **57%** at the bar · **37%** on the 8B (floor 3%) | ✅ pool seat under both solvers |
| `hotpotqa` distractor (2026-10-14) | **60%** at the bar · **27%** on the 8B (floor 3%) | ✅ pool seat under both solvers; the bar reading sits on the band's top edge |
| `bbh/word_sorting` (2026-10-14) | 73% at the bar · **40%** on the 8B (floor 3%) | ✅ pool seat on the 8B; above the bar |
| `bbh/multistep_arithmetic_two` (2026-10-14) | 100% at the bar · **60%** on the 8B (floor 3%) | ✅ pool seat on the 8B, top edge; saturated at the bar |
| `mgsm/te` (2026-10-14) | 83% at the bar · **57%** on the 8B (floor 3%) | ✅ pool seat on the 8B; `mgsm/sw` 80% / `/bn` 70% / `/th` 73% on the 8B are above band, and all four are above it at the bar (77–97%) |
| `trec` (2026-10-14) | 77% at the bar · **60%** on the 8B (floor 27%) | ✅ held-out bench seat on the 8B — graded, never drawn |
| `bbh/salient_translation_error_detection` (2026-10-14) | **63%** at the bar (floor 30%) | ✅ held-out bench seat at the bar, one SE above the band |
| `bbh-geometric_shapes` (2026-10-14) | 70% at the bar · 30% on the 8B (floor 23%) | ❌ above the bar; on the 8B the gap over the floor is one SE — unsettled, not seated |
| `bbh-logical_deduction_seven_objects` · `bbh-tracking_shuffled_objects_seven_objects` (2026-10-14) | 97% / 93% at the bar · 23% / 17% on the 8B (floors 27% / 23%) | ❌ above the bar; BELOW the constant-answer floor on the 8B |
| `bbh-dyck_languages` · `bbh-temporal_sequences` (2026-10-14) | 80% / 100% at the bar · 0% / — on the 8B | ❌ above the bar; the 8B collapses on dyck (0/30) |
| `ag_news` · `subj` · `aqua_rat` (2026-10-14) | 73% / 93% / 80% at the bar · 67% / 37% / — on the 8B | ❌ above the bar; `subj` on the 8B sits below its 63% floor, `ag_news` on the 8B is above band |
| `sst5` (2026-10-14) | **37%** at the bar (floor 33%) | ❌ in band on accuracy, no settled gap over the constant answer — misses are one step on the scale |
| `arc_challenge` · `instruction-induction` (2026-10-14) | **60%** / **57%** on the 8B (floors 33% / 7%, both settled) · 97% / 80% at the bar | ✅ pool seats on the 8B; above band at the bar |
| `scone` · `counterfactual-arith` · `math-l5` (2026-10-14) | 70% / 7% / 7% on the 8B — `scone` ON its 70% floor (a majority-label read), `math-l5` at 7 s a row · 83% / 83% / 80% at the bar | ❌ no seat on either tier: on the floor or below band on the 8B, above band at the bar |
| `planbench-bw-t1` · `naturalplan-meeting` (2026-10-14) | **33%** / **60%** at the bar (floors 3%, settled) · 0% / 0% on the 8B — invalid plans, not a format miss | ✅ `planbench-bw-t1` pool seat at the bar; `naturalplan-meeting` held-out bench seat at the bar, the band's top edge — the planning family |
| `boardgameqa-d3` · `lichess-puzzles` (2026-10-14) | 77% at the bar under the framed origin (hedge 47%, floor 40%) · 43% on the 8B (floor 40%) / 10% at the bar (floor 3%, unsettled), 0% on the 8B | ❌ `boardgameqa-d3` above the bar and on its floor on the 8B — the recon's collapse did not reproduce under the framed origin; `lichess-puzzles` below band at the bar, the canary, no seat |
| `bigbench/word_unscrambling` · `legalbench-function_of_decision_section` (2026-10-15) | **20%** / **47%** on the 8B (floors 3% / 27%) · **40%** / **47%** at the bar (same floors, settled) | ✅ pool seats under BOTH solvers — the only two of the batch; word_unscrambling costs the 8B 13 s a row |
| `banking77` · `bigbench/operators` · `last-letters` · `wikitq` · `hi-tom` · `medmcqa` (2026-10-15) | **40%** / **57%** / **43%** / **50%** / **47%** / **53%** on the 8B (floors 10 / 13 / 3 / 13 / 13 / 33%) · 77% / 97% / 77% / 77% / 63% / 67% at the bar | ✅ pool seats on the 8B; above band at the bar |
| `tempreason-l1` · `clutrr` (2026-10-15) | 0% / 0% on the 8B (floors 3% / 23%) · 83% / 27% at the bar (`clutrr` unsettled over 23%) | ❌ the 8B zero is a grading-shape miss to peek before any verdict, not a reasoning read; `tempreason-l1` above band at the bar |
| `medqa-usmle4` · `2wikimultihopqa` · `bigbench-matrixshapes` · `knights-knaves-p78` · `knights-knaves-p23` · `stepgame` (2026-10-15) | 70% / 13% (unsettled) / 3% / 3% / 17% (on its floor) / 17% (floor 27%) on the 8B · 87% / 70% / 100% / 80% / 97% / 93% at the bar | ❌ the 8B is above band or at the floor, the bar above band — the bar-vs-8B gap is a capability cliff, not a prompt-shaped one |
| `bigbench-logic_grid_puzzle` · `iris` · `bigbench-presuppositions_as_nli` · `folio` (2026-10-15) | 30% / 27% / 47% / — on the 8B, each under its constant floor (43 / 40 / 50%) · 67% / 93% / 60% (unsettled) / 83% at the bar (floors 43 / 40 / 50 / 53%) | ❌ the constant answer beats or ties the origin on the 8B |
| BBEH on the 8B (2026-10-14) | **0%** (floor 7%) | ❌ floor: the 8B loops to the token cap on hard rows and bolds a wrong answer where it finishes; BBEH stays a `gpt-oss-20b` seat |
| BBEH mini | **28%** | ✅ headline |
| `justlogic/d234` | **0.500–0.625** (floor 0.350) — the recon slice, not the current wiring; re-read off `noise-floor --k 3` on a cold workspace before quoting it | ✅ focus instrument |
| AIME 2025 | **30%** | ✅ wired; too small to split |
| `sealqa/longseal-12` | published no-skill **26.3–33.0%**, ceiling 39.4–44.7% (literature — the bar cannot screen it, being a judge-graded agent episode) | ✅ wired; one harbor episode per cell, graded by the `sealqa` judge |
| PlanBench `task_1` | **36%** | 🟡 next-priority; needs a PDDL plan validator |
| NaturalPlan | **36%** macro | 🟡 next-priority; `meeting_planning`-only (43%) is the clean cut |
| MuSiQue | **60%** macro, **38%** 3hop | 🟡 3hop held; overlaps BBEH's RC subtasks |
| BBEH-mini @ `medium` | **44%** | 🟡 in band, but 1.8–219.9s per sample — operationally unviable |
| BoardgameQA `Main-depth3` | **64%** | 🟡 revisit as a class-collapse-recovery target |
| MMLU-ProX Swahili | **36%** | ❌ in band, wrong axis — L1 would optimize translation tricks |
| JustLogic d≥4 (early cut) | **52%** | ❌ label-bias coast; always-uncertain baseline 44% |
| JustLogic d6–7 | **44%** | ❌ floor — pipeline answers `Uncertain` on ~80% of rows (120b: ~96%, its exact constant floor) |
| PopQA | **44%** | ❌ memorization coast — 10/11 hits were one entity |
| BoardgameQA d2/3 | **60%** | ❌ class-collapse — `disproved` never predicted (0/7) |
| ExploreToM | **68%** | ❌ ceiling |
| AR-LSAT (AGIEval) | **72%** | ❌ saturated — solved directly at 1.8s/sample |
| FOLIO | **80%** | ❌ saturated; reproduced across two providers |
| MuSR | **81%** | ❌ ceiling — B-skewed binary golds, frequency-bias coast |
| SATBench | **100%** | ❌ saturated at every filter the schema exposes |
| GSM8K (2026-10-12) | **90–100%** at the bar, 30 rows — the literature's ~78% was a weaker solver's origin | ❌ saturated for reporting; `gsm_hard` is the seat — § Order of use owns the reading. Kept as a candidate `promptpotter-self` environment, where a near-ceiling seat can still carry signal |
| BBEH @ `high` | ~25% naïve | ❌ the *effort* is rejected, not the dataset — the reasoning trace exhausts the visible-token budget |
| IFBench · CRUXEval-O · MuSR (2-subtask) | — | ❌ desk-rejected / not measurable |

**Rejected without trial** — the list, and the reason beside each name, is owned by
[`../operations/dataset-selection-rationale.md`](../operations/dataset-selection-rationale.md)
§ Rejected without trial; none is re-investigated. The recurring reasons are *above the bar*, *below the floor*, *unstable test set*, and *outside PoBB's
cost model* (tool-call dependent — the budget gate polls at the sample edge, so one
agent episode is unbounded spend between polls). Neither *boundary* is a reason any more: a
containerized agent episode is a measured cell through `connectors/harbor.py`, and an LLM-judge
grade is a banked per-sample observation through `promptpotter/judges/` — so a row rejected for
either reads on the revised criterion 5, not on this list.

## Protocol

**Two reference models, two jobs.** `gpt-oss-20b @ low` is the admission bar and the focus-iteration
model — everything in the roster is scored there. The head-to-head target is held identical across
PromptPotter and every peer and is owned by [`bbeh-comparison/README.md`](bbeh-comparison/README.md).

**Tuning numbers and headline numbers do not mix.** Optimizer-prompt evaluation and ablation tuning
run at 50–100 samples to bound cost; a single 100-sample × 5-variant × 10-round campaign is already
~5,000 backend evaluations, and a sweep multiplies that by the variants under test. Headline numbers
use 200+ samples. Tuning is high-iteration/low-fidelity, reporting is low-iteration/high-fidelity —
**a small-sample tuning number must never appear in a main results table.**

Per-dataset scorers are declared in each `dataset.yaml::campaign.scoring`; the answer-format
contract each one implies is the live string `matchers.py::EXTRACTION_NOTES`, not a doc.

## PEvol-Bench — the AC-grade bench definition (v1 draft)

PromptPotter is an Algorithm Configuration solver in prompt space. What this bench requires — a
canonical split, population-grade size, and non-saturation or procedural generation — is what
separates a credible AC benchmark from a method-comparison harness. **Nothing above is
PEvol-Bench-grade**: the live datasets were chosen for headroom and head-to-head comparability, and
BBEH mini and AIME are too small to split and claim population representativeness.

Definition only; instance assembly TBD. PromptPotter is the reference solver.

- **Framing.** Algorithm Configuration (Hutter et al.) — an algorithm with a configuration space,
  searched for the best config; *per-instance* AC when configs adapt per input. Family: AutoML;
  closest classical relative HPO; in prompt space, Automatic Prompt Optimization.
- **Requirements.** (1) **Pre-assembled canonical split — hard requirement**, else every paper
  compares on slightly different distributions and the field cannot accumulate knowledge;
  (2) DSPy-style compound-system pipeline description; (3) a population large enough for a real
  **config set** / held-out **test set** split.
- **Saturation, raised.** An instance must be unsaturated for the *reference solver's* target model —
  and because that model changes, procedurally generated tasks with an unlimited test set are the
  ideal, not a curated set that ages into saturation.
- **v1 candidates.** **MMLU-Pro** (~12k questions, canonical split) for breadth + **MATH** (7,500 test
  instances, baked-in split) for depth, both HuggingFace-native. Both sit *above* the 20b bar and are
  listed on size and split quality — a PEvol-Bench run implies a stronger reference model than the
  focus instrument uses. **LiveBench** is the contamination-resistant watch item, with the caveat that
  monthly refreshes invalidate cross-cycle comparisons mid-campaign. The **chess puzzles** of
  § Order of use are the other renewable one: a canonical split today, fresh puzzles and an
  adjustable rating band when it saturates.
- **Long-term node-type coverage.** LLM-only: MMLU-Pro, MATH, LiveBench · retrieval+LLM: HotpotQA,
  PopQA, FEVER · multi-step agent: GAIA, τ-bench · code pipeline: SWE-bench · long-context: LongBench,
  FRAMES. Aspiration: ship our own procedurally-generated instances.

## What we report

**Absolute Accuracy** — `correct / total` on test — per (method, model), from `results_*.json`. It is
the raw performance of the best prompt found, and the standard comparison point. Its interval is
paired and clustered — a head-to-head differences the two arms per cell — exact or bootstrapped,
with signal-to-noise per benchmark, never a binomial SE; and the reporting section of the Agentic
Benchmark Checklist gets filled in for this bench and published as an appendix. The source binding
each is [`external-constraints.md`](external-constraints.md) § M14.

Beside it, two of the round counts a cycle banks in `index.json::final`, which say how *fast* rather
than how *high*: **`rounds_to_separable`**, the first round whose pick separates from the origin on the shared panel,
and **`rounds_to_improved`**, the first round that crowned a winner. Quote the first. The second is
a promotion verdict on `lift > 0.0` with no interval and no multiplicity correction
([`../methods/verdict-resolution.md`](../methods/verdict-resolution.md) § ⚠️ The crowning bar), so the
two are routinely far apart and a claim that does not name which one it rests on is not a result.

A query denominator is deliberately absent from all of them. Nearly all of a campaign's wall clock
and spend is backend scoring, and the optimizer's own calls are a few percent of it, so "per
optimization query" prices the cheap part and hides the bill the operator actually pays. Price a
lift in wall clock and dollars. That rules out a *denominator*, not the count: a head-to-head
still holds total calls equal across arms, and reports calls, tokens and dollars (with the price
table's date) as separate axes beside wall clock, with a holdout never used for tuning — the
template [`external-constraints.md`](external-constraints.md) § Ranked, item 4, and § Cost reporting
name.

### The winner's own number is biased upward

Accuracy is read off the **selected** candidate, and selection and estimation must not come from the same rows. Where they do, the reported figure overstates what the prompt will do on deployment: the argmax of noisy means is optimistic, and PoBB compounds it, because elimination stops an arm at a data-dependent time, so the survivor's mean is already biased before a max is taken over it. Being Bayesian is not an exemption — the selected arm's posterior mean still conditions on the selection that chose it. Neither is subset-invariant θ, which corrects for *which samples* were scored rather than *which candidate was chosen*; reaching for θ here is the plausible wrong move.

**Published head-to-head figures are clean** — the split that makes them so is owned by [`bbeh-comparison/README.md`](bbeh-comparison/README.md) § The protocol. **In-campaign figures are not:** the winner's `composite_fitness`, the round banner, the dashboard headline and `export.json`'s fitness are all computed on the rows that selected the winner. The fix is a held-out bench set no optimizer node sees, scored by one evaluator — the contract is [`../architecture.md`](../architecture.md) § The bench score is not an optimizer's selection.

**The bench set is itself spent by reuse.** Every campaign an operator keeps or drops off its headline is one adaptive query against the same rows, so a holdout read by many picks drifts optimistic the same way the search rows did. Every bench pass stamps the count as it is sent (`BenchPass.reads_before`: individuals already graded on those rows), and the campaign's own headline serves its selection's (`BenchStatus.reads_before`) rather than hiding it. There is deliberately no once-only final split: a head-to-head needs every optimizer's pick graded on ONE set, so "read once" either forbids the second arm or has to mean once per comparison — a boundary nothing on disk can tell apart from a fresh campaign on the same seed. A new `dataset_split.seed` is the honest fresh holdout, and the count says when one is due.

Named and corrected for in *Correcting the Winner's Curse in Adaptive Benchmarking* ([arXiv:2605.05973](https://arxiv.org/abs/2605.05973)), whose protocol assumes a fixed shortlist and smooth stabilized selection — the assumption PoBB's adaptive stopping strains, so their estimator needs checking against it before it is adopted. The same data-dependent stopping is why PoBB's ε carries no anytime-valid guarantee ([`external-constraints.md`](external-constraints.md) § Ranked, item 3, which names the form that would carry one).
