# Benchmark Methodology

What we measure on, the bar a dataset passes, and what a number may claim. Model pins and selection
criteria are [`../operations/datasets.md`](../operations/datasets.md)'s; the BBEH head-to-head
protocol is [`../../paper/bbeh-comparison/README.md`](../../paper/bbeh-comparison/README.md) § The
protocol's; peers are [`landscape.md`](landscape.md)'s.

> **Nothing here is a result.** Every origin figure is an admission screen; no head-to-head number is
> published while the peers and PromptPotter do not call the same model. The roster is a dated
> screening log, not a scoreboard.

## The admission bar — `gpt-oss-20b` must not already solve it

Every candidate is screened against **`openai/gpt-oss-20b @ reasoning_effort: low`**, `temperature:
0.0`, 30 rows (`seed-screen`) — the cheapest model a campaign ships on. Rows dated 2026-10-14/15 were
screened under `meta-llama/llama-3.1-8b-instruct` (the L4 pool's cheap tier) as well; the verdict
names the solver. Sidecars:
`.promptpotter/projects/default/diagnostics/seed-screen-<dataset>[-<solver>]-<date>.json`.

- **Saturated** = near ceiling, so every optimizer ties. A saturated dataset is not tested, so **an
  absent dataset is usually saturated, not overlooked**.
- **A low score is not admission either** — near zero has no reachable headroom.
- **Floor** = the best constant answer on the same rows. A seat needs the origin in **15–60%** AND a
  settled gap over the floor ([`../operations/datasets.md`](../operations/datasets.md) § Selection
  criteria, 1b) — screen it before reading any origin.
- Literature anchors from older proxies **underpredict `gpt-oss-20b @ low` by 10–20pp**; bias every
  sub-20B prior upward. Headroom admits a dataset, never ranks it
  ([`external-constraints.md`](external-constraints.md) § L4).

## Order of use

| # | Dataset | Role |
|---|---|---|
| 1 | **BBEH** | Headline; carries the head-to-head. Its 28% was measured at `gpt-oss-20b @ low`, **not the model the dataset runs** — re-measure before quoting. The wrong *iteration* target: at the bar every cycle ties at noise. |
| 2 | **HotpotQA** | `hotpotqa`, distractor validation, `em_match`; MIPROv2 / GEPA / adv-CoT all use it. |
| 3 | **AIME 2025** | 30% at origin, wired; 30 problems, no split. |
| 4 | **Chess puzzles (imec)** | Candidate, **not wired, unmeasured at the bar**: 1,118 Lichess puzzles, 559 / 559 split, exact match on the move ([arXiv 2610.00416](https://arxiv.org/abs/2610.00416), [repo](https://github.com/imec-ailabs/Automatic-Prompt-Optimization-with-Chess), Apache-2.0 / CC0). Six DSPy optimizers published on that split across eight models, `gpt-oss-20b` not among them, so the risk is the floor. |
| — | **`justlogic/d234`** | The **focus instrument** — the optimizer's own behaviour, round over round. |
| — | **`justlogic/d234-held`** | The head-to-head's bench instrument (`justlogic/d234-held/README.md`). |
| — | **`reactome-typeql-42`** | TypeDB's query-writing benchmark under their harness; outside the head-to-head. |
| — | **GSM8K** | **Saturated at the bar** (90–100% on 30 rows under `gpt-oss-20b` and `-120b`); CAPO's 78.1 → 93.7 is a 7B-class solver's home turf. `gsm_hard` is the seat. |

## Every dataset we measured

One row per dataset: origin at the bar / on the 8B (floor), verdict. What a campaign on a seat then
did is the campaign tree's `readout.log` and `evidence`; earlier rows are
`git log -p -- docs/research/benchmarks.md`.

| Dataset | Origin | Verdict |
|---|---|---|
| `gsm_hard` | 57% bar · 37% 8B (floor 3%) | ✅ pool seat under both |
| `hotpotqa` distractor | 60% bar · 27% 8B (floor 3%) | ✅ pool seat under both; bar reading on the band's top edge |
| `hotpotqa-closedbook` | 14% bench | ❌ no paragraphs, no retriever: 82% of misses are missing knowledge; the best literature arm reached 0.21 |
| `hotpotqa-gepa` (GEPA's cut, gold + 8 distractors) | 0.625 (floor 0.100) | ❌ near GEPA's *optimized* 69.00, not its 38.00 baseline: supplied paragraphs remove the retrieval half |
| `logic-fld` (`hitachi-nlp/FLD.v2`) | 60% bar · 55% llama (floor 43%; hedge 68%) | ✅ nearest JustLogic twin, with the hedge-to-UNKNOWN lever |
| `logic-proverqa` (`opendatalab/ProverQA` hard) | 58% bar · 43% llama (floor 43%; hedge 70%) | ✅ candidates move the panel 0.10–0.25 on both |
| `logic-logicnli` (`tasksource/LogicNLI`) | 70% bar · 15% llama | 🟡 top edge at the bar; llama has the headroom — a one-round 20-row lead did not carry to held-out rows |
| `logic-proofwriter` (`tasksource/proofwriter`) | 98% bar · 40% llama | 🟡 in band on llama; ❌ at the bar (mostly depth 0) — screen a depth ≥ 3 cut |
| `bbh/word_sorting` · `instruction-induction` | 73% / 80% bar · 40% / 57% 8B · 0.467 / 0.500 `ministral-8b` | ✅ 8B seats; in band on both 8Bs — the cross-solver transfer pair |
| `bbh/multistep_arithmetic_two` · `mgsm/te` · `arc_challenge` | 100% / 83% / 97% bar · 60% / 57% / 60% 8B | ✅ 8B pool seats (`mgsm/sw`, `/bn`, `/th` above band) |
| `trec` · `bbh/salient_translation_error_detection` | 77% / 63% bar · 60% / — 8B | ✅ held-out bench seats (`trec` on the 8B, salient at the bar) — graded, never drawn |
| `planbench-bw-t1` · `naturalplan-meeting` | 33% / 60% bar (floors 3%) · 0% 8B (invalid plans) | ✅ pool seat / held-out bench seat at the bar |
| `bigbench/word_unscrambling` · `legalbench-function_of_decision_section` | 40% / 47% bar · 20% / 47% 8B | ✅ pool seats under both |
| `banking77` · `bigbench/operators` · `last-letters` · `wikitq` · `hi-tom` · `medmcqa` | 77 / 97 / 77 / 77 / 63 / 67% bar · 40 / 57 / 43 / 50 / 47 / 53% 8B | ✅ 8B pool seats; `banking77` on `gemma-3-4b` 0.51 → 0.59, not separated, misses are neighbour intents |
| `bbh-dyck` | 15–18% `mimo-v2.6-flash` / `mercury-2.5` (floor 13%) | 🟡 ONE-KEY: a stack procedure reaches 65%, potter finds it in round 1 (0.23 → 0.72) — a smoke test, never the main instrument |
| `legalbench-fods` | 53% / 45% mimo / mercury (floor 25%); 30% / 38% on 3–4B | 🟡 nearest MULTI-CLUSTER seat: seven confusable roles |
| Headroom screen, natural origin on mimo / mercury (2026-10-24) | `bbh-multistep-arithmetic`, `bbh-tracking-seven`, `clinc150`, `banking77` (mteb), `legalbench-abercrombie`, `last-letters`, `bbh-word-sorting` | ❌ above band — both solvers reason unprompted |
| BBEH mini | 28% bar · 0% 8B (floor 7%) · 44% `@ medium` · ~25% `@ high` | ✅ headline at the bar; ❌ 8B loops to the cap; 🟡 `medium` 1.8–220 s per sample; ❌ `high` exhausts the visible-token budget |
| `justlogic/d234` | 0.500–0.625 (floor 0.350); re-read off `noise-floor --k 3` before quoting | ✅ focus instrument |
| `justlogic-d234-held` | 0.48–0.51 on 930–1,000 held-out rows | ✅ the instrument potter separates on (below) |
| `sealqa/longseal-12` | literature 26.3–33.0%, ceiling 39.4–44.7% (the bar cannot screen a judge-graded episode) | ✅ wired; one harbor episode per cell |
| PlanBench `task_1` · NaturalPlan · MuSiQue · BoardgameQA `Main-depth3` | 36% · 36% macro · 60% (38% 3hop) · 64% | 🟡 PDDL validator needed · `meeting_planning`-only (43%) is the clean cut · 3hop held · class-collapse-recovery target |
| `bbh-geometric_shapes` · `bbh-logical_deduction_seven` · `bbh-tracking_shuffled_seven` · `bbh-dyck_languages` · `bbh-temporal_sequences` | 70–100% bar · 0–30% 8B, under their floors | ❌ |
| `ag_news` · `subj` · `aqua_rat` · `sst5` · `scone` · `counterfactual-arith` · `math-l5` · `boardgameqa-d3` · `lichess-puzzles` | above the bar, or on the floor on the 8B; `sst5` 37% (floor 33%); `lichess-puzzles` 10% (floor 3%) below band | ❌ |
| `tempreason-l1` · `clutrr` | 83% / 27% bar · 0% 8B | ❌ the 8B zero is a grading-shape miss |
| `medqa-usmle4` · `2wikimultihopqa` · `bigbench-matrixshapes` · `knights-knaves-p78` / `-p23` · `stepgame` · `bigbench-logic_grid_puzzle` · `iris` · `bigbench-presuppositions_as_nli` · `folio` | 60–100% bar · at or under the floor on the 8B | ❌ |
| MMLU-ProX Swahili · JustLogic d≥4 / d6–7 · PopQA · BoardgameQA d2/3 · ExploreToM · AR-LSAT · FOLIO · MuSR · SATBench · GSM8K | 36% · 52% / 44% · 44% · 60% · 68% · 72% · 80% · 81% · 100% · 90–100% | ❌ wrong axis · label-bias coast / `Uncertain` floor · memorization coast · class collapse · ceiling · saturated |
| IFBench · CRUXEval-O · MuSR (2-subtask) | — | ❌ not measurable |

**Rejected without trial** — [`../operations/datasets.md`](../operations/datasets.md) § Rejected
without trial. A containerized agent episode is a measured cell (`connectors/harbor.py`) and an
LLM-judge grade a banked observation (`application/scoring/judges/`), so a *boundary* rejection no
longer holds.

**What the roster decided.**
- Potter's inner optimizer runs `minimax/minimax-m3.1-flash` (jl-optmodel-1, 2026-10-30): held-out
  **+0.073** [+0.001, +0.146] at **2.6 s** a call vs `qwen3.7-flash @ medium` +0.013 at 55 s — chosen
  on speed, the lift not separated (p 0.08); pinned to Together, since `:nitro` served it without
  reasoning. CAPO and LEVI stay on qwen ([`../../paper/notes/model-per-method.md`](../../paper/notes/model-per-method.md)).
- `justlogic-d234-held` is where potter separates: **+0.130** [+0.100, +0.160] (h2h-1007, 1,000 rows);
  paired, Holm: LEVI − potter +0.096 (p < 0.001), GEPA ns, CAPO −0.107.
- `hotpotqa-closedbook` h2h-7 ($0.20 search per arm): potter **+0.050** [+0.007, +0.093], LEVI +0.040
  [−0.016, +0.096], CAPO −0.030.
- L4 inner cells: nothing separated; between-run SD 0.104 per pair, so ±0.05 needs ≈ 17 cells vs the
  5 a round draws; search θ predicts held-out lift weakly (Spearman ≈ 0.3).

## Protocol

**Two reference models, two jobs.** `gpt-oss-20b @ low` is the admission bar and focus-iteration
model; the head-to-head target, identical across every arm, is the bbeh-comparison README's.

**Tuning numbers and headline numbers do not mix.** Tuning runs at 50–100 samples, headlines at
200+; a small-sample tuning number never appears in a main results table.

Per-dataset scorers are `dataset.yaml::campaign.scoring`; each one's answer-format contract is the
live `matchers.py::MATCHERS` `note`.

## PEvol-Bench — the AC-grade bench definition (v1 draft)

PromptPotter is an Algorithm Configuration solver in prompt space (Hutter et al.). A credible AC bench
needs a pre-assembled canonical split, a compound-pipeline description, a population large enough for
a config / test split, and non-saturation for the *reference solver's* target model — procedurally
generated tasks with an unlimited test set are the ideal. **Nothing above is PEvol-Bench-grade.** v1
candidates: **MMLU-Pro** (~12k) for breadth, **MATH** (7,500 test) for depth, both above the 20b bar;
**LiveBench** the contamination-resistant watch item; the chess puzzles renewable.

## What we report

**Absolute accuracy** — `correct / total` on test, per (method, model). Its interval is paired and
clustered (arms differenced per cell, seeds averaged within it: `evidence` serves it as
`HeadToHead.contrasts`, Holm and BH across the matrix), exact or bootstrapped, with SNR per benchmark,
never a binomial SE; the Agentic Benchmark Checklist is filled and published as an appendix
([`external-constraints.md`](external-constraints.md) § The preprint).

Beside it, two round counts from `index.json::final`: **`rounds_to_separable`** (first round whose
pick separates from the origin on the shared panel) — quote this — and **`rounds_to_improved`** (first
round that crowned a winner on `lift > 0.0`, no interval). A claim not naming which it rests on is not
a result.

**No query denominator** — backend scoring is nearly all the wall clock and spend, so "per
optimization query" prices the cheap part. A head-to-head reports calls, tokens, dollars (with the
price table's date) and wall clock, on a holdout never used for tuning
([`external-constraints.md`](external-constraints.md) § Ranked, item 3, and § Cost reporting).

### The winner's own number is biased upward

Selection and estimation must not come from the same rows: the argmax of noisy means is optimistic,
and PoBB's data-dependent stopping compounds it. θ is no exemption — it corrects *which samples* were
scored, not *which candidate was chosen*.

**Published head-to-head figures are clean** (the bbeh-comparison README owns the split). **In-campaign
figures are not:** `composite_fitness`, the round banner, the dashboard headline and `export.json`'s
fitness are read on the selecting rows; the clean number is the held-out bench set no optimizer node
sees ([`../architecture.md`](../architecture.md) § The bench score is not an optimizer's selection).

**The bench set is spent by reuse.** Every campaign kept or dropped off its headline is one adaptive
query against the same rows; each pass stamps the count (`VerificationPass.reads_before`), the headline
serves its selection's (`BenchStatus.reads_before`). There is deliberately no once-only split — a
head-to-head needs every pick graded on ONE set; a new `run_seed` is the fresh holdout, and the count
says when one is due. [arXiv:2605.05973](https://arxiv.org/abs/2605.05973) corrects the curse
assuming a fixed shortlist and smooth selection, which PoBB's stopping strains — check its estimator
before adopting; the same stopping is why ε carries no anytime-valid guarantee
([`external-constraints.md`](external-constraints.md) § Ranked, item 2).
