# Dataset Selection Rationale — Self-Optimizing Campaign Signal Density

> **How to add a dataset:** [`adding-a-dataset.md`](adding-a-dataset.md) — research the canonical train/test split *before* writing the loader. This doc is *which* datasets and *why*; the add-doc is *the process for wiring one*.

Parallel to [`dataset-reasoning-matrix.md`](dataset-reasoning-matrix.md) (per-dataset model defaults). This doc captures *which* datasets we trial during L1 optimizer prompt evolution and *why* — same operator-driven format, evidence + verdicts + dates.

## Frame — BBEH is the headline; self-optimizing campaign needs signal

**BBEH stays the headline benchmark** for publication framing. Nothing about the candidate list below changes that.

**But** at the optimizer's current maturity, BBEH is the wrong *iteration* target for L1 optimizer prompt evolution. `gpt-oss-20b @ low` scores low enough on BBEH that every cycle ties at noise (our in-house BBEH-mini measurement at `low` is 28%, which also debunks the ~14% public figure for this model), PoBB can't separate candidates, and the L1 self-optimizing campaign can't tell good edits from bad ones.

Framing the operator gave (2026-05-18): *we are too far from the local valley on BBEH; we have to work our way to it with more tractable signal first — improve L1 optimizer prompts (and other optimizer pieces) on datasets where lift is measurable, then return to BBEH as headline with better hyperparameters and a more mature optimizer.*

## Selection criteria — L1-self-optimizing campaign focus datasets

A focus dataset for L1 optimizer prompt evolution must satisfy:

1. **Origin in band.** `gpt-oss-20b @ low` scores **15–40%** at origin. Below 15% → floor effect (BBEH problem). Above 40% → ceiling effect (no headroom for L1 lift to register against PoBB noise). *The band is 15-40% rather than 15-35% because of the projection slop in § What the selection trail established.*
1b. **Origin clears its CONSTANT-ANSWER floor.** The score a stub emitting the single commonest label would earn. This is the floor that matters, and it is per-dataset, not 15%: on a 3-class set whose majority label holds 40% of the bank, a collapsed pipeline scores 40% and reads as a healthy in-band origin with headroom. An origin that merely ties its constant is not a measurement — nothing can be optimised out of it, because the pipeline is not reading the input. **Read it off the `answer_distribution` panel**, which renders the score a constant single-label answer would earn on every round — that is the live surface for this criterion. (An earlier enforcement — `classify_band` + `constant_answer_floor` in `application/resource_matrix/matrix.py` — was retired 2026-07-26 having never once run: reaching it required `matrix measure`, and no matrix was ever measured. It is now enforced where the measurement is actually taken: `domain/scoring.py::is_answer_collapsed` withholds θ from a collapsed candidate and PoBB eliminates it, so a dataset whose pipeline ties its constant can no longer contribute a fitted ability at all.) Criterion (1) alone cannot see this and let a degenerate JustLogic cell into the L4 panel for the whole of its first campaign — see the JustLogic row below.
2. **Reachable ceiling.** Plausible **50–75%** under strong prompt engineering. The origin-to-ceiling gap is what L1 climbs; bigger gap = cleaner signal/noise.
3. **N ≥ 400, preferably 800+.** For stable cycle-to-cycle verdicts under PoBB (thresholds: `pobb_epsilon` / `elimination_n_min`, defaults on `OptimizationConfig`). Smaller N usable with per-subtask stratification.
4. **Multiple distinct subtask categories.** Each subtask is an independent L1 prompt lever (decomposition, scaffolding, role-priming, anti-shortcut framing, format pinning). Single-axis datasets give L1 only one knob to turn.
5. **Deterministic per-sample grading.** Exact match, MC, F1, regex extraction. **No LLM-judge scoring** (breaks PoBB's per-candidate independence model; cost-prohibitive at cycle scale).
6. **No mode-collapse on a single gold class.** A dataset where the modal answer covers >40% of the gold set, or where the model collapses 3-class to 2-class (BoardgameQA's missing `disproved`), will be label-bias coasted and produce inflated origin. Stratify by `prop` / `category` / `subtask` to spot the trap before commit.
7. **HF-loadable.** Single jsonl on Hugging Face = trivial loader. Custom scraper = real plumbing cost.
8. **Contamination-resistant.** 2024+ release preferred. Synthetic generation a plus.
9. **Anchored by measurement, not projection.** A 25-sample recon on `gpt-oss-20b @ low` via Groq is the verdict — projections from Llama-3-8B / T5-XL / GPT-3.5 anchors systematically underpredict `gpt-oss-20b`'s reasoning strength (§ What the selection trail established).

## Headline ≠ focus

| Role | Dataset(s) | Why |
|---|---|---|
| **Headline benchmark** (publication) | BBEH | Hardest reasoning benchmark, established competitor comparison, public leaderboards. |
| **Self-optimizing campaign focus** (L1/L2/L3 iteration) | **`justlogic-d234`** (iid mix of depths 2-4) | Live L4 inner instrument (`datasets/justlogic-d234/`); BBEH-mini @ `low` held as secondary in-band candidate. Next-priority queue: **PlanBench task_1** (36%, PDDL planning — brand-new family) and **NaturalPlan** (36% macro; `meeting_planning`-only at 43% is the clean cut) — both diversify into planning, no overlap with current portfolio. |
| **Connector validation** | TermNorm (lca-termnorm) | Per-connector regression, not optimizer iteration. |

When the optimizer matures enough that L1 prompts produce measurable lift on BBEH, the focus role collapses back into the headline role. Until then, they are separate jobs.

## Why `gpt-oss-20b @ reasoning_effort: low` (operator commitment 2026-05-19)

Operator-pinned model for the self-optimizing campaign focus. Justification:
- **Leading open-source** at the 20B-active scale.
- **Fast on Groq** routing (845 tok/s on `:nitro`-eligible providers; ~5s median per call observed).
- **Very cheap** — $0.03 in / $0.14 out per Mtok. A full-cycle eval on the 420-sample MMLU-ProX-sw train pool with 5 candidates × 6 rounds ≈ 12.6k calls fits well inside Groq's daily volume.
- **Conservative-floor at `reasoning_effort: low`** per `promptpotter/CLAUDE.md` — optimizer climbs from the floor. `medium` / `high` are L1-reachable mutations when sibling-yield supports.

Pinning is via `nodes.llm_only.config` overlay in each dataset's `pipeline.yaml`, not in `optimizer.param_keys` — L1 cannot propose `model` or `provider` mutations (operator-locked axes per `PARAM_FORBIDDEN_KEYS`).

## Wired — primary

**JustLogic — `justlogic-d234`, an iid random mix of depths 2, 3 and 4.** Synthetic 3-class
deductive reasoning (`TRUE`/`FALSE`/`Uncertain`, Chen 2025), so zero contamination and a
balanced gold distribution — a class bias the pipeline shows is a reasoning failure, not a
label-skew coast. The cut, the scoring rule and the `:nitro` speed trade are owned by
[`../../datasets/justlogic-d234/dataset.md`](../../datasets/justlogic-d234/dataset.md); the
model pin by [`dataset-reasoning-matrix.md`](dataset-reasoning-matrix.md).

Two things a reader of this page needs that those files do not carry:

- **Depth cuts do not compare.** Each is a separate `dataset_name` sharing no cache key with
  another, so a cross-cut "band" reads the keying rather than the capability
  (`datasets/CLAUDE.md` § L4). The earlier d6-7 wiring and its recon numbers were replaced for
  that reason, not refined.
- **The hedge is not a prompt-shaped target.** The pipeline's dominant pathology at `low` is
  retreat to `Uncertain`, and it does **not** respond to being told off — anti-hedge wording,
  derivation procedures and personas measure *worse* than the plain origin, because the extra
  text competes for the budget the derivation needs. Attack the chain, not the conclusion.

Origin and latency are unmeasured under the current wiring; read them off a cold-workspace
ledger (`noise-floor --k 3`) rather than quoting a figure from this page.

## Next-priority after JustLogic

Two new in-band candidates from the colleague-triage recon (NaturalPlan + MuSiQue + AR-LSAT + PlanBench, 4 candidates) — operator decision: **hold both, wire after JustLogic delivers its first cycle**, treat as the "30-40% experiments" queue. Both genuinely diversify L1 attack surface (planning, not deduction or math).

| Dataset | HF / source | Slice | Effort | Measured origin | Latency | L1 attack surface |
|---|---|---|---|---|---|---|
| **PlanBench task_1** | `tasksource/planbench`, config `task_1_plan_generation` (2,270 rows) | Multi-domain stratified (~5/domain across blocksworld + logistics + 3 obfuscated variants), 25 samples | low | **36% (9/25)** | **1.5s/sample** | PDDL-style symbolic planning. **Brand-new family** for the portfolio (no overlap with deduction / math / multi-hop QA). Obfuscated-domain variants (`paltry`, `sip`, `wretched` as action names) deliberately test reasoning vs pattern-matching — high-value subset for L1 prompt mutation that *forces* PDDL-shaped reasoning. Recon scorer is coarse 50% action-call overlap; **wire-time needs a PDDL plan validator** (~half-day work) for credible per-cycle scoring. |
| **NaturalPlan** | `google-deepmind/natural-plan` raw GitHub (NOT on HF Hub — colleague misremembered) | 3-subtask stratified (`trip_planning` + `calendar_scheduling` + `meeting_planning`), ~9/subtask, 25 samples | low | **36% (9/25)** macro — Frankenstein avg | **0.5s/sample** | Multi-waypoint constraint planning. **Per-subtask breakdown reveals the trap**: `trip_planning` 0/9 (real floor at low, combinatorial flight-chain search), `calendar_scheduling` 6/9 (ceiling at this slice; needs harder filter — more participants, tighter windows), `meeting_planning` 3/7 = 43% (clean in-band, real constraint problem). Macro 36% is misleading — L1 would game calendar's short boilerplate golds and never make progress on trip. **Cleanest wire is `meeting_planning`-only at 43%.** Scorer needs per-subtask dispatch: day+time-slot exact match for calendar, joined-list token overlap for meeting, token overlap for trip. |

**Why these two specifically**:

- **AR-LSAT was the third candidate** (`hails/agieval-lsat-ar`, 230 test rows, 5-option MC). Recon: **72% (18/25) CEILING at 1.8s/sample**. `gpt-oss-20b @ low` solves AGIEval LSAT analytical-reasoning puzzles directly — no headroom for L1 lift. Surprising vs literature projection (smaller models 30-50%); the AGIEval cut may be easier than full LSAT-AR, OR the model is genuinely strong on this format. **Rejected for self-optimizing campaign focus**; could become a "verify-improvements-don't-regress" probe at wire-time. See `recon_arlsat` in the recon script for the bulletproof MC parsing — reusable for any future LSAT-style MC dataset.
- **MuSiQue macro ceiling** but per-hop reveals structure: 2hop = 89%, 3hop = 38%, 4hop = 57% (with several "misses" being partial-string mismatches very close to gold). **3hop-only at 38% is the clean in-band cut** — held as secondary if MuSiQue family becomes the L1 surface, but lower priority than PlanBench + NaturalPlan because multi-hop QA overlaps with BBEH's reading-comprehension subtasks (lower marginal-diversity value).

**Held subtask cuts** (lower-priority than the macro candidates above; revisit only if PlanBench + NaturalPlan don't pan out):

- **NaturalPlan `meeting_planning`-only** — 43% on 7 samples, single coherent constraint family, scorer is token-overlap on joined waypoint list.
- **MuSiQue `3hop`-only** — 38% on 8 samples, substring scorer (with `answer_aliases`) is clean, ~6,000 rows for the 3hop split alone.

## In-band candidates held for later

- **BBEH-mini @ `low`** (28%, 1.1s median latency): boardgame_qa subtask class-collapses to `unknown` at `low`, resolves at `medium`. Class-collapse is itself a research-grade target — PromptPotter L1's prompt-mutation surface may be **exactly anti-built against this** ("commit to all three classes; do not default to the safest hedge"). A future campaign could trial BBEH-mini + BoardgameQA as a *class-collapse-recovery* benchmark — measure whether L1 systematically unblocks the hedge. Operator decision 2026-05-19: held; JustLogic d≥6 takes priority because the hedge bias is the same lever in a cleaner 3-class setting.
- **BBEH-mini @ `medium`** (44% with paren-strip ~48%): in band but **24.7s mean with 1.8-219.9s range** — operationally unviable for per-cycle wall-clock. Operator note: "really only if other things don't work."
- **BoardgameQA Main-depth3 @ `low`** (64% above ceiling): same class-collapse as BBEH boardgame_qa subtask; 0.5s/sample (very cheap). Revisit as part of the class-collapse-recovery campaign above.

## Trialed and rejected — 2026-05-19 recon trail

7 datasets trialed, 7 rejected before MMLU-ProX-sw landed. **Default recon conditions: `openai/gpt-oss-20b @ reasoning_effort: low`, `temperature: 0.0`** (matches every dataset's wired `pipeline.yaml` — the operator's pinned self-optimizing campaign setting). Provider was Groq for the first wave (until rate-limits), then OpenRouter `:nitro` routing for the rest. The `effort` column below flags any deviation from the `low` default.

| Dataset | Slice trialed | Effort | Measured origin | Why rejected |
|---|---|---|---|---|
| **IFBench** | `allenai/IFBench_test`, 300 test | — | *not measured — desk-rejected* | Pure instruction-following compliance, not reasoning. Sanctioned training pool `IF_multi_constraints_upto5` has 29 IFTrain families **disjoint** from the 58 test families → prompt-level transfer unproven. Off the BBEH-readiness path. Parked as diagnostic re-entry only. Full rationale: Round 5 below. |
| **MuSR** (3 subtasks) | `TAUR-Lab/MuSR` full 300-row operator cut | low | **81%** on 21-sample live cycle | `murder_mysteries` ships binary A/B choices with B-skewed golds — model coasts on frequency bias. Ceiling effect. |
| **MuSR** (2 subtasks salvage) | `object_placements` + `team_allocation`, 200 train | low | *not measurable* | Dataset cached in backend store from the 3-subtask wire; loader change didn't take effect on `new musr` (cache hit). Superseded by MMLU-ProX-sw before re-test. Loader + `datasets/musr/` deleted 2026-05-19. |
| **JustLogic** | depth ≥ 4, 25 samples | low | **52% (13/25)** | Label-bias coast: 11/25 gold = `Uncertain`, model defaulted to `uncertain` on 15. Always-predict-uncertain baseline = 44%. Real reasoning lift over mode-prediction ≈ 8pp. |
| **JustLogic d6-7** (the deep cut) | depth ≥ 6, 25 samples | low | **rejected — a `floor` cell under criterion (1b)** | A 25-sample recon read 44% and "all 3 classes used, no class-collapse", and that verdict was wrong twice over. The ~4pp lift over the mode-predict-`Uncertain` baseline sits well inside the Wilson half-width at n=25, so it was never a signal; and label PRESENCE is the wrong test — the collapse is in the *proportions*. Measured across full-bank origin runs the pipeline answers `Uncertain` on ~80% of samples (`gpt-oss-120b`: ~96%, exactly its constant floor). The hedge is real but is the pipeline's dominant pathology at this depth, **not** a prompt-mutation target — see § Wired — primary. The live cut is `justlogic-d234`. |
| **ExploreToM** | non-adversarial slice, 25 samples | low | **68% (17/25)** | Model strong on state tracking even at high difficulty. Adversarial split projected at 5-15% (floor risk) — not trialed. |
| **BoardgameQA** | depth-2/3, 25 samples | low | **60% (15/25)** | Class-collapse: model never predicts `disproved` (0/7 disproved-gold samples). Effectively a 2-class problem; 60% is on the easier proved/unknown subset. |
| **BoardgameQA** (harder) | `Main-depth3` only, 25 samples | low | **64% (16/25)** — ceiling, class-collapse persists | Predictions: `proved` × 14, `unknown` × 11, `disproved` × **0**. Of 8 `disproved`-gold samples → 0 hits. On the proved/unknown subset alone, 16/17 = 94%. Model still treats it as a 2-class problem at depth-3. **Latency 0.5s/sample on `:nitro`** — very low / very cheap, like JustLogic. **Keep as a future candidate** if the class-collapse can be unblocked by prompt mutations forcing `disproved` consideration (operator's note 2026-05-19): "we can use this later." For now, JustLogic d≥6 is the better fit — same latency profile, real 3-class signal, in-band. |
| **SATBench** | vars ≥ 5 ∧ clauses ≥ 5, 21 samples | low | **100% (21/21)** | Model aces NL SAT puzzles up to 40 vars / 7 clauses. Above ceiling at every filter the schema exposes. |
| **PopQA** | low-`s_pop` quartile globally, 25 samples | low | **44% (11/25)** | 10/11 hits = `Romania` (low-popularity tail clusters by template). Per-prop stratified retry: ~20% but model still coasts on relation-modal answers (`rock` for `genre`, etc.). Knowledge-recall too memorized for `gpt-oss-20b`. |
| **CRUXEval-O** | desk-rejected | — | *not trialed* | Operator rejected upfront: Python function output prediction is "too math again" — execution reasoning isn't the diversity we want. |
| **MMLU-ProX Swahili** | `li-lab/MMLU-ProX` config `sw`, 14-cat stratified 25 samples | low | **36% (9/25)** | Initially wired 2026-05-19 as in-band winner, then rejected same day — operator clarified the target is English reasoning, not language-transfer. Swahili comprehension would dominate as the signal axis (L1 would optimize for translation tricks). Loader + `datasets/mmluprox_sw/` kept on disk pending cleanup. |
| **FOLIO** | `tasksource/folio` (open mirror of gated `yale-nlp/FOLIO`), full train 25 samples | low | **80% (20/25)** | Above-ceiling on `openai/gpt-oss-20b:nitro` via OpenRouter at the wired `low` default. Reject 2026-05-19. 3-class label match (`true`/`false`/`uncertain`); model coasts on the FOL premises being short enough for direct evaluation. Reproduced cleanly across Groq and OpenRouter, so the number is provider-independent. |
| **BBEH @ 20b/high/8k** | `BBEH/bbeh` mini, 12 samples on OpenRouter:nitro | **high** (one-time experiment) | **~25% naïve / ~75% on non-empty subset** | At `reasoning_effort: high` the hidden reasoning trace consumes the full output budget before visible content emerges (~8/12 samples returned `pred=''`); `max_tokens: 8192` override is ignored — the model enforces a per-model ~2048-visible-token ceiling. **`high` was an experiment; rejected as the wired effort. `low` and `medium` measurements landed in band — promoted to "In-band candidates" above.** |
| **AR-LSAT (AGIEval)** | `hails/agieval-lsat-ar`, full 230-row `test` first 25 | low | **72% (18/25)** — CEILING | 5-option LSAT analytical-reasoning MC (schedule/assignment puzzles with hard constraints). Literature projected smaller models 30-50%; `gpt-oss-20b @ low` solves it directly at 1.8s/sample. No headroom for L1 lift. **First-pass recon had a field-shape bug** (assumed `gold`/`choices` were stringified, HF stores them as native Python lists → empty options sent to model, every sample MISS) — patched, then 72% on real prompts. Could become a "regression probe" at wire-time but unsuitable as self-optimizing campaign focus. |
| **MuSiQue** (macro) | `dgslibisey/MuSiQue` (open mirror; AI2 original gated), hop-stratified 2/3/4, 25 samples | low | **60% (15/25)** — CEILING macro, **38% (3/8) on 3hop-only** | Reading-comprehension multi-hop QA. Paragraphs supplied in user prompt (NOT closed-book) to sidestep PopQA-style tail-entity-recall. Per-hop: 2hop=89% (ceiling), 3hop=38% (in band), 4hop=57% (borderline; several misses are partial-string mismatches very close to gold). Macro rejected. **3hop-only cut held** as a secondary candidate — overlaps with BBEH's RC subtasks so lower marginal-diversity value than PlanBench / NaturalPlan. |
| **NaturalPlan** | `google-deepmind/natural-plan` raw GitHub (NOT HF — colleague misremembered) | 3-subtask stratified, 25 samples | low | **36% (9/25)** in-band macro — **HELD next-priority** | Per-subtask scorer dispatch was required (each gold has a different shape): `trip_planning` 0/9 (real floor at low), `calendar_scheduling` 6/9 (ceiling — bare 4-token boilerplate gold caused initial scorer-artifact 0% before the day+time-slot scorer was added), `meeting_planning` 3/7 = 43% (clean in-band). 0.5s/sample. **Wire path: `meeting_planning`-only** for the cleanest cut. See "Next-priority after JustLogic" section above. |
| **PlanBench task_1** | `tasksource/planbench`, config `task_1_plan_generation`, multi-domain stratified, 25 samples | low | **36% (9/25)** in-band — **HELD next-priority** | PDDL-style plan generation across blocksworld + logistics + obfuscated variants. Scorer = 50% action-call overlap with gold plan (coarse but fair for 3-7 action plans). 1.5s/sample. Brand-new family (planning, no overlap with current portfolio). Wire-time needs PDDL plan-validator scorer for rigor. See "Next-priority after JustLogic" section above. |

## What the selection trail established

Eight rounds of literature triage and empirical recon (the per-candidate outcomes are in
§ Trialed and rejected and § Rejected without trial above and below) converged on one
systemic finding, which is the part worth carrying forward:

**Model-strength projections taken from older proxies underpredict `gpt-oss-20b @ low` by
10-20pp.** Reasoning benchmarks designed for the GPT-3.5 / Llama-3-8B era are ceiling-prone
for this model, so bias every `<20B`-class projection upward before trusting it. The one
exception measured was language transfer (Swahili), which bypasses the effect — the model's
reasoning strength in English does not carry across the language barrier — and was rejected
anyway: it teaches L1 "translate to English first" tricks rather than reasoning.

The wired outcome is `justlogic-d234` (§ Wired — primary). This sequence is
operator-revisable at any time.

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
- **GDPval-AA** — pairwise Elo scoring (breaks PoBB), artifact outputs (docs/slides/diagrams — beyond `llm_only` node), agentic published scores (tool use, not raw completion). Right tool for benchmarking agents, wrong tool for L1 optimizer prompt evolution.

## Deferred research

Candidates that may earn a slot pending an empirical check. Verify the open question first. **Projection bands here are priors, not commitments — the recon trail overshot published anchors five times out of five, so bias all <20B-class anchors upward 10-20pp.**

- **AA-Omniscience** (`ArtificialAnalysis/AA-Omniscience-Public`, N=600 public / 6000 full, 42 topics, released 2025-11). Asymmetric scoring (+correct / −hallucinated / 0 abstain) gives L2 a real second knob: a candidate that hallucinates 30% and one that abstains 30% score differently at identical correctness. **Open question — floor risk.** AA reports "even the best frontier models score only slightly above 0" on the Omniscience Index → `gpt-oss-20b @ low` plausibly deep-negative, but the calibration axis means lift can come from teaching abstention even without raising raw correctness. **Verify with a 50-sample reconnaissance slice** before committing. Currently the only candidate not invalidated by Round 6 evidence — its scoring rubric is different enough that the proxy-anchor projection issue doesn't apply.

*(CRUXEval-O, JustLogic d6-7, ExploreToM, BoardgameQA, PopQA and SATBench were all reconned and rejected — measurements + reasons in § Trialed and rejected above. Do not re-investigate.)*

## Update protocol

When a shortlisted dataset is trialed:
1. Record measured 20b-low origin + observed ceiling in the row above.
2. If it falls outside the projected band, move it to *Rejected* with the empirical reason.
3. If it works, leave it shortlisted — several datasets can serve as L4 inner cells at once.
4. Once a dataset is wired as an L4 inner benchmark, record its model + reference accuracy in [`dataset-reasoning-matrix.md`](dataset-reasoning-matrix.md) — the canonical table.

See also: [`dataset-reasoning-matrix.md`](dataset-reasoning-matrix.md) for per-dataset model defaults once a candidate graduates from shortlist to wired.
