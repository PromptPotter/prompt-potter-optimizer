# Benchmark Methodology

What we measure on, what it scored, and what we refuse to measure on. Model + `reasoning_effort`
pins → [`../operations/datasets.md`](../operations/datasets.md) § The model a dataset runs.
Selection criteria and the recon detail behind each verdict →
[`../operations/datasets.md`](../operations/datasets.md) § Selection criteria.
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
  [`../operations/datasets.md`](../operations/datasets.md)
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
| `hotpotqa-distractor` (2026-10-23, 40 rows, the bold-final-line origin) | **45%** on the 8B · **55%** `gpt-oss-20b @ low` · **75%** `@ medium` · **57.5%** `mimo-v2.6-flash` · **67.5%** `mercury-2.5` | 🟡 wave 1, single campaigns, not reproduced by the replicates below: 6-round potter campaigns, 100-row bench, 0.49 → **0.58** under a `gpt-6-luna` optimizer and → **0.57** under `claude-haiku-5.5`, **+0.00** under `qwen3.7-flash`; `mimo` 0.72 → 0.77 under `qwen3.7-flash`. None separated at 100 paired rows — the optimizer model is the lever; `gpt-oss-20b @ medium` 0.67 → 0.70 under `qwen3.7-flash`, and `llama-3.1-8b` 0.49 → 0.48 under `deepseek-v4-flash` (both measured at `14e11b5d8`) |
| `hotpotqa-distractor`, wave 2 (2026-10-23, `llama-3.1-8b` target, `max_tokens: 1536` on `llm_only`, code `50f3c80d0`) | **0.52** origin on the 100-row bench | ❌ no arm reached +10 points: `gpt-6-luna` with block packs 0.52 → 0.43 (**−0.09**), `gpt-6-luna` blocks off 0.52 → 0.50 (−0.02), `claude-haiku-5.5` blocks off 0.52 → 0.56 (+0.04). Blocks off beat blocks on for luna by 0.07, one pair of single campaigns at 100 paired rows, so not separated. The dataset file is gitignored: the 1536 output cap is not in this commit |
| `hotpotqa-distractor`, replicates of wave 1's best arm (2026-10-24, `llama-3.1-8b`, `gpt-6-luna` optimizer + block packs, 6 rounds, 100-row bench) | wave 3 at `50f3c80d0`, cap 1536, origin **0.52**: `__df5073` −0.09 · `__9f3bf8` +0.01 · `__6f055c` −0.10. Wave 4 at `14e11b5d8`, no `max_tokens`, fresh archive in a worktree, origin **0.53**: `__8fc359` −0.01 · `__5c862f` +0.00 | 🟡 five replicates after wave 1's +0.09 read −0.04 on average (spread ≈ 0.11), on the old code and the new alike, so wave 1 was one favourable draw and `50f3c80d0` is not a regression. A per-row diff of the origin's wave-1 vs wave-2 bench: 7 of 100 rows flip, 5 up and 2 down, every flip a short `stop` reply of different text at temperature 0 on `llama-3.1-8b:nitro`, and the 8 replies that ran to length miss under both caps. What this does not say: that the setting cannot climb. Luna with block packs, 6 rounds, produced no separable lift on this target; what its proposer saw is the open question |
| `hotpotqa-closedbook` (2026-10-23) | **14%** on the 100-row bench | ❌ does not climb: no paragraphs and no retriever, so no prompt supplies the missing fact — +0.01 to +0.03 over 6 rounds under three optimizer models. On the configs explored, 82% of misses are missing knowledge and 4.5% format |
| `hotpotqa-closedbook`, hand-authored arms from the literature (2026-10-23, authored mode, `submit-arms`) | origin **0.14** on the 100-row bench; **25.0%** on the 40-row origin panel | 🟡 the best closed-book bench so far, single campaigns: E11 (`__b7fcbb`, harvested phrases as round-1 arms) elected C1.12 PREDICT_PHRASE (evolved on gpt-oss-120b), panel 25.0% → 32.5%, bench **0.210 (+0.07)**; next best on the panel STEP_BACK, GENREAD, CANDIDATES, WIKI_TITLE at 30.0%, worst CONTRAPROMPT_SPAN 17.5%. E10 self-ask: panel 20% → 35% (n = 20), bench 0.16 (+0.02). E13 (`resume --campaign hotpotqa-closedbook__b7fcbb --max-rounds 2 --max-usd 1.2` + `submit-arms`, C1.12 stacked with each of seven phrases) elected C2.8 STEP_BACK on round rows 27.5% vs 20.0% (θ +0.632, se 0.390), bench **0.16 (+0.02)**, below C1.12; the panel's best was C2.5 CONTRASTIVE at 35.0%. At 8 arms a round always elects a winner |
| **Headroom screen** (2026-10-24, 40 rows, one pass, a NATURAL origin — the task stated, the answer format, no reasoning scaffold — then one hand-written arm; `mimo-v2.6-flash @ none` / `mercury-2.5`, `max_tokens 1536`) | wanted: natural origin < 40% and a prompt reaching ≥ 80% | Both solvers reason unprompted, so most natural origins already sit high |
| `bbh-multistep-arithmetic` · `bbh-tracking-seven` · `clinc150` (2026-10-24) | **100% / 73% / 93%** on mimo · **90% / 93% / 83%** on mercury | ❌ above band under a natural origin |
| `banking77` (mteb copy) · `legalbench-abercrombie` (2026-10-24) | **78% / 75%** on mimo · **70% / 60%** on mercury; abercrombie with written label definitions 80% / 70% | ❌ above band; definitions buy +0.05 to +0.10 |
| `legalbench-fods` — function_of_decision_section (2026-10-24) | **53%** on mimo · **45%** on mercury (floor 25%); with written definitions of the seven roles **60% / 53%** | 🟡 the nearest MULTI-CLUSTER seat: seven confusable roles, a definition per role lifts each cluster, ceiling unmeasured |
| `last-letters` · `bbh-word-sorting` (2026-10-24) | **55% / 60%** on mimo · **53% / 48%** on mercury; a letter-by-letter arm 73% / 63% · 65% / 50% | ❌ above band, and last-letters is a ONE-KEY task (one instruction fixes every row) |
| Smaller natural targets (2026-10-24, same protocol) | `legalbench-fods` **30%** on `llama-3.2-3b-instruct` · **38%** on `gemma-3-4b-it` (floor 25%, both unsettled; definitions +0.05 / +0.00) · `banking77` **55%** / **53%** (floor 5%, settled) | 🟡 fods on a small solver: in band, near its floor; written definitions moved it +0.05 / +0.00, so a stronger proposal is needed; 🟡 `banking77` on a small solver: 77 confusable intents, a wide margin over its floor, reachability unmeasured |
| `bbh-dyck` — dyck_languages (2026-10-24) | **15%** on mimo · **18%** on mercury (floor 13%); a hand-written stack procedure **65%** on mimo, 10% on mercury | 🟡 in band and reachable, but ONE-KEY: every row is the same algorithm, so a lift is one discovery in one round, and the optimizer model already knows "use a stack" — a smoke test that the loop can find a key, never the main instrument |
| `bbh-dyck`, is the lift potter's? (2026-10-24, mimo target, 100 rows, one pass, `seed-screen <ds> --seeds 0 --n-samples 100 --repeat 1 --model xiaomi/mimo-v2.6-flash`) | origin **0.20** · potter's round-1 pick C1.1 (`bbh-dyck__99d12e`, `gpt-6-luna` optimizer) **0.68** · a stack procedure written once by a chat model with no run evidence **0.55** | 🟡 a chatbot gets 0.35 of potter's 0.48 lift in one shot; the 0.13 between them is ~1.9 SE unpaired, not separated. Both prompts say "use a stack"; C1.1 adds two worked examples and "reverse the remaining stack". CAPO under the same optimizer model and target (`bbh-dyck__c9f090`, paused in round 3) reached only **0.40** vs the origin's 0.15 in round 1 on its 40-row origin panel and held there in round 2, where potter's round 1 read 0.70 vs 0.20. Round 1 at matched spend is the comparison that counts. The CAPO config was not tuned for this task |
| `bbh-dyck`, potter campaigns (2026-10-24, mimo target at effort none, `max_tokens 1536`, 6 rounds, $0.5 cap, 100-row bench, code `c7f78b2bf`, default block pool) | origin **0.23** on the bench | 🟡 smoke test, one replicate per optimizer: `gpt-6-luna` (`bbh-dyck__99d12e`) **0.23 → 0.72**, round 1 · `claude-haiku-5.5` (`bbh-dyck__8a1203`) **0.23 → 0.61**, round 1. Both found the key in the first round and rounds 2–6 added nothing; origin misses were immediate 4–9 token answers plus a few 1536-token repetition loops, so the cap cut no stack trace. Single campaigns, so the luna/haiku gap is not separated |
| `banking77`, potter campaign (2026-10-24, `google/gemma-3-4b-it` target at effort none, `max_tokens 1536`, `gpt-6-luna` optimizer, 6 rounds, $0.5 cap, 100-row bench, code `c7f78b2bf`, default block pool; `banking77__889695`) | origin **0.51** on the bench (52.5% on the 40-row round-0 subset) | 🟡 **0.51 → 0.59** (+0.08), election at round 3; the round subsets read 0.50 → 0.55 → 0.60 → 0.60 → 0.60 and stayed there. One campaign at 100 paired rows, so +0.08 is not separated (it needs ~190 for +0.10), and the +0.30 the dyck smoke test showed has no counterpart here. The logged misses are neighbour intents (`transaction_charged_twice` for `direct_debit_payment_not_recognised`), so the open questions are what evidence the proposer saw about the 77-way confusions and whether a replicate lands in the same place |
| **JustLogic-like screen** (2026-10-24, 40 rows, one pass, natural origin — task stated, labels listed, a bold final line; `llama-3.1-8b:nitro` / `gpt-oss-20b:nitro @ low`, `max_tokens 1536`, `columns` recipe with a `{column}` query template) | wanted: JustLogic's shape — synthetic deduction, balanced labels, the origin in 50–65% on gpt-oss-20b over a settled floor | |
| `hotpotqa-gepa` — GEPA's HotpotQA cut (2026-10-20, distractor validation, 450 search / 300 bench, gold + 8 distractor paragraphs in the prompt, no retriever; `gpt-4.1-mini` target and LEVI refine) | origin **0.625** (`seed-screen hotpotqa-gepa --seeds 0 --n-samples 40 --repeat 3`, spread 0.050, floor 0.100); **0.647** on the bench | ❌ near GEPA's OPTIMIZED 69.00, not its 38.00 baseline: supplying the paragraphs removes the retrieval half. potter 0.647 → 0.667 (+0.020 [−0.013, +0.053]) at $0.99 incurred; LEVI spent its $0.50 inside the calibration round. Of 99 origin misses, 41 are span-boundary (right entity, another span than gold) — under EM only, the headroom is annotator span convention |
| `hotpotqa-closedbook`, head-to-head h2h-3 (2026-10-20, `gpt-oss-20b:nitro @ low`, T 0, $0.20 search per arm, 100-row bench) | origin **0.150** | ❌ LEVI 0.150 → 0.140 (−0.010 [−0.044, +0.024]), 13 INVALID proposals, every one an unclosed `<prompt>`; potter's arm is void — its ruler was calibrated on the race's own parent, so the panel drew C0's known hits (C0 45–60% on every panel against ~21% true) |
| Paired seed-screen, `hotpotqa-distractor` on llama (2026-10-23, 100 rows) | origin **0.53** | a hand-written "span verbatim" `answer_format` read **0.43** |
| `logic-fld` — `hitachi-nlp/FLD.v2` test (Morishita 2023), PROVED / DISPROVED / UNKNOWN with distractor sentences | **55%** on llama · **60%** on gpt-oss-20b (floor 43%, settled; hedge 68%) | ✅ the nearest JustLogic twin: in band, settled over its floor, and the hedge-to-UNKNOWN lever JustLogic has |
| `logic-fld` campaigns (2026-10-24, potter, `gpt-6-luna` optimizer, 4 rounds, uncommitted code beside `c7f78b2bf`) | gpt-oss-20b (`max_tokens 8000`, `logic-fld__253e8e`): bench **0.50 → 0.58 (+0.08)** on 100 held-out rows, round-1 pick; the 20 overlap rows read 0.70 → 0.90 · llama (`max_tokens 1536`, `logic-fld-llama__71cd1a`): nothing promoted in 4 rounds | 🟡 climbs on gpt-oss-20b, short of +0.10 on the bench. On llama the natural origin's "give only the label" keeps every reply a bare label, and no candidate in 4 rounds edited `answer_format` — the proposer prompt warns off that field, so the lever stayed unseen (same in `logic-proverqa__c8b01f`) |
| `logic-proverqa` campaigns (2026-10-24, potter, `gpt-6-luna`, 4 rounds) | llama (`logic-proverqa__c8b01f`): origin 0.30; on the 20 origin-panel rows C0 **0.20**, C1.1 and C3.2 **0.45** — neither elected, each lost its own round's 20 rows by one row · gpt-oss-20b (`logic-proverqa-oss`): origin 0.425; origin panel C0 **0.50**, C1.1 **0.65**, C4.1 **0.60**; C3.3 elected (θ lift +0.72, se 0.49) and C4.1 not (+0.63, se 0.47); bench **0.46 → 0.50 (+0.04)** on 100 held-out rows (`logic-proverqa-oss__57c37c`) | ✅ a good instrument on both targets — candidates move the origin panel by 0.10–0.25. The election reads only the round's own 20 rows, so the panel rows each candidate already paid for never reach it |
| `logic-proverqa` — `opendatalab/ProverQA` `dev/hard.json` (Qi 2025), True / False / Uncertain as A / B / C | **43%** on llama · **58%** on gpt-oss-20b (floor 43%, unsettled; hedge 70%) | 🟡 in band on gpt-oss-20b, hedge-heavy; needs a wider read to settle the margin |
| `logic-logicnli` — `tasksource/LogicNLI` test (Tian 2021), four balanced labels | **15%** on llama · **70%** on gpt-oss-20b (settled) | 🟡 gpt-oss-20b at the band's top edge; ✅ llama starts low with the most headroom of the four — read its misses first (label vocabulary vs reasoning) |
| `logic-logicnli` campaign (2026-10-24, potter, `gpt-6-luna`, 4 rounds, llama, `logic-logicnli__83d7ff`) | C2.2 elected in round 2 (0.30 vs 0.15 on its 20 rows), held in rounds 3–4; round 1's three arms read 0% on 8 rows each; bench **0.25 → 0.20 (−0.05)** on 100 held-out rows | 🔴 the round-2 pick did not carry to held-out rows: a one-round 20-row lead promoted |
| `logic-proofwriter` — `tasksource/proofwriter` test (Tafjord 2021), open world, all depths | **40%** on llama · **98%** on gpt-oss-20b | 🟡 in band on llama; ❌ on gpt-oss-20b, where the mostly-depth-0 test split is easy — a depth ≥ 3 cut is the version to screen there |
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
| `medqa-usmle4` · `2wikimultihopqa` · `bigbench-matrixshapes` · `knights-knaves-p78` · `knights-knaves-p23` · `stepgame` (2026-10-15) | 70% / 13% (unsettled) / 3% / 3% / 17% (on its floor) / 17% (floor 27%) on the 8B · 87% / 70% / 100% / 80% / 97% / 93% at the bar | ❌ the 8B is above band or at the floor, the bar above band |
| `bigbench-logic_grid_puzzle` · `iris` · `bigbench-presuppositions_as_nli` · `folio` (2026-10-15) | 30% / 27% / 47% / — on the 8B, each under its constant floor (43 / 40 / 50%) · 67% / 93% / 60% (unsettled) / 83% at the bar (floors 43 / 40 / 50 / 53%) | ❌ the constant answer beats or ties the origin on the 8B |
| `mistralai/ministral-8b-2512` as a second 8B solver (2026-10-14, 30 rows, one pass, origin / floor) | in band: `word_sorting` 0.467 / 0.033 · `instruction-induction` 0.500 / 0.067 · `counterfactual-arith` 0.567 / 0.067; band edge `hotpotqa` 0.600; above: `multistep` 0.733 · `gsm_hard` 0.700 · `mgsm-te` 0.800 · `trec` 0.900 · `arc` 0.933 · `math-l5` 0.633 at 27 s a row | 🟡 `word_sorting` and `instruction-induction` are in band on both 8B solvers — the pair that tests whether a lift transfers across solvers; dyck and planbench unmeasured (the key ran out) |
| L4 inner cells, `promptpotter-self-pool` round 0 (2026-10-14; B = held-out accuracy of the cell's standing selection minus the origin's on the same 20 rows; point estimates, ±0.1 at n = 20) | `__28d33b`: bbeh/20b −0.050 · word_sorting/8b +0.200 · gsm_hard/20b +0.050 · gsm_hard/8b −0.025 · hotpotqa/20b +0.050 · hotpotqa/8b +0.200 · bbeh-s1/20b +0.025 · multistep/8b +0.250 · mgsm-te/8b +0.050; held-out trec/8b +0.300 · salient/8b +0.025. `__c794fd`: bbeh/20b +0.000 · word_sorting/8b +0.250 · gsm_hard/20b +0.000 · gsm_hard/8b +0.000 · hotpotqa/20b +0.000 · hotpotqa/8b −0.050 · bbeh-s1/20b +0.000 · multistep/8b +0.100 · arc/8b −0.025 · instruction-induction/8b −0.250 · planbench-bw-t1/20b −0.100 | 🟡 nothing separated; the 8B seats carry the lift (+0.135 vs +0.019 on the 20b in `__28d33b`, +0.075 vs +0.000 in `__c794fd`). Between-run noise on the 8 shared seats: SD 0.104 per pair, ≈ 0.074 per cell, so a paired arm difference reaches ±0.05 at ≈ 17 cells against the 5 a round draws. The search pool's θ lift predicts B weakly (Spearman ≈ 0.3 over 11). The overfits the held-out read exists for: instruction-induction/8b panel 33.3% → 50.0% while B fell to −0.250; bbeh/20b θ +0.574 while its held-out rows fell 0.100 → 0.050. A cell ran 4.7–10.9 min at $0.02–0.06 |
| L4 outer arms (2026-10-13 → 14; cell fitness `(mean_round_delta + 1) / 3`, one inner row moves it ~0.1) | `__96acac` authored rounds 1–4 on five bbeh/aime cells: origin 0.436, critique edit 0.448, generator edit 0.464 (elected, θ +0.083, se 0.368), both 0.410; rounds 2–4 held or tied. `__28d33b` rounds 1–2: a 727c `l1_critique.thinking_style` rewrite paired −0.045 over 5 cells, a 2,725c `l1_generate.instruction` rewrite −0.075; both held, bench 0.662 → 0.662 | ❌ no outer arm separated. `__96acac` billed $2.34 over 4 rounds; the combined arm sat below both its parts, which only a cell's step size explains. Rounds 2–3 were spend-axis arms (`reasoning_effort`, `max_tokens`), reclassified as spend, not findings, when those axes were pinned |
| BBEH on the 8B (2026-10-14) | **0%** (floor 7%) | ❌ floor: the 8B loops to the token cap on hard rows and bolds a wrong answer where it finishes; BBEH stays a `gpt-oss-20b` seat |
| BBEH mini | **28%** | ✅ headline |
| `justlogic/d234` | **0.500–0.625** (floor 0.350) — the recon slice, not the current wiring; re-read off `noise-floor --k 3` on a cold workspace before quoting it | ✅ focus instrument |
| `justlogic-d234-held`, head-to-head `h2h-1007` (2026-10-07, four controlled arms, one seed, prompt axes only, every optimizer call on `openai/gpt-6-luna`, $0.30 search per arm, no round ceiling; pre-reshape code `a91abdf3f`) | origin **0.479** on 1,000 held-out rows | ✅ the first four-arm head-to-head graded end to end (M13's closing bar): LEVI **0.705**, potter **0.609** (+0.130 [+0.100, +0.160]), GEPA **0.591**, CAPO **0.502**. Paired on the same rows, Holm: LEVI − potter +0.096 (p < 0.001), GEPA − potter −0.018 (ns), CAPO − potter −0.107 (p < 0.001). $1.82 billed in all. LEVI's winner was one rewritten instruction carrying a paraphrase lexicon and a rule against defaulting to "Uncertain"; few-shot was not the lever |
| `justlogic-d234-held`, optimizer-model screen E12 (2026-10-23, potter, 6 rounds, $0.60 cap per arm, after the election's discordance check moved from `objective` to the verdict) | origin **0.496** on 930 held-out rows | ✅ HEAD climbs again: `gpt-6-luna @ medium` 0.496 → **0.614** (+0.118, composite +0.078, C6.1, rounds 1 and 6 promoted) · `qwen3.7-flash @ medium` (`__b38045`) 0.496 → **0.600** (+0.104, composite +0.066, round 4). Before the fix (`__e8e655`) luna held rounds 1–3 though C3.1 read 50.0% vs the parent's 11.8% (14 verdict wins, 1 loss): a cost composite gave misses ~0.19 by length, so the discordance read 14W/20L and refused. The luna lift matches `h2h-1007`'s pre-reshape +0.130 |
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
[`../operations/datasets.md`](../operations/datasets.md)
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
contract each one implies is the live string `matchers.py::MATCHERS`' `note`, not a doc.

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

**The bench set is itself spent by reuse.** Every campaign an operator keeps or drops off its headline is one adaptive query against the same rows, so a holdout read by many picks drifts optimistic the same way the search rows did. Every bench pass stamps the count as it is sent (`BenchPass.reads_before`: individuals already graded on those rows), and the campaign's own headline serves its selection's (`BenchStatus.reads_before`) rather than hiding it. There is deliberately no once-only final split: a head-to-head needs every optimizer's pick graded on ONE set, so "read once" either forbids the second arm or has to mean once per comparison — a boundary nothing on disk can tell apart from a fresh campaign on the same seed. A new `run_seed` is the honest fresh holdout, and the count says when one is due.

Named and corrected for in *Correcting the Winner's Curse in Adaptive Benchmarking* ([arXiv:2605.05973](https://arxiv.org/abs/2605.05973)), whose protocol assumes a fixed shortlist and smooth stabilized selection — the assumption PoBB's adaptive stopping strains, so their estimator needs checking against it before it is adopted. The same data-dependent stopping is why PoBB's ε carries no anytime-valid guarantee ([`external-constraints.md`](external-constraints.md) § Ranked, item 3, which names the form that would carry one).
