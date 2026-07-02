# Dataset Selection Rationale — Meta-Campaign Signal Density

> **How to add a dataset:** [`adding-a-dataset.md`](adding-a-dataset.md) — research the canonical train/test split *before* writing the loader. This doc is *which* datasets and *why*; the add-doc is *the process for wiring one*.

Parallel to [`dataset-reasoning-matrix.md`](dataset-reasoning-matrix.md) (per-dataset model defaults). This doc captures *which* datasets we trial during L1 meta-prompt evolution and *why* — same operator-driven format, evidence + verdicts + dates.

## Frame — BBEH is the headline; meta-campaign needs signal

**BBEH stays the headline benchmark** for publication framing. Nothing about the candidate list below changes that.

**But** at the optimizer's current maturity, BBEH is the wrong *iteration* target for L1 meta-prompt evolution. `gpt-oss-20b @ low` scores in the floor band on BBEH (~14% public, see [BBEH score anomaly](../../README.md) and `project_bbeh_score_anomaly.md`) — every cycle ties at noise, PoBB can't separate candidates, and the L1 meta-campaign can't tell good edits from bad ones.

Framing the operator gave (2026-05-18): *we are too far from the local valley on BBEH; we have to work our way to it with more tractable signal first — improve L1 meta-prompts (and other optimizer pieces) on datasets where lift is measurable, then return to BBEH as headline with better hyperparameters and a more mature optimizer.*

## Selection criteria — L1-meta-campaign focus datasets

A focus dataset for L1 meta-prompt evolution must satisfy:

1. **Origin in band.** `gpt-oss-20b @ low` scores **15–40%** at origin. Below 15% → floor effect (BBEH problem). Above 40% → ceiling effect (no headroom for L1 lift to register against PoBB noise). *Band widened from 15-35% after the 7-dataset recon trail 2026-05-19 — see "Selection trail Round 6" for the systemic finding on projection slop.*
2. **Reachable ceiling.** Plausible **50–75%** under strong prompt engineering. The origin-to-ceiling gap is what L1 climbs; bigger gap = cleaner signal/noise.
3. **N ≥ 400, preferably 800+.** For stable cycle-to-cycle verdicts under PoBB (ε=0.05–0.10, n_min=4–6). Smaller N usable with per-subtask stratification.
4. **Multiple distinct subtask categories.** Each subtask is an independent L1 prompt lever (decomposition, scaffolding, role-priming, anti-shortcut framing, format pinning). Single-axis datasets give L1 only one knob to turn.
5. **Deterministic per-sample grading.** Exact match, MC, F1, regex extraction. **No LLM-judge scoring** (breaks PoBB's per-candidate independence model; cost-prohibitive at cycle scale).
6. **No mode-collapse on a single gold class.** A dataset where the modal answer covers >40% of the gold set, or where the model collapses 3-class to 2-class (BoardgameQA's missing `disproved`), will be label-bias coasted and produce inflated origin. Stratify by `prop` / `category` / `subtask` to spot the trap before commit.
7. **HF-loadable.** Single jsonl on Hugging Face = trivial loader. Custom scraper = real plumbing cost.
8. **Contamination-resistant.** 2024+ release preferred. Synthetic generation a plus.
9. **Anchored by measurement, not projection.** A 25-sample recon on `gpt-oss-20b @ low` via Groq is the verdict — projections from Llama-3-8B / T5-XL / GPT-3.5 anchors systematically underpredict `gpt-oss-20b`'s reasoning strength. See Round 6 below.

## Headline ≠ focus

| Role | Dataset(s) | Why |
|---|---|---|
| **Headline benchmark** (publication) | BBEH | Hardest reasoning benchmark, established competitor comparison, public leaderboards. |
| **Meta-campaign focus** (L1/L2/L3 iteration) | **JustLogic depth ≥ 6** (`justlogic`) | Wired 2026-05-19 after 9-dataset recon trail. 400/1000 cut on depths 6-7 only. See "Wired — primary" below. BBEH-mini @ `low` held as secondary in-band candidate. **Next-priority queue (Round 8, 2026-05-19)**: **PlanBench task_1** (36%, PDDL planning — brand-new family) and **NaturalPlan** (36% macro; `meeting_planning`-only at 43% is the clean cut) — both diversify into planning, no overlap with current portfolio. |
| **Connector validation** | TermNorm (lca-termnorm) | Per-connector regression, not optimizer iteration. |

When the optimizer matures enough that L1 prompts produce measurable lift on BBEH, the focus role collapses back into the headline role. Until then, they are separate jobs.

## Why `gpt-oss-20b @ reasoning_effort: low` (operator commitment 2026-05-19)

Operator-pinned model for the meta-campaign focus. Justification:
- **Leading open-source** at the 20B-active scale.
- **Fast on Groq** routing (845 tok/s on `:nitro`-eligible providers; ~5s median per call observed).
- **Very cheap** — $0.03 in / $0.14 out per Mtok. A full-cycle eval on the 420-sample MMLU-ProX-sw train pool with 5 candidates × 6 rounds ≈ 12.6k calls fits well inside Groq's daily volume.
- **Conservative-floor at `reasoning_effort: low`** per `promptpotter/CLAUDE.md` — optimizer climbs from the floor. `medium` / `high` are L1-reachable mutations when sibling-yield supports.

Pinning is via `nodes.llm_only.config` overlay in each dataset's `pipeline.json`, not in `optimizer.param_keys` — L1 cannot propose `model` or `provider` mutations (operator-locked axes per `PARAM_FORBIDDEN_KEYS`).

## Wired — primary

| Dataset | HF path | Cut | Measured origin | Latency | Class behavior | L1 attack surface |
|---|---|---|---|---|---|---|
| **JustLogic depth ≥ 6** | `michaelchenkj/JustLogic` (single `train` split, 4,900 rows) | Operator-defined: depths 6+7 only, 200/depth train (400) + 500/depth test (1,000). Authors' canonical test set is withheld; HF `train` IS the public training fold. See `datasets/justlogic/dataset.md`. | **44% (11/25)** on 25-sample d≥6 recon, OpenRouter `:nitro` | **~0.3s/sample** — very fast, very cheap | 3-class `TRUE`/`FALSE`/`Uncertain`, all used (no class-collapse). Hedge bias: ~17/25 preds = `Uncertain`; per-depth gold distribution is balanced ~33% each → hedge bias is real reasoning failure, not label-skew coast. | **Break the hedge**: prompt mutations saying "commit to TRUE/FALSE when premises strictly determine the conclusion; reserve `Uncertain` for genuine indeterminacy." Synthetic generation = zero contamination. Random baseline 33.3%; human avg 73%; ceiling per o1-preview 81% (paper). |

**Wire commit 2026-05-19**: `datasets/justlogic/` + `load_justlogic(split=...)` shipped. `pipeline.json` pins `openai/gpt-oss-20b:nitro` @ `low` via OpenRouter (matches recon conditions exactly).

## Next-priority after JustLogic (Round 8, 2026-05-19)

Two new in-band candidates from the colleague-triage recon (NaturalPlan + MuSiQue + AR-LSAT + PlanBench, 4 candidates) — operator decision: **hold both, wire after JustLogic delivers its first cycle**, treat as the "30-40% experiments" queue. Both genuinely diversify L1 attack surface (planning, not deduction or math).

| Dataset | HF / source | Slice | Effort | Measured origin | Latency | L1 attack surface |
|---|---|---|---|---|---|---|
| **PlanBench task_1** | `tasksource/planbench`, config `task_1_plan_generation` (2,270 rows) | Multi-domain stratified (~5/domain across blocksworld + logistics + 3 obfuscated variants), 25 samples | low | **36% (9/25)** | **1.5s/sample** | PDDL-style symbolic planning. **Brand-new family** for the portfolio (no overlap with deduction / math / multi-hop QA). Obfuscated-domain variants (`paltry`, `sip`, `wretched` as action names) deliberately test reasoning vs pattern-matching — high-value subset for L1 prompt mutation that *forces* PDDL-shaped reasoning. Recon scorer is coarse 50% action-call overlap; **wire-time needs a PDDL plan validator** (~half-day work) for credible per-cycle scoring. |
| **NaturalPlan** | `google-deepmind/natural-plan` raw GitHub (NOT on HF Hub — colleague misremembered) | 3-subtask stratified (`trip_planning` + `calendar_scheduling` + `meeting_planning`), ~9/subtask, 25 samples | low | **36% (9/25)** macro — Frankenstein avg | **0.5s/sample** | Multi-waypoint constraint planning. **Per-subtask breakdown reveals the trap**: `trip_planning` 0/9 (real floor at low, combinatorial flight-chain search), `calendar_scheduling` 6/9 (ceiling at this slice; needs harder filter — more participants, tighter windows), `meeting_planning` 3/7 = 43% (clean in-band, real constraint problem). Macro 36% is misleading — L1 would game calendar's short boilerplate golds and never make progress on trip. **Cleanest wire is `meeting_planning`-only at 43%.** Scorer needs per-subtask dispatch: day+time-slot exact match for calendar, joined-list token overlap for meeting, token overlap for trip. |

**Why these two specifically**:

- **AR-LSAT was the third candidate** (`hails/agieval-lsat-ar`, 230 test rows, 5-option MC). Recon: **72% (18/25) CEILING at 1.8s/sample**. `gpt-oss-20b @ low` solves AGIEval LSAT analytical-reasoning puzzles directly — no headroom for L1 lift. Surprising vs literature projection (smaller models 30-50%); the AGIEval cut may be easier than full LSAT-AR, OR the model is genuinely strong on this format. **Rejected for meta-campaign focus**; could become a "verify-improvements-don't-regress" probe at wire-time. See `recon_arlsat` in the recon script for the bulletproof MC parsing — reusable for any future LSAT-style MC dataset.
- **MuSiQue macro ceiling** but per-hop reveals structure: 2hop = 89%, 3hop = 38%, 4hop = 57% (with several "misses" being partial-string mismatches very close to gold). **3hop-only at 38% is the clean in-band cut** — held as secondary if MuSiQue family becomes the L1 surface, but lower priority than PlanBench + NaturalPlan because multi-hop QA overlaps with BBEH's reading-comprehension subtasks (lower marginal-diversity value).

**Held subtask cuts** (lower-priority than the macro candidates above; revisit only if PlanBench + NaturalPlan don't pan out):

- **NaturalPlan `meeting_planning`-only** — 43% on 7 samples, single coherent constraint family, scorer is token-overlap on joined waypoint list.
- **MuSiQue `3hop`-only** — 38% on 8 samples, substring scorer (with `answer_aliases`) is clean, ~6,000 rows for the 3hop split alone.

## In-band candidates held for later

- **BBEH-mini @ `low`** (28%, 1.1s median latency): boardgame_qa subtask class-collapses to `unknown` at `low`, resolves at `medium`. Class-collapse is itself a research-grade target — PromptPotter L1's prompt-mutation surface may be **exactly anti-built against this** ("commit to all three classes; do not default to the safest hedge"). A future campaign could trial BBEH-mini + BoardgameQA as a *class-collapse-recovery* benchmark — measure whether L1 systematically unblocks the hedge. Operator decision 2026-05-19: held; JustLogic d≥6 takes priority because the hedge bias is the same lever in a cleaner 3-class setting.
- **BBEH-mini @ `medium`** (44% with paren-strip ~48%): in band but **24.7s mean with 1.8-219.9s range** — operationally unviable for per-cycle wall-clock. Operator note: "really only if other things don't work."
- **BoardgameQA Main-depth3 @ `low`** (64% above ceiling): same class-collapse as BBEH boardgame_qa subtask; 0.5s/sample (very cheap). Revisit as part of the class-collapse-recovery campaign above.

## Held for later (in-band but not preferred)

- **BBEH-mini @ `medium`** — 44% in band, but the 24.7s mean latency with 219.9s outliers is operationally bad. Use only as fallback if `low` candidates exhaust.
- **BoardgameQA Main-depth3 @ `low`** — 64% above ceiling, but **0.5s/sample latency = very cheap**; class-collapse is the problem (0/8 `disproved`-gold hits). **Class-collapse may actually be what PromptPotter is anti-built against** — the L1 mutation surface is exactly the kind of prompt edit ("commit to all three classes; do not default to the safest hedge") that the optimizer is designed to discover. A future campaign could trial BBEH-mini @ low + BoardgameQA as a *class-collapse-recovery* benchmark — measure whether L1 can systematically unblock the hedge. Held for later — operator's call 2026-05-19 to wire JustLogic d≥6 first.

## Trialed and rejected — 2026-05-19 recon trail

7 datasets trialed, 7 rejected before MMLU-ProX-sw landed. **Default recon conditions: `openai/gpt-oss-20b @ reasoning_effort: low`, `temperature: 0.0`** (matches every dataset's wired `pipeline.json` — the operator's pinned meta-campaign setting). Provider was Groq for the first wave (until rate-limits), then OpenRouter `:nitro` routing for the rest. The `effort` column below flags any deviation from the `low` default.

| Dataset | Slice trialed | Effort | Measured origin | Why rejected |
|---|---|---|---|---|
| **IFBench** | `allenai/IFBench_test`, 300 test | — | *not measured — desk-rejected* | Pure instruction-following compliance, not reasoning. Sanctioned training pool `IF_multi_constraints_upto5` has 29 IFTrain families **disjoint** from the 58 test families → prompt-level transfer unproven. Off the BBEH-readiness path. Parked as diagnostic re-entry only. Full rationale: Round 5 below. |
| **MuSR** (3 subtasks) | `TAUR-Lab/MuSR` full 300-row operator cut | low | **81%** on 21-sample live cycle | `murder_mysteries` ships binary A/B choices with B-skewed golds — model coasts on frequency bias. Ceiling effect. |
| **MuSR** (2 subtasks salvage) | `object_placements` + `team_allocation`, 200 train | low | *not measurable* | Dataset cached in backend store from the 3-subtask wire; loader change didn't take effect on `new musr` (cache hit). Superseded by MMLU-ProX-sw before re-test. Loader + `datasets/musr/` deleted 2026-05-19. |
| **JustLogic** | depth ≥ 4, 25 samples | low | **52% (13/25)** | Label-bias coast: 11/25 gold = `Uncertain`, model defaulted to `uncertain` on 15. Always-predict-uncertain baseline = 44%. Real reasoning lift over mode-prediction ≈ 8pp. |
| **JustLogic** (harder) | depth ≥ 6, 25 samples | low | **44% (11/25)** — *in-band edge* | Same hedge bias but at the deeper filter. Predictions: `uncertain` × 17, `false` × 5, `true` × 3 — all 3 classes used (no class-collapse). Mode-predict-uncertain baseline = 10/25 = 40%; real lift over mode ~4pp. **Latency 0.3s/sample on OpenRouter `:nitro`** — very fast, very cheap. Closest in-band candidate among the trial-and-error English reasoning datasets; hedge bias is itself a clean L1 prompt-mutation target ("commit to TRUE/FALSE when premises strictly determine the conclusion"). Held aside pending BBEH re-recon at `low`/`medium`. |
| **ExploreToM** | non-adversarial slice, 25 samples | low | **68% (17/25)** | Model strong on state tracking even at high difficulty. Adversarial split projected at 5-15% (floor risk) — not trialed. |
| **BoardgameQA** | depth-2/3, 25 samples | low | **60% (15/25)** | Class-collapse: model never predicts `disproved` (0/7 disproved-gold samples). Effectively a 2-class problem; 60% is on the easier proved/unknown subset. |
| **BoardgameQA** (harder) | `Main-depth3` only, 25 samples | low | **64% (16/25)** — ceiling, class-collapse persists | Predictions: `proved` × 14, `unknown` × 11, `disproved` × **0**. Of 8 `disproved`-gold samples → 0 hits. On the proved/unknown subset alone, 16/17 = 94%. Model still treats it as a 2-class problem at depth-3. **Latency 0.5s/sample on `:nitro`** — very low / very cheap, like JustLogic. **Keep as a future candidate** if the class-collapse can be unblocked by prompt mutations forcing `disproved` consideration (operator's note 2026-05-19): "we can use this later." For now, JustLogic d≥6 is the better fit — same latency profile, real 3-class signal, in-band. |
| **SATBench** | vars ≥ 5 ∧ clauses ≥ 5, 21 samples | low | **100% (21/21)** | Model aces NL SAT puzzles up to 40 vars / 7 clauses. Above ceiling at every filter the schema exposes. |
| **PopQA** | low-`s_pop` quartile globally, 25 samples | low | **44% (11/25)** | 10/11 hits = `Romania` (low-popularity tail clusters by template). Per-prop stratified retry: ~20% but model still coasts on relation-modal answers (`rock` for `genre`, etc.). Knowledge-recall too memorized for `gpt-oss-20b`. |
| **CRUXEval-O** | desk-rejected | — | *not trialed* | Operator rejected upfront: Python function output prediction is "too math again" — execution reasoning isn't the diversity we want. |
| **MMLU-ProX Swahili** | `li-lab/MMLU-ProX` config `sw`, 14-cat stratified 25 samples | low | **36% (9/25)** | Initially wired 2026-05-19 as in-band winner, then rejected same day — operator clarified the target is English reasoning, not language-transfer. Swahili comprehension would dominate as the signal axis (L1 would optimize for translation tricks). Loader + `datasets/mmluprox_sw/` kept on disk pending cleanup. |
| **FOLIO** | `tasksource/folio` (open mirror of gated `yale-nlp/FOLIO`), full train 25 samples | low | **80% (20/25)** | Above-ceiling on `openai/gpt-oss-20b:nitro` via OpenRouter at the wired `low` default. Reject 2026-05-19. 3-class label match (`true`/`false`/`uncertain`); model coasts on the FOL premises being short enough for direct evaluation. Reproduced cleanly across Groq and OpenRouter, so the number is provider-independent. |
| **BBEH @ 20b/high/8k** | `BBEH/bbeh` mini, 12 samples on OpenRouter:nitro | **high** (one-time experiment) | **~25% naïve / ~75% on non-empty subset** | At `reasoning_effort: high` the hidden reasoning trace consumes the full output budget before visible content emerges (~8/12 samples returned `pred=''`); `max_tokens: 8192` override is ignored — the model enforces a per-model ~2048-visible-token ceiling. **`high` was an experiment; rejected as the wired effort. `low` and `medium` measurements landed in band — promoted to "In-band candidates" above.** |
| **AR-LSAT (AGIEval)** | `hails/agieval-lsat-ar`, full 230-row `test` first 25 | low | **72% (18/25)** — CEILING | 5-option LSAT analytical-reasoning MC (schedule/assignment puzzles with hard constraints). Literature projected smaller models 30-50%; `gpt-oss-20b @ low` solves it directly at 1.8s/sample. No headroom for L1 lift. **First-pass recon had a field-shape bug** (assumed `gold`/`choices` were stringified, HF stores them as native Python lists → empty options sent to model, every sample MISS) — patched, then 72% on real prompts. Could become a "regression probe" at wire-time but unsuitable as meta-campaign focus. |
| **MuSiQue** (macro) | `dgslibisey/MuSiQue` (open mirror; AI2 original gated), hop-stratified 2/3/4, 25 samples | low | **60% (15/25)** — CEILING macro, **38% (3/8) on 3hop-only** | Reading-comprehension multi-hop QA. Paragraphs supplied in user prompt (NOT closed-book) to sidestep PopQA-style tail-entity-recall. Per-hop: 2hop=89% (ceiling), 3hop=38% (in band), 4hop=57% (borderline; several misses are partial-string mismatches very close to gold). Macro rejected. **3hop-only cut held** as a secondary candidate — overlaps with BBEH's RC subtasks so lower marginal-diversity value than PlanBench / NaturalPlan. |
| **NaturalPlan** | `google-deepmind/natural-plan` raw GitHub (NOT HF — colleague misremembered) | 3-subtask stratified, 25 samples | low | **36% (9/25)** in-band macro — **HELD next-priority** | Per-subtask scorer dispatch was required (each gold has a different shape): `trip_planning` 0/9 (real floor at low), `calendar_scheduling` 6/9 (ceiling — bare 4-token boilerplate gold caused initial scorer-artifact 0% before the day+time-slot scorer was added), `meeting_planning` 3/7 = 43% (clean in-band). 0.5s/sample. **Wire path: `meeting_planning`-only** for the cleanest cut. See "Next-priority after JustLogic" section above. |
| **PlanBench task_1** | `tasksource/planbench`, config `task_1_plan_generation`, multi-domain stratified, 25 samples | low | **36% (9/25)** in-band — **HELD next-priority** | PDDL-style plan generation across blocksworld + logistics + obfuscated variants. Scorer = 50% action-call overlap with gold plan (coarse but fair for 3-7 action plans). 1.5s/sample. Brand-new family (planning, no overlap with current portfolio). Wire-time needs PDDL plan-validator scorer for rigor. See "Next-priority after JustLogic" section above. |

## Selection trail — six rounds (2026-05-18 → 2026-05-19)

**Round 1 — "Goldilocks for 120b @ low" (wrong model, anchored too high).** Picked GPQA Diamond + MuSR. Rejected when operator corrected: target is 20b, not 120b.

**Round 2 — "Goldilocks for 20b @ low, origin 40–75%".** Picked MMLU-Pro (~63–67%) + MuSR (~45–60%). Rejected when operator pushed back: bigger headroom = cleaner signal/noise, want origin lower not higher.

**Round 3 — "20–30% origin, big ceiling-room, multi-subtask".** Picked OlympiadBench (math/physics) → operator asked for non-math. Re-scoped to MuSR (deductive/spatial/constraint reasoning) as primary, FOLIO as fallback. OlympiadBench kept as math-axis option.

**Round 4 — "signal content > N + ceiling-room"** (peer-review pushback). Reframed: per-sample structured failure modes outrank raw accuracy resolution for L1 critique quality. Added IFBench as a parallel Track B (diagnostic). Rejected AA-LCR (LLM-judge cost). Deferred AA-Omniscience pending floor check.

**Round 5 — IFBench parked.** Literature research confirmed (a) no author-blessed train/test sub-split of the 300, (b) training pool families disjoint from test families, (c) tests compliance not reasoning. Dropped from primary trial sequence. Produced `docs/operations/adding-a-dataset.md` as a side benefit.

**Round 6 — empirical recon, first wave: 7 candidates, all rejected, then MMLU-ProX-sw landed.** Wired MuSR per Round 5; trial cycle landed at 81% origin (ceiling). Salvaged to 2 subtasks (`murder_mysteries` excluded) — but the dataset-store cache blocked re-test. Pivoted to a fresh literature hunt with the new evidence: published anchors from Llama-3-8B / T5-XL / GPT-3.5 systematically underpredict `gpt-oss-20b @ low`. Recon'd 4 more candidates (JustLogic d≥4 52%, ExploreToM 68%, BoardgameQA d2/3 60%, CRUXEval-O desk-rejected). All over-band. Expanded hunt: shortlist 2 added knowledge-intensive (PopQA), structured-prediction (SATBench), language-transfer (MMLU-ProX Swahili). Recon: SATBench 100% (over-ceiling), PopQA 44%-then-20%-with-coast (knowledge-memorized), **MMLU-ProX Swahili 36% — in band**.

**Round 7 — operator rejects MMLU-ProX-sw, retests harder strata + BBEH (2026-05-19).** Operator clarified: target is *English* reasoning, not language-transfer (MMLU-ProX-sw would have L1 finding "translate to English first" tricks). MMLU-ProX-sw moved to rejected. Re-recon with harder strata + BBEH at the wired `low` (plus a `medium` fallback test, with `high` as the upfront experiment): **JustLogic d≥6 = 44% (in band, hedge bias, no class-collapse)**, BoardgameQA Main-depth3 = 64% (ceiling + class-collapse persists; held for later — very cheap latency), **BBEH-mini @ `low` = 28% (in band; boardgame_qa subtask class-collapse, but other subtasks contribute), BBEH-mini @ `medium` = 44% (in band but 24.7s mean with 220s tail — fallback only)**. **Public BBEH-on-20b ~14% reference is debunked** — our in-house measurement at `low` lands at 28% on the mini split; the public number must have used a different setup or a different model. Two viable candidates: JustLogic d≥6 and BBEH @ low. Operator decision pending.

**Round 8 — colleague triage adds 4 new candidates (2026-05-19, post-JustLogic wire).** Research colleague raised NaturalPlan + MuSiQue + NPHardEval + CLadder. Pre-sort triage on existing evidence: CLadder dropped (same class-with-hedge family as JustLogic / BoardgameQA / FOLIO — colleague's own rejection pattern). NPHardEval deferred (canonical task data is DIMACS files on GitHub with ad-hoc ground-truth construction — wiring a fair harness exceeds today's scope). Recon'd the other two, plus a follow-up wave with AR-LSAT + PlanBench: **AR-LSAT 72% (CEILING — surprise vs lit projection)**, **MuSiQue 60% macro CEILING (3hop-only at 38% in-band held)**, **NaturalPlan 36% in-band macro (Frankenstein avg: trip=0% / calendar=67% / meeting=43%; `meeting_planning`-only is the clean 43% cut)**, **PlanBench task_1 36% in-band**. Two systemic findings: (a) NaturalPlan is NOT on the HF Hub despite the colleague's claim — data lives in `google-deepmind/natural-plan/data/*.json` on GitHub; future recons should `curl https://api.github.com/repos/.../contents/...` to verify the source layout BEFORE writing a `load_dataset(...)` call; (b) AR-LSAT first-pass had a field-shape bug (assumed HF stores `gold` / `choices` as stringified, actually native lists) — recon harness should always inspect `type(row[field])` before `ast.literal_eval`. **Operator decision: hold PlanBench + NaturalPlan as "next-priority after JustLogic" — both genuinely diversify the portfolio (no planning surface wired today).** See "Next-priority after JustLogic" section above for the full per-dataset breakdown.

The systemic finding: model-strength projections from older proxies underpredict `gpt-oss-20b @ low` by 10-20pp. Reasoning benchmarks designed for the GPT-3.5 / Llama-3-8B era are ceiling-prone for our model. Language-transfer (Swahili) bypasses this — the model's strength on reasoning *in English* is bounded by its weakness in *reading Swahili technical prose*, which is empirically a 30-50pp gap. The bias rule going forward: **prefer measurement to projection; bias projections from <20B-class anchors upward 10-20pp.**

**Wired**: JustLogic d≥6 (Round 7 outcome; MMLU-ProX-sw rejected in Round 7 — language-transfer, not English reasoning).

This sequence is operator-revisable any time.

## Rejected without trial — one-line reasons

Captured here so they don't get re-investigated next time:

- **GPQA Diamond / GPQA Main** — gpt-oss-20b-low scores 56.8% (Diamond) → top of band, ~10pp ceiling-room only. N=198 (Diamond) also sub-spec.
- **MMLU-Pro** — 20b @ low ~63–67%. Above-band; ceiling effect.
- **GSM8K** — saturated at 78% for 20b.
- **MATH-500 / MATH Level 5** — 20b is a strong math model (37% AIME); Level 5 origin likely 40–55%. Above band.
- **HLE** — 4.2% origin sounds great for headroom, but ceiling is only ~17% (120b @ high tops 17.3%). Floor problem in disguise — model fundamentally can't do most HLE problems regardless of prompt.
- **ZebraLogic** — <15% even on easy split for 7–10B class; mode-collapses to "always wrong" like BBEH.
- **ARC-AGI-2** — pure LLMs score 0–1%. Hard floor.
- **FrontierMath** — best mid-tier <10% even on Tier 1–3. Floor.
- **SimpleBench** — private test set (10 public Qs only).
- **LiveBench** — monthly refreshes invalidate cross-cycle comparisons mid-campaign.
- **AGIEval-EN** — Llama-3.1-8B base already 47.8% overall; 20b instruct likely 50–60%. Above-band.
- **DROP** — F1 + period-stopword tokenization bug systematically tanks small-model signal. Broken scoring, not real reasoning gap.
- **ReClor** — bimodal: hard split ~25% (random), easy split ~80%. No usable middle.
- **StrategyQA** — origin too high (~55–65%), ceiling too low (~70%). No room.
- **ANLI R3** — in band but strictly worse than FOLIO (single axis, no subtasks).
- **LogiQA 2.0** — translation artifacts; English subset overlaps AGIEval.
- **HumanEval / CRUX** — single axis (code format); no decomposition lever.
- **IFEval** — predecessor to IFBench; superseded by IFBench's expanded constraint set and 2025-12 release.
- **Tau-Bench** — needs tool-call infrastructure; outside the current connector boundary.
- **AA-LCR** — LLM-judge per sample at cycle scale = budget-incompatible. Same disqualifier as GDPval-AA.
- **GDPval-AA** — pairwise Elo scoring (breaks PoBB), artifact outputs (docs/slides/diagrams — beyond `llm_only` node), agentic published scores (tool use, not raw completion). Right tool for benchmarking agents, wrong tool for L1 meta-prompt evolution.

## Deferred research

Candidates that may earn a slot pending an empirical check. Verify the open question first. **Projection bands here are priors, not commitments — after the Round 6 recon trail showed 5-for-5 overshoots vs published anchors, bias all <20B-class anchors upward 10-20pp.**

- **AA-Omniscience** (`ArtificialAnalysis/AA-Omniscience-Public`, N=600 public / 6000 full, 42 topics, released 2025-11). Asymmetric scoring (+correct / −hallucinated / 0 abstain) gives L2 a real second knob: a candidate that hallucinates 30% and one that abstains 30% score differently at identical correctness. **Open question — floor risk.** AA reports "even the best frontier models score only slightly above 0" on the Omniscience Index → `gpt-oss-20b @ low` plausibly deep-negative, but the calibration axis means lift can come from teaching abstention even without raising raw correctness. **Verify with a 50-sample reconnaissance slice** before committing. Currently the only candidate not invalidated by Round 6 evidence — its scoring rubric is different enough that the proxy-anchor projection issue doesn't apply.

*(Round 6 ran recons on CRUXEval-O, JustLogic, ExploreToM, BoardgameQA, PopQA, SATBench — all rejected. Measurements + reasons in the "Trialed and rejected" table above. Do not re-investigate.)*

## Update protocol

When a shortlisted dataset is trialed:
1. Record measured 20b-low origin + observed ceiling in the row above.
2. If it falls outside the projected band, move it to *Rejected* with the empirical reason.
3. If it works, leave it shortlisted — at any time multiple datasets can serve as meta-campaign focus, rotated per-cycle.
4. Once a dataset becomes the *primary* focus for a meta-campaign run, also update that campaign's NOTES.md (e.g. `.promptpotter/meta_campaigns/l1_generate/NOTES.md`) with the model + reference accuracy.

See also: [`dataset-reasoning-matrix.md`](dataset-reasoning-matrix.md) for per-dataset model defaults once a candidate graduates from shortlist to wired.
