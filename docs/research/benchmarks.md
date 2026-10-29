# Benchmark Methodology

What we measure on, what it scored, and what we refuse to measure on. Model + `reasoning_effort` pins
and the selection criteria behind each verdict are [`../operations/datasets.md`](../operations/datasets.md)'s
(§ The model a dataset runs, § Selection criteria); the BBEH head-to-head protocol is
[`../../paper/bbeh-comparison/README.md`](../../paper/bbeh-comparison/README.md)'s; peers, [`related-work.md`](related-work.md)'s.

> **Nothing here is a result — a gate, not an omission.** Every origin figure is an admission screen:
> what an *origin* scored, deciding whether a dataset is worth wiring. No head-to-head number is
> published while the optimizer is tuned for measurable lift and the peers and PromptPotter do not yet
> call the same model ([`../../paper/bbeh-comparison/README.md`](../../paper/bbeh-comparison/README.md) owns both blockers).
> Read the roster as a dated screening log, not a scoreboard.

## The admission bar — `gpt-oss-20b` must not already solve it

Every candidate is screened against **`openai/gpt-oss-20b @ reasoning_effort: low`** — the cheapest
model we would ship a campaign on, weak enough that a prompt still has work to do.

**Saturated** = near ceiling, so no lift can exceed the measurement's error bar and every optimizer
ties. A saturated dataset is **not tested at all**, so **an absent dataset is usually saturated, not
overlooked**; the roster records what each scored, making the omission checkable.

- **A low score is not admission either** — near-zero has no reachable headroom and ties just as hard.
- **The constant-answer floor is invisible to the bar** —
  [`../operations/datasets.md`](../operations/datasets.md) § Selection criteria, criterion 1b. Screen it
  before reading any origin below.

## Order of use

| # | Dataset | Role |
|---|---|---|
| 1 | **BBEH** | Headline benchmark — carries the head-to-head. Its 28% was measured at `gpt-oss-20b @ low`, **not the model the dataset runs** ([`../../paper/bbeh-comparison/README.md`](../../paper/bbeh-comparison/README.md) § The protocol); re-measure before quoting. |
| 2 | **HotpotQA** | Wired 2026-10-14 (`hotpotqa`, distractor validation, `em_match`), seated in the L4 pool under both solvers. MIPROv2 / GEPA / adv-CoT all use it. |
| 3 | **AIME 2025** | In band at 30%, wired; limited by size (30 problems, no split). |
| 4 | **Chess puzzles (imec)** | Candidate, **not wired, unmeasured at the bar**. 1,118 Lichess puzzles, published 559 / 559 dev / test split stratified by length (1–5 moves) and rating (400–2,800), exact-match on the move — [arXiv 2610.00416](https://arxiv.org/abs/2610.00416), [`imec-ailabs/Automatic-Prompt-Optimization-with-Chess`](https://github.com/imec-ailabs/Automatic-Prompt-Optimization-with-Chess) (Apache-2.0; Lichess export CC0). Six DSPy optimizers (GEPA, MIPROv2, SIMBA, COPRO, BootstrapFewShot, BootstrapRandomSearch) already published on that split across eight models (~$800), so one more arm lands beside existing rows. Before wiring: `gpt-oss-20b` is not in their panel, so the bar risk is the **floor** (a rating cut moves it); a multi-move puzzle counts only when every move is right. Abstract, setup, takeaways read 2026-10-11; tables not. |
| — | **`justlogic/d234`** | Not a publication benchmark — the **focus instrument**, where the optimizer's own behaviour is measured round over round. |
| — | **`justlogic/d234-held`** | The head-to-head's bench instrument (`justlogic/d234-held/README.md`). |
| — | **`reactome-typeql-42`** | TypeDB's query-writing benchmark under their own harness (`reactome-typeql-42/README.md`): TypeQL executed and its result graded. Outside the head-to-head — compared against TypeDB's published rows on the operator's model. |
| — | **GSM8K** | **Saturated at the bar 2026-10-12**: 90–100% at origin on 30 rows under `gpt-oss-20b` and `-120b` (`promptpotter-self/pool/dataset.yaml::panel`); the literature's 78% was a weaker solver's origin. CAPO's 78.1 → 93.7 makes it the opponent's home turf — a head-to-head there belongs on an in-band (7B-class) solver, never ours. `gsm_hard` is the seat. |

BBEH is the headline but the wrong *iteration* target: at the bar every cycle ties at noise, hence the
separate focus role, which collapses into the headline once L1 produces measurable lift on BBEH.

## Every dataset we measured

Defaults unless noted: **`gpt-oss-20b @ low`, `temperature: 0.0`**, 25-sample slice. Literature anchors
from older proxies **underpredict `gpt-oss-20b @ low` by 10–20pp** (five recons of five) — bias every
sub-20B prior upward. GPT-3.5 / Llama-3-8B-era reasoning benchmarks are ceiling-prone here; the one
measured exception was language transfer (Swahili).

**Rows dated 2026-10-14/15 are per ENVIRONMENT**: 30 rows, the shipped `origin.yaml`, graded under the
last-line fallback (`extract_last_bold`), under `gpt-oss-20b @ low` AND `meta-llama/llama-3.1-8b-instruct`
(the L4 pool's cheap tier), both at T 0; the verdict names the solver. Floor = best constant answer on
the same rows; a seat needs the origin in 15–60% AND a settled gap over it. Sidecars:
`.promptpotter/projects/default/diagnostics/seed-screen-<dataset>[-<solver>]-<date>.json`.

| Dataset | Origin | Verdict |
|---|---|---|
| `gsm_hard` (2026-10-14) | **57%** at the bar · **37%** on the 8B (floor 3%) | ✅ pool seat under both solvers |
| `hotpotqa` distractor (2026-10-14) | **60%** at the bar · **27%** on the 8B (floor 3%) | ✅ pool seat under both; the bar reading on the band's top edge |
| `hotpotqa-distractor` (2026-10-23, 40 rows, bold-final-line origin) | **45%** 8B · **55%** `gpt-oss-20b @ low` · **75%** `@ medium` · **57.5%** `mimo-v2.6-flash` · **67.5%** `mercury-2.5` | 🟡 wave 1, single 6-round potter campaigns, 100-row bench, not reproduced below: 0.49 → **0.58** (`gpt-6-luna` optimizer), → **0.57** (`claude-haiku-5.5`), **+0.00** (`qwen3.7-flash`); `mimo` 0.72 → 0.77 (`qwen3.7-flash`); `gpt-oss-20b @ medium` 0.67 → 0.70; `llama-3.1-8b` 0.49 → 0.48 (`deepseek-v4-flash`) (last two at `14e11b5d8`). None separated at 100 paired rows |
| `hotpotqa-distractor`, wave 2 (2026-10-23, `llama-3.1-8b`, `max_tokens: 1536` on `llm_only`, `50f3c80d0`) | **0.52** on the 100-row bench | ❌ no arm reached +10: luna + block packs **−0.09**, luna blocks off −0.02, haiku blocks off +0.04 — not separated. The 1536 cap lives in the gitignored dataset file |
| `hotpotqa-distractor`, replicates of wave 1's best arm (2026-10-24, llama, luna + block packs, 6 rounds, 100-row bench) | wave 3 (`50f3c80d0`, cap 1536, origin **0.52**): `__df5073` −0.09 · `__9f3bf8` +0.01 · `__6f055c` −0.10. Wave 4 (`14e11b5d8`, no cap, fresh archive, origin **0.53**): `__8fc359` −0.01 · `__5c862f` +0.00 | 🟡 five replicates average −0.04 (spread ≈ 0.11): wave 1's +0.09 was one favourable draw and `50f3c80d0` is no regression. Origin wave-1 vs wave-2 bench: 7 of 100 rows flip (5 up, 2 down), each a short `stop` reply of different text at T 0 on `llama-3.1-8b:nitro`. Not shown: that the setting cannot climb — what the proposer saw is the open question |
| `hotpotqa-closedbook` (2026-10-23) | **14%** on the 100-row bench | ❌ no paragraphs, no retriever: +0.01 to +0.03 over 6 rounds under three optimizer models; 82% of misses are missing knowledge, 4.5% format |
| `hotpotqa-closedbook`, hand-authored arms from the literature (2026-10-23, `submit-arms`) | origin **0.14** bench; **25.0%** on the 40-row origin panel | 🟡 best closed-book bench so far, single campaigns: E11 (`__b7fcbb`) elected C1.12 PREDICT_PHRASE, panel 25.0% → 32.5%, bench **0.210 (+0.07)**; STEP_BACK, GENREAD, CANDIDATES, WIKI_TITLE 30.0%, CONTRAPROMPT_SPAN 17.5%. E10 self-ask: panel 20% → 35% (n = 20), bench 0.16. E13 (`resume --campaign hotpotqa-closedbook__b7fcbb --max-rounds 2 --max-usd 1.2` + `submit-arms`, C1.12 × seven phrases) elected C2.8 STEP_BACK (27.5% vs 20.0%, θ +0.632, se 0.390), bench **0.16**, below C1.12; panel best C2.5 CONTRASTIVE 35.0%. At 8 arms a round always elects a winner |
| **Headroom screen** (2026-10-24, 40 rows, one pass, NATURAL origin — task, answer format, no scaffold — then one hand-written arm; `mimo-v2.6-flash @ none` / `mercury-2.5`, `max_tokens 1536`) | wanted: natural origin < 40% and a prompt reaching ≥ 80% | Both solvers reason unprompted, so most natural origins sit high |
| `bbh-multistep-arithmetic` · `bbh-tracking-seven` · `clinc150` (2026-10-24) | **100% / 73% / 93%** mimo · **90% / 93% / 83%** mercury | ❌ above band |
| `banking77` (mteb) · `legalbench-abercrombie` (2026-10-24) | **78% / 75%** mimo · **70% / 60%** mercury; abercrombie with label definitions 80% / 70% | ❌ above band; definitions +0.05 to +0.10 |
| `legalbench-fods` (2026-10-24) | **53%** mimo · **45%** mercury (floor 25%); with role definitions **60% / 53%** | 🟡 nearest MULTI-CLUSTER seat: seven confusable roles, ceiling unmeasured |
| `last-letters` · `bbh-word-sorting` (2026-10-24) | **55% / 60%** mimo · **53% / 48%** mercury; letter-by-letter arm 73% / 63% · 65% / 50% | ❌ above band; last-letters is ONE-KEY |
| Smaller natural targets (2026-10-24) | `legalbench-fods` **30%** `llama-3.2-3b-instruct` · **38%** `gemma-3-4b-it` (floor 25%, unsettled; definitions +0.05 / +0.00) · `banking77` **55%** / **53%** (floor 5%, settled) | 🟡 fods in band near its floor, needs a stronger proposal; 🟡 `banking77`: 77 confusable intents, wide margin, reachability unmeasured |
| `bbh-dyck` (2026-10-24) | **15%** mimo · **18%** mercury (floor 13%); hand-written stack procedure **65%** mimo, 10% mercury | 🟡 in band, reachable, ONE-KEY — a smoke test that the loop finds a key, never the main instrument |
| `bbh-dyck`, is the lift potter's? (2026-10-24, mimo, 100 rows, `seed-screen <ds> --seeds 0 --n-samples 100 --repeat 1 --model xiaomi/mimo-v2.6-flash`) | origin **0.20** · potter's round-1 C1.1 (`bbh-dyck__99d12e`, luna) **0.68** · a one-shot chat-model stack procedure **0.55** | 🟡 a chatbot gets 0.35 of potter's 0.48 lift; the 0.13 gap is ~1.9 SE unpaired. C1.1 adds two worked examples and "reverse the remaining stack". CAPO, same optimizer and target (`bbh-dyck__c9f090`, paused round 3, untuned): **0.40** vs origin 0.15 in round 1 on its 40-row panel, held in round 2, where potter's round 1 read 0.70 vs 0.20. Round 1 at matched spend is the comparison that counts |
| `bbh-dyck`, potter campaigns (2026-10-24, mimo @ none, `max_tokens 1536`, 6 rounds, $0.5 cap, 100-row bench, `c7f78b2bf`) | origin **0.23** | 🟡 one replicate each: luna (`__99d12e`) **0.23 → 0.72**, haiku (`__8a1203`) **0.23 → 0.61**, both in round 1, rounds 2–6 nothing; the cap cut no stack trace. Gap not separated |
| `banking77`, potter (2026-10-24, `google/gemma-3-4b-it` @ none, `max_tokens 1536`, luna, 6 rounds, $0.5, 100-row bench, `c7f78b2bf`; `banking77__889695`) | origin **0.51** (52.5% on the 40-row round-0 subset) | 🟡 **0.51 → 0.59** (+0.08), elected round 3, subsets 0.50 → 0.55 → 0.60 flat. Not separated (+0.10 needs ~190 rows). Misses are neighbour intents (`transaction_charged_twice` for `direct_debit_payment_not_recognised`): open — what the proposer saw about the 77-way confusions, and whether a replicate lands there |
| **JustLogic-like screen** (2026-10-24, 40 rows, one pass, natural origin with labels and a bold final line; `llama-3.1-8b:nitro` / `gpt-oss-20b:nitro @ low`, `max_tokens 1536`, `columns` recipe with a `{column}` query template) | wanted: synthetic deduction, balanced labels, origin 50–65% on gpt-oss-20b over a settled floor | |
| `hotpotqa-gepa` — GEPA's cut (2026-10-20, 450 search / 300 bench, gold + 8 distractor paragraphs, no retriever; `gpt-4.1-mini` target and LEVI refine) | origin **0.625** (`seed-screen hotpotqa-gepa --seeds 0 --n-samples 40 --repeat 3`, spread 0.050, floor 0.100); **0.647** bench | ❌ near GEPA's OPTIMIZED 69.00, not its 38.00 baseline: supplied paragraphs remove the retrieval half. potter +0.020 [−0.013, +0.053] at $0.99; LEVI spent its $0.50 in calibration. 41 of 99 origin misses are span-boundary — under EM the headroom is annotator span convention |
| `hotpotqa-closedbook`, h2h-3 (2026-10-20, `gpt-oss-20b:nitro @ low`, T 0, $0.20 search per arm, 100-row bench) | origin **0.150** | ❌ LEVI −0.010 [−0.044, +0.024], 13 INVALID proposals (unclosed `<prompt>`); potter's arm void — its ruler was calibrated on the race's own parent, so the panel drew C0's known hits (45–60% on every panel vs ~21% true) |
| Paired seed-screen, `hotpotqa-distractor` on llama (2026-10-23, 100 rows) | origin **0.53** | a hand-written "span verbatim" `answer_format` read **0.43** |
| `logic-fld` — `hitachi-nlp/FLD.v2` test (Morishita 2023), PROVED / DISPROVED / UNKNOWN with distractors | **55%** llama · **60%** gpt-oss-20b (floor 43%, settled; hedge 68%) | ✅ nearest JustLogic twin, with the hedge-to-UNKNOWN lever |
| `logic-fld` campaigns (2026-10-24, potter, luna, 4 rounds, uncommitted code beside `c7f78b2bf`) | gpt-oss-20b (`max_tokens 8000`, `logic-fld__253e8e`): bench **0.50 → 0.58** on 100 held-out rows, round-1 pick; 20 overlap rows 0.70 → 0.90 · llama (`max_tokens 1536`, `logic-fld-llama__71cd1a`): nothing promoted | 🟡 climbs on gpt-oss-20b, short of +0.10. On llama "give only the label" keeps every reply bare and no candidate edited `answer_format` — the proposer prompt warns off that field (same in `logic-proverqa__c8b01f`) |
| `logic-proverqa` campaigns (2026-10-24, potter, luna, 4 rounds) | llama (`__c8b01f`): origin 0.30; origin panel C0 **0.20**, C1.1 and C3.2 **0.45**, neither elected (each lost its round's 20 rows by one) · gpt-oss-20b (`logic-proverqa-oss`): origin 0.425; panel C0 **0.50**, C1.1 **0.65**, C4.1 **0.60**; C3.3 elected (θ +0.72, se 0.49), C4.1 not (+0.63, se 0.47); bench **0.46 → 0.50** (`__57c37c`) | ✅ good instrument on both — candidates move the panel 0.10–0.25. The election read only the round's own 20 rows |
| `logic-proverqa` — `opendatalab/ProverQA` `dev/hard.json` (Qi 2025), True / False / Uncertain as A / B / C | **43%** llama · **58%** gpt-oss-20b (floor 43%, unsettled; hedge 70%) | 🟡 in band on gpt-oss-20b, hedge-heavy; needs a wider read |
| `logic-logicnli` — `tasksource/LogicNLI` test (Tian 2021), four balanced labels | **15%** llama · **70%** gpt-oss-20b (settled) | 🟡 gpt-oss-20b at the top edge; ✅ llama has the most headroom — read its misses first (label vocabulary vs reasoning) |
| `logic-logicnli` campaign (2026-10-24, potter, luna, 4 rounds, llama, `__83d7ff`) | C2.2 elected round 2 (0.30 vs 0.15 on 20 rows), held 3–4; round 1's arms 0% on 8 rows each; bench **0.25 → 0.20** | 🔴 a one-round 20-row lead promoted and did not carry to held-out rows |
| `logic-proofwriter` — `tasksource/proofwriter` test (Tafjord 2021), open world, all depths | **40%** llama · **98%** gpt-oss-20b | 🟡 in band on llama; ❌ on gpt-oss-20b (mostly depth 0) — screen a depth ≥ 3 cut there |
| `bbh/word_sorting` (2026-10-14) | 73% at the bar · **40%** 8B (floor 3%) | ✅ pool seat on the 8B |
| `bbh/multistep_arithmetic_two` (2026-10-14) | 100% at the bar · **60%** 8B (floor 3%) | ✅ pool seat on the 8B, top edge |
| `mgsm/te` (2026-10-14) | 83% at the bar · **57%** 8B (floor 3%) | ✅ pool seat on the 8B; `mgsm/sw` 80% / `/bn` 70% / `/th` 73% on the 8B above band; all four 77–97% at the bar |
| `trec` (2026-10-14) | 77% at the bar · **60%** 8B (floor 27%) | ✅ held-out bench seat on the 8B — graded, never drawn |
| `bbh/salient_translation_error_detection` (2026-10-14) | **63%** at the bar (floor 30%) | ✅ held-out bench seat at the bar |
| `bbh-geometric_shapes` (2026-10-14) | 70% at the bar · 30% 8B (floor 23%) | ❌ above the bar; one SE over the floor on the 8B |
| `bbh-logical_deduction_seven_objects` · `bbh-tracking_shuffled_objects_seven_objects` (2026-10-14) | 97% / 93% at the bar · 23% / 17% 8B (floors 27% / 23%) | ❌ above the bar; BELOW the floor on the 8B |
| `bbh-dyck_languages` · `bbh-temporal_sequences` (2026-10-14) | 80% / 100% at the bar · 0% / — 8B | ❌ above the bar; the 8B collapses on dyck (0/30) |
| `ag_news` · `subj` · `aqua_rat` (2026-10-14) | 73% / 93% / 80% at the bar · 67% / 37% / — 8B | ❌ above the bar; `subj` 8B below its 63% floor, `ag_news` 8B above band |
| `sst5` (2026-10-14) | **37%** at the bar (floor 33%) | ❌ no settled gap over the constant answer |
| `arc_challenge` · `instruction-induction` (2026-10-14) | **60%** / **57%** 8B (floors 33% / 7%, settled) · 97% / 80% at the bar | ✅ pool seats on the 8B |
| `scone` · `counterfactual-arith` · `math-l5` (2026-10-14) | 70% / 7% / 7% 8B (`scone` ON its 70% floor; `math-l5` 7 s a row) · 83% / 83% / 80% at the bar | ❌ no seat on either tier |
| `planbench-bw-t1` · `naturalplan-meeting` (2026-10-14) | **33%** / **60%** at the bar (floors 3%, settled) · 0% / 0% 8B (invalid plans) | ✅ `planbench-bw-t1` pool seat at the bar; `naturalplan-meeting` held-out bench seat at the bar, top edge |
| `boardgameqa-d3` · `lichess-puzzles` (2026-10-14) | 77% at the bar framed (hedge 47%, floor 40%) · 43% 8B (floor 40%) / 10% at the bar (floor 3%, unsettled), 0% 8B | ❌ `boardgameqa-d3` above the bar, on its floor on the 8B; `lichess-puzzles` below band — the canary |
| `bigbench/word_unscrambling` · `legalbench-function_of_decision_section` (2026-10-15) | **20%** / **47%** 8B (floors 3% / 27%) · **40%** / **47%** at the bar (settled) | ✅ pool seats under BOTH solvers; word_unscrambling 13 s a row on the 8B |
| `banking77` · `bigbench/operators` · `last-letters` · `wikitq` · `hi-tom` · `medmcqa` (2026-10-15) | **40 / 57 / 43 / 50 / 47 / 53%** 8B (floors 10 / 13 / 3 / 13 / 13 / 33%) · 77 / 97 / 77 / 77 / 63 / 67% at the bar | ✅ pool seats on the 8B |
| `tempreason-l1` · `clutrr` (2026-10-15) | 0% / 0% 8B (floors 3% / 23%) · 83% / 27% at the bar (`clutrr` unsettled) | ❌ the 8B zero is a grading-shape miss to peek first |
| `medqa-usmle4` · `2wikimultihopqa` · `bigbench-matrixshapes` · `knights-knaves-p78` · `knights-knaves-p23` · `stepgame` (2026-10-15) | 70 / 13 (unsettled) / 3 / 3 / 17 (on its floor) / 17% (floor 27%) 8B · 87 / 70 / 100 / 80 / 97 / 93% at the bar | ❌ |
| `bigbench-logic_grid_puzzle` · `iris` · `bigbench-presuppositions_as_nli` · `folio` (2026-10-15) | 30 / 27 / 47 / —% 8B, each under its floor (43 / 40 / 50%) · 67 / 93 / 60 (unsettled) / 83% at the bar (floors 43 / 40 / 50 / 53%) | ❌ the constant answer beats or ties the origin on the 8B |
| `mistralai/ministral-8b-2512` as a second 8B (2026-10-14, 30 rows, origin / floor) | in band: `word_sorting` 0.467 / 0.033 · `instruction-induction` 0.500 / 0.067 · `counterfactual-arith` 0.567 / 0.067; edge `hotpotqa` 0.600; above: `multistep` 0.733 · `gsm_hard` 0.700 · `mgsm-te` 0.800 · `trec` 0.900 · `arc` 0.933 · `math-l5` 0.633 (27 s a row) | 🟡 `word_sorting` and `instruction-induction` in band on both 8Bs — the cross-solver transfer pair |
| L4 inner cells, `promptpotter-self-pool` round 0 (2026-10-14; B = held-out accuracy of the cell's selection minus the origin's on the same 20 rows; ±0.1 at n = 20) | `__28d33b`: bbeh/20b −0.050 · word_sorting/8b +0.200 · gsm_hard/20b +0.050 · gsm_hard/8b −0.025 · hotpotqa/20b +0.050 · hotpotqa/8b +0.200 · bbeh-s1/20b +0.025 · multistep/8b +0.250 · mgsm-te/8b +0.050; held-out trec/8b +0.300 · salient/8b +0.025. `__c794fd`: bbeh/20b +0.000 · word_sorting/8b +0.250 · gsm_hard/20b +0.000 · gsm_hard/8b +0.000 · hotpotqa/20b +0.000 · hotpotqa/8b −0.050 · bbeh-s1/20b +0.000 · multistep/8b +0.100 · arc/8b −0.025 · instruction-induction/8b −0.250 · planbench-bw-t1/20b −0.100 | 🟡 nothing separated; the 8B seats carry the lift (+0.135 vs +0.019 on the 20b in `__28d33b`, +0.075 vs +0.000 in `__c794fd`). Between-run SD on the 8 shared seats 0.104 per pair (≈ 0.074 per cell): ±0.05 needs ≈ 17 cells vs the 5 a round draws. Search θ lift predicts B weakly (Spearman ≈ 0.3 over 11). Overfits: instruction-induction/8b panel 33.3% → 50.0% while B fell to −0.250; bbeh/20b θ +0.574 while held-out fell 0.100 → 0.050. A cell: 4.7–10.9 min, $0.02–0.06 |
| L4 outer arms (2026-10-13 → 14; cell fitness `(mean_round_delta + 1) / 3`, one inner row ~0.1) | `__96acac` authored rounds 1–4 on five bbeh/aime cells: origin 0.436, critique edit 0.448, generator edit 0.464 (elected, θ +0.083, se 0.368), both 0.410. `__28d33b` rounds 1–2: a 727c `l1_critique.thinking_style` rewrite −0.045 over 5 cells, a 2,725c `l1_generate.instruction` rewrite −0.075; bench 0.662 → 0.662 | ❌ no outer arm separated. `__96acac` $2.34 over 4 rounds; the combined arm below both parts, explained only by a cell's step size. Rounds 2–3 were spend-axis arms (`reasoning_effort`, `max_tokens`), reclassified as spend when those axes were pinned |
| BBEH on the 8B (2026-10-14) | **0%** (floor 7%) | ❌ floor: loops to the token cap; BBEH stays a `gpt-oss-20b` seat |
| BBEH mini | **28%** | ✅ headline |
| `justlogic/d234` | **0.500–0.625** (floor 0.350) — the recon slice; re-read off `noise-floor --k 3` on a cold workspace before quoting | ✅ focus instrument |
| `justlogic-d234-held`, head-to-head `h2h-1007` (2026-10-07, four controlled arms, one seed, prompt axes only, every optimizer call on `openai/gpt-6-luna`, $0.30 search per arm, no round ceiling; pre-reshape `a91abdf3f`) | origin **0.479** on 1,000 held-out rows | ✅ first four-arm head-to-head graded end to end (M13's closing bar): LEVI **0.705**, potter **0.609** (+0.130 [+0.100, +0.160]), GEPA **0.591**, CAPO **0.502**. Paired, Holm: LEVI − potter +0.096 (p < 0.001), GEPA − potter −0.018 (ns), CAPO − potter −0.107 (p < 0.001). $1.82 in all. LEVI's winner: one rewritten instruction carrying a paraphrase lexicon and a rule against defaulting to "Uncertain"; few-shot was not the lever |
| `justlogic-d234-held`, optimizer-model screen E12 (2026-10-23, potter, 6 rounds, $0.60 cap per arm, after the discordance check moved from `objective` to the verdict) | origin **0.496** on 930 held-out rows | ✅ climbs again: `gpt-6-luna @ medium` → **0.614** (+0.118, composite +0.078, C6.1) · `qwen3.7-flash @ medium` (`__b38045`) → **0.600** (+0.104, round 4). Before the fix (`__e8e655`) luna held rounds 1–3 though C3.1 read 50.0% vs 11.8% (14 verdict wins, 1 loss): a cost composite gave misses ~0.19 by length, so discordance read 14W/20L and refused. Matches `h2h-1007`'s +0.130 |
| AIME 2025 | **30%** | ✅ wired; too small to split |
| `sealqa/longseal-12` | published no-skill **26.3–33.0%**, ceiling 39.4–44.7% (literature — the bar cannot screen a judge-graded agent episode) | ✅ wired; one harbor episode per cell, `sealqa` judge |
| PlanBench `task_1` | **36%** | 🟡 needs a PDDL plan validator |
| NaturalPlan | **36%** macro | 🟡 `meeting_planning`-only (43%) is the clean cut |
| MuSiQue | **60%** macro, **38%** 3hop | 🟡 3hop held; overlaps BBEH's RC subtasks |
| BBEH-mini @ `medium` | **44%** | 🟡 in band, but 1.8–219.9 s per sample — operationally unviable |
| BoardgameQA `Main-depth3` | **64%** | 🟡 a class-collapse-recovery target |
| MMLU-ProX Swahili | **36%** | ❌ wrong axis — L1 would optimize translation tricks |
| JustLogic d≥4 (early cut) | **52%** | ❌ label-bias coast; always-uncertain 44% |
| JustLogic d6–7 | **44%** | ❌ floor — `Uncertain` on ~80% of rows (120b ~96%, its exact constant floor) |
| PopQA | **44%** | ❌ memorization coast — 10/11 hits one entity |
| BoardgameQA d2/3 | **60%** | ❌ class-collapse — `disproved` never predicted (0/7) |
| ExploreToM | **68%** | ❌ ceiling |
| AR-LSAT (AGIEval) | **72%** | ❌ saturated — solved directly at 1.8 s/sample |
| FOLIO | **80%** | ❌ saturated across two providers |
| MuSR | **81%** | ❌ ceiling — B-skewed binary golds |
| SATBench | **100%** | ❌ saturated at every filter |
| GSM8K (2026-10-12) | **90–100%** at the bar, 30 rows | ❌ saturated for reporting (§ Order of use); kept as a candidate `promptpotter-self` environment |
| BBEH @ `high` | ~25% naïve | ❌ the *effort* is rejected — the trace exhausts the visible-token budget |
| IFBench · CRUXEval-O · MuSR (2-subtask) | — | ❌ desk-rejected / not measurable |

**Rejected without trial** — names and reasons are [`../operations/datasets.md`](../operations/datasets.md)
§ Rejected without trial's; none is re-investigated. Neither *boundary* reason holds any more: a
containerized agent episode is a measured cell (`connectors/harbor.py`) and an LLM-judge grade a banked
observation (`promptpotter/application/scoring/judges/`), so such a row reads on revised criterion 5.

## Protocol

**Two reference models, two jobs.** `gpt-oss-20b @ low` is the admission bar and focus-iteration model;
the head-to-head target, identical across PromptPotter and every peer, is
[`../../paper/bbeh-comparison/README.md`](../../paper/bbeh-comparison/README.md)'s.

**Tuning numbers and headline numbers do not mix.** Tuning runs at 50–100 samples (one 100-sample ×
5-variant × 10-round campaign is ~5,000 backend evaluations); headlines use 200+. **A small-sample tuning
number never appears in a main results table.**

Per-dataset scorers are `dataset.yaml::campaign.scoring`; each one's answer-format contract is the live
`matchers.py::MATCHERS` `note`.

## PEvol-Bench — the AC-grade bench definition (v1 draft)

PromptPotter is an Algorithm Configuration solver in prompt space. A credible AC benchmark needs a
canonical split, population-grade size, and non-saturation or procedural generation. **Nothing above is
PEvol-Bench-grade** — chosen for headroom and comparability; BBEH mini and AIME are too small to split.
Definition only; PromptPotter is the reference solver.

- **Framing.** Algorithm Configuration (Hutter et al.); *per-instance* AC when configs adapt per input.
  Family AutoML; classical relative HPO; in prompt space, Automatic Prompt Optimization.
- **Requirements.** (1) **A pre-assembled canonical split — hard**, else papers compare on drifting
  distributions; (2) a DSPy-style compound-pipeline description; (3) a population large enough for a
  real **config set** / held-out **test set** split.
- **Saturation, raised.** Unsaturated for the *reference solver's* target model, which changes — so
  procedurally generated tasks with an unlimited test set are the ideal.
- **v1 candidates.** **MMLU-Pro** (~12k, canonical split) for breadth + **MATH** (7,500 test, baked-in
  split) for depth — both above the 20b bar, so a PEvol-Bench run implies a stronger reference model.
  **LiveBench** is the contamination-resistant watch item (monthly refreshes break cross-cycle
  comparison). The **chess puzzles** (§ Order of use) are renewable too.
- **Long-term node-type coverage.** LLM-only: MMLU-Pro, MATH, LiveBench · retrieval+LLM: HotpotQA,
  PopQA, FEVER · multi-step agent: GAIA, τ-bench · code: SWE-bench · long-context: LongBench, FRAMES.
  Aspiration: our own procedurally-generated instances.

## What we report

**Absolute Accuracy** — `correct / total` on test, per (method, model), from `results_*.json`. Its
interval is paired and clustered (arms differenced per cell, seeds averaged within it: `evidence`
serves it as `HeadToHead.contrasts`, Holm and BH across the matrix), exact or bootstrapped, with SNR per
benchmark, never a binomial SE; the Agentic Benchmark Checklist's reporting section is filled and
published as an appendix ([`external-constraints.md`](external-constraints.md) § M14).

Beside it, two round counts from `index.json::final`: **`rounds_to_separable`** (first round whose pick
separates from the origin on the shared panel) — quote this — and **`rounds_to_improved`** (first round
that crowned a winner, on `lift > 0.0` with no interval or correction:
[`../specs/code-debt-cleanup.md`](../specs/code-debt-cleanup.md) § Open — multi-arc). A claim
not naming which it rests on is not a result.

**No query denominator.** Backend scoring is nearly all the wall clock and spend, so "per optimization
query" prices the cheap part. Price a lift in wall clock and dollars. A head-to-head still holds total
calls equal and reports calls, tokens and dollars (with the price table's date) beside wall clock, with
a holdout never used for tuning ([`external-constraints.md`](external-constraints.md) § Ranked, item 4,
and § Cost reporting).

### The winner's own number is biased upward

Selection and estimation must not come from the same rows: the argmax of noisy means is optimistic, and
PoBB compounds it (data-dependent stopping biases the survivor's mean before the max). Being Bayesian
is no exemption, nor is θ — it corrects *which samples* were scored, not *which candidate was chosen*;
reaching for θ here is the plausible wrong move.

**Published head-to-head figures are clean** ([`../../paper/bbeh-comparison/README.md`](../../paper/bbeh-comparison/README.md)
§ The protocol owns the split). **In-campaign figures are not:** `composite_fitness`, the round banner,
the dashboard headline and `export.json`'s fitness are all read on the selecting rows. The fix is a
held-out bench set no optimizer node sees, one evaluator — [`../architecture.md`](../architecture.md)
§ The bench score is not an optimizer's selection.

**The bench set is itself spent by reuse.** Every campaign kept or dropped off its headline is one
adaptive query against the same rows. Every bench pass stamps the count as sent
(`BenchPass.reads_before`), and the headline serves its selection's (`BenchStatus.reads_before`). There
is deliberately no once-only split: a head-to-head needs every pick graded on ONE set. A new `run_seed`
is the honest fresh holdout, and the count says when one is due.

Named and corrected in *Correcting the Winner's Curse in Adaptive Benchmarking*
([arXiv:2605.05973](https://arxiv.org/abs/2605.05973)), assuming a fixed shortlist and smooth
stabilized selection — which PoBB's adaptive stopping strains, so check their estimator against it
before adopting. The same stopping is why ε carries no anytime-valid guarantee
([`external-constraints.md`](external-constraints.md) § Ranked, item 3).
