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
0.0`, 30 rows of origin passes — the cheapest model a campaign ships on. Rows dated 2026-10-14/15 were
screened under `meta-llama/llama-3.1-8b-instruct` (the L4 pool's cheap tier) as well; the verdict
names the solver.

- **Saturated** = near ceiling, so every optimizer ties. A saturated dataset is not tested, so **an
  absent dataset is usually saturated, not overlooked**.
- **A low score is not admission either** — near zero has no reachable headroom.
- **The band — an origin with room to climb above its floor — is an approximate, unproven estimate: a lower origin is better, but one at 60–70% still
  leaves room to climb** (operator, 2026-11-10).
- **Floor** = the best constant answer on the same rows. A seat needs a settled gap over the floor
  ([`../operations/datasets.md`](../operations/datasets.md) § Selection criteria, 1b); the origin
  round serves it, paired (`domain/scoring.py::constant_answer`).
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
| `justlogic/d234` | 0.500–0.625 (floor 0.350); re-read off a noise floor (`origin_replicates: 3`) before quoting | ✅ focus instrument |
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
without trial. An agent episode is a measured cell and an LLM-judge grade a banked observation, so a
*boundary* rejection there no longer holds.

**What the roster decided.**
- Potter's inner optimizer runs `minimax/minimax-m3.1-flash` (jl-optmodel-1, 2026-10-30): held-out
  **+0.073** [+0.001, +0.146] at **2.6 s** a call vs `qwen3.7-flash @ medium` +0.013 at 55 s — chosen
  on speed, the lift not separated (p 0.08); pinned to Together, since `:nitro` served it without
  reasoning. Every head-to-head arm runs it: one model, each peer's swap from its paper's a declared
  deviation ([`../../paper/notes/model-per-method.md`](../../paper/notes/model-per-method.md) holds the case against).
- `justlogic-d234-held` is where potter separates: **+0.130** [+0.100, +0.160] (h2h-1007, 1,000 rows);
  paired, Holm: LEVI − potter +0.096 (p < 0.001), GEPA ns, CAPO −0.107.
- `hotpotqa-closedbook` h2h-7 ($0.20 search per arm): potter **+0.050** [+0.007, +0.093], LEVI +0.040
  [−0.016, +0.096], CAPO −0.030.
- L4 inner cells: nothing separated; between-run SD 0.104 per pair, so ±0.05 needs ≈ 17 cells vs the
  5 a round draws; search θ predicts held-out lift weakly (Spearman ≈ 0.3).
- L4 panel C0 (`promptpotter-self/panel`, now `dev`, f589cbe9a, 2026-11-14, $0.24 billed): `composite_fitness`
  **0.533** over six cells, each cell's held-out lift on 20 rows: 0 on hotpotqa-distractor ×2 and
  justlogic-d234 ×2, +5 and +15 pts on legalbench-fods; in three legalbench-fods rounds L1 proposed
  only no-op variants. Re-measured on a3312e3e1 (cycle_524b6b54dcd5, $0.276 billed, $0.313
  incurred): **0.546** over six cells, held-out lift 0 / +2.5 on justlogic-d234, +10 / +15 on
  legalbench-fods, 0 / 0 on hotpotqa-distractor (mean +0.046); search θ +0.142. Re-measured on
  dcbb0f66e (cycle_d34546396419, $0.198 billed, $0.234 incurred): **0.518** over five of six cells,
  halted at the origin gate; held-out lift 0 / −1.3 on justlogic-d234, −5 on legalbench-fods, 0 / +15
  on hotpotqa-distractor (mean +0.017); θ +0.052. The sixth cell (legalbench-fods seed 1) elected in
  two rounds and was excluded (roadmap § Optimizer tuning — the standing board, item 4), so this
  reading covers a different cell set from the two above and is not comparable.
- L4 `run` C0 (`promptpotter-self.run__265c4d`, e8d97499f, 2026-11-18, rounds 0–2, $2.06 billed):
  **the inner search delivers no held-out lift, so no optimizer prompt can be ranked on it.** Of 45
  inner runs (sandbox `inner_978bb669adfac9d8`, a fork counted once), 25 adopted; an adoption's
  20-row bench delta is **+0.004** (SD 0.107, SE 0.021; 10 above the origin, 11 below, 4 equal),
  and the SD is the 20-row bench's own sampling noise at ~23% discordant rows. Every arm's per-run
  mean sits in [−0.050, +0.050]; round 1's best, +0.200 ± 0.443 logits, cleared nothing. The inner
  gate adopts at 1.1–2.0 SE (`won on θ lift +0.343 logits, se 0.250`), the best of up to 12 looks
  (3 variants × 4 rounds), so on these seats a winner is a draw. A no-adoption seat scores 0.50 and the
  origin panel reads 0.49: on this measurand the optimizer equals one that never adopts.
- L4 known-sign check (`promptpotter-self.dyck__2bd880`, cycle_4ec5c80c24a4, bfd0d772b, 2026-11-18,
  `arm_gate: author`, $0.768 billed): two hand-written `l1_generate` arms on the `bbh-dyck` seats, a
  positive arm (stack procedures) and a crippled control (L1 may only reword). Bar fixed with only
  s2 read: mean(positive − crippled) raw held-out lift (the inner 20-row bench; a clip at fit 1.00
  counts +50) ≥ +10 pts AND ≥ 4 seats > 0. **PASS, n=5: +19.5, 4 of 5** (s1 +12.5, s2 +23.8,
  s3 +32.5, s4 0.0, s5 +28.8; s0, dead on the per-round ruler, apart). Two cells were re-asked after
  infra losses (the gate collision; a 504 that cut the inner run) under a rule fixed before the last
  cell landed; the re-asked positive s5 elected its inner rounds 2–4 on ruler v1125, every other
  round-1 cell on v1087 (the resume re-opened the round on the live ruler; cloud `l4-valid`).
  Without s5: +17.2, n=4, 3 of 4. Across rounds, on different rulers, the s1–s5 mean lift reads
  origin 0.30 (round 0, v1052), positive 0.29, crippled 0.09; the election HELD (positive θ −0.078
  ± 0.609 vs the origin). The instrument reads a broken optimizer prompt's sign on a one-key smoke
  seat; the positive arm does not beat the origin.
- Inner headroom probes (2026-11-18, one hand-written target arm per dataset, written before any row
  was read, inside potter's 800-char `length_budget`, `arm_gate: author`, 100 paired rows):
  `hotpotqa-distractor__3b0221` (shortest-span arm) **40% vs 40%** on search, θ +0.075 ± 0.339; 55 of
  its 130 distinct misses are sentence or cut-off replies, which `answer_format` (never L1's) governs.
  `legalbench-fods__985e9c` (label-definition arm) **40% vs 30%** on search (θ +1.376 ± 0.458,
  selected) but **49% vs 49%** on 100 held-out rows (`held-out`): 10 rows won exactly where aimed
  (Decree/Conclusion 5, Facts/Procedural History 3), 10 lost elsewhere. The draw was blind
  (`selection.py::crowning_cells` is empty under an origin parent); the search split read harder
  (origin 39% on 164 rows, 30% on the arm's 100) than the bench (49%). The edit won ~20% of the
  parent's misses on both splits and lost 10% of its hits on search, 20% on the bench: pooled over
  200 paired rows 23 wins / 13 losses, ≈ +5 ± 3 pts. Each probe is one arm (a lower bound on
  headroom); hotpotqa was not graded held-out; justlogic is unprobed. A well-aimed edit's ~5 pts sits
  under the 20-row inner bench's ~10-pt SD: these seats offer too little reachable headroom for
  the bench to read, before any optimizer prompt is compared.
- Seat screens, same method (2026-11-18 evening, `--max-rounds 1`, `sp_budget_round=100`, one arm in
  `<scratch>/screen/<ds>.yaml`): `logic-fld__0aba75` (`gpt-oss-20b@low`; two-direction derivation
  arm) parent **63% vs 57%** on 100 paired search rows, θ +0.010 ± 0.555, $0.026 billed: this arm
  found no lift (one arm, a lower bound). `banking77__1101ce` (`gemma-3-4b`; object-then-state
  arm) parent **52%** on 100; the arm is unread: it was cut `broken` at row 18 (9 of 18 hit) by ONE
  empty answer, which `results_health.py` grades fatal on one sighting (cloud `small-6`). Prose
  replies instead of a label come from both prompts (the parent's too), a 4B format miss; $0.005.
  Re-read on `small-6` (`banking77__6cae77`, 2975f7225, the empty answer graded a miss): arm
  **55% vs parent 50%** on 100 paired search rows (+5.0 pts raw), θ lift −0.407 ± 0.727 logits
  (θ leaves rows out), not elected; $0.008, 17% replayed. A reachable gain of ~5 pts under its own
  noise: not a mid-band seat on this solver. `logic-logicnli__e7b7f5` (`llama-3.1-8b-instruct`;
  2975f7225) origin 22% on 40, parent **15% vs arm 6%** on 100 paired, θ lift −0.865 ± 1.441, both
  under the 30% constant-answer floor (always `contradiction`); most misses are prose, not a label,
  which `answer_format` owns: disqualified on this solver, $0.035. `logic-proverqa__c4ea6f`
  (`llama-3.1-8b-instruct`; forward-chaining both-directions arm) origin 30% on 40, parent **42% vs
  arm 24%** on 100 paired search rows (−18 pts, θ −1.03 ± 0.78, eliminated `p_best` 4.6%), $0.014.
  Split per candidate (`<scratch>/screen/split.py` over each ledger's `sample_scored`), a label-shaped
  reply's accuracy apart from the share that lands a label: proverqa's −18 is the token cap (34% of
  the arm's replies cut at 1,536, parent 0%), equal 43% vs 43% among labels; its headroom is the
  uncertain class (36% of rows, the parent hits 2 of 36). logicnli's 8B is cap-bound on both prompts
  (cut 36% parent, 68% arm; 28% vs 19% among labels). banking77 lands labels alike (88% / 87%) and
  the arm's +6 holds among them (57% → 63%). logic-fld lands every label: its −6 is reasoning.
  proverqa round 2 (a decision-rule arm written after that split, reasoning bounded to five lines):
  **26% vs parent 31%** on a fresh 100 (θ −0.18 ± 0.61), 32% vs 32% among labels; it moved answers
  from false to uncertain (uncertain hits 1 → 4 of 40, false 18 → 5 of 35): the 8B cannot tell the
  two apart, and a prompt shifts the boundary, not the discrimination. The parent read 42% and 31%
  on two 100-row draws. Not a seat on this solver; $0.024. A
  seat where a reasoning edit runs into the cap punishes potter's own variants too, so a screen
  reads the label-shaped share per candidate before the seat is crowned.

## Protocol

**Two reference models, two jobs.** `gpt-oss-20b @ low` is the admission bar and focus-iteration
model; the head-to-head target, identical across every arm, is the bbeh-comparison README's.

**Tuning numbers and headline numbers do not mix.** Tuning runs at 50–100 samples, headlines at
200+; a small-sample tuning number never appears in a main results table.

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
query against the same rows; each pass stamps the count and the headline serves its selection's. There is deliberately no once-only split — a
head-to-head needs every pick graded on ONE set; a new `run_seed` is the fresh holdout, and the count
says when one is due. [arXiv:2605.05973](https://arxiv.org/abs/2605.05973) corrects the curse
assuming a fixed shortlist and smooth selection, which PoBB's stopping strains — check its estimator
before adopting; the same stopping is why ε carries no anytime-valid guarantee
([`external-constraints.md`](external-constraints.md) § Ranked, item 2).
