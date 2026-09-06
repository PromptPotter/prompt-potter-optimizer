# Datasets — which ones, why, and how to add one

Which datasets we trial during L1 optimizer prompt evolution and why, and at the end **the process for wiring a new one**. Per-dataset model defaults are [`dataset-reasoning-matrix.md`](dataset-reasoning-matrix.md)'s.

**The measurement roster and the saturation bar** — owned by [`../research/benchmarks.md`](../research/benchmarks.md) § Every dataset we measured and § The admission bar; this page adds only the recon detail and the selection decision standing behind each score.

## Frame — BBEH is the headline; the self-optimizing campaign needs signal

**BBEH stays the headline benchmark** for publication framing, and nothing in the candidate list changes that. But at the optimizer's current maturity BBEH is the wrong *iteration* target: `gpt-oss-20b @ low` scores low enough that every cycle ties at noise, PoBB cannot separate candidates, and the campaign cannot tell good edits from bad. Our in-house BBEH-mini reading does **not** debunk the public figure — that gap is an open publication blocker owned by [`../research/bbeh-comparison/README.md`](../research/bbeh-comparison/README.md), and quoting it as a result is the error to avoid.

The operator's framing (2026-05-18): *we are too far from the local valley on BBEH; work toward it with more tractable signal first, then return to BBEH as headline with better hyperparameters and a more mature optimizer.*

**Which dataset holds which role** — owned by [`../research/benchmarks.md`](../research/benchmarks.md) § Order of use. TermNorm (`lca-termnorm`) sits outside the question entirely: per-connector regression, not optimizer iteration.

## Selection criteria

A focus dataset for L1 optimizer prompt evolution must satisfy:

1. **Origin in band.** `gpt-oss-20b @ low` scores **15–40%** at origin. Below 15% → floor effect (the BBEH problem). Above 40% → ceiling effect, no headroom for L1 lift to register against PoBB noise. The band tops out at 40 rather than 35 because of the projection slop in § What the trail established.
1b. **Origin clears its CONSTANT-ANSWER floor** — the score a stub emitting the single commonest label would earn. This floor is per-dataset, not 15%: on a 3-class set whose majority label holds 40% of the bank, a collapsed pipeline scores 40% and reads as a healthy in-band origin with headroom. An origin that merely ties its constant is not a measurement, because the pipeline is not reading the input. **Read it off the `answer_distribution` panel**, the live surface for this criterion. It is enforced where the measurement is taken — `domain/scoring.py::is_answer_collapsed` withholds θ from a collapsed candidate and PoBB eliminates it — so a dataset whose pipeline ties its constant cannot contribute a fitted ability at all. Criterion 1 alone cannot see this, and let a degenerate JustLogic cell into the L4 panel for the whole of its first campaign.
2. **Reachable ceiling.** Plausible **50–75%** under strong prompt engineering. The origin-to-ceiling gap is what L1 climbs; bigger gap = cleaner signal/noise.
3. **N ≥ 400, preferably 800+**, for stable cycle-to-cycle verdicts under PoBB. Smaller N is usable with per-subtask stratification.
4. **Multiple distinct subtask categories.** Each subtask is an independent L1 prompt lever — decomposition, scaffolding, role-priming, anti-shortcut framing, format pinning. Single-axis datasets give L1 one knob.
5. **Deterministic per-sample grading** — exact match, MC, F1, regex extraction. **An LLM judge is admissible only through `promptpotter/judges/`, and its two original objections have not aged equally.** *Cost-prohibitive at cycle scale* was written when a cell was one LLM call; beside an episodic cell (one measured harbor cell: 172 s, $0.00137) a grader call is rounding error, and the judge runs once per cell and is banked. *Breaks PoBB's per-candidate independence model* still stands and is **unverified against the model** — a judge adds a measurement error that is larger and model-dependent even at `temperature: 0.0`. So: wire one where no matcher can grade the answer at all, validate it against the published grader before quoting a number, and do not treat a judged dataset as comparable to a deterministically-graded one until that check is done.
6. **No mode-collapse on a single gold class.** A modal answer covering >40% of the gold set, or a model collapsing 3-class to 2-class, will be label-bias coasted into an inflated origin. Stratify by `prop` / `category` / `subtask` to spot the trap before commit.
7. **HF-loadable.** Single jsonl on Hugging Face = trivial loader; a custom scraper is real plumbing cost.
8. **Contamination-resistant.** 2024+ release preferred, synthetic generation a plus.
9. **Anchored by measurement, not projection.** A 25-sample recon on `gpt-oss-20b @ low` is the verdict.

**Toy and demo datasets are picked on deliberation length, not difficulty.** The criteria above buy signal; a toy buys turnaround, and what costs turnaround is how long the model thinks per cell, not how many cells there are. Prefer a task answerable in one pass — recognition, extraction, ordering a given set — over one that invites the model to reason its way there, and read the split off `dashboard.json::spend.backend` (`reasoning_tokens` against `output_tokens`) on the first round rather than after the campaign. The same test applies when **evolving** one: a toy task that has grown a deliberation step has stopped being a toy.

## Why `gpt-oss-20b @ reasoning_effort: low` (operator commitment 2026-05-19)

Operator-pinned model for the self-optimizing campaign focus: leading open-source at the 20B-active scale, fast on Groq routing (845 tok/s on `:nitro`-eligible providers, ~5s median per call), very cheap at $0.03 in / $0.14 out per Mtok, and conservative at `low` so the optimizer climbs from the floor — `medium` / `high` stay L1-reachable mutations when sibling-yield supports them.

Pinning is via the `nodes.llm_only.config` overlay in each dataset's `pipeline.yaml`, never `optimizer.param_keys`: L1 cannot propose `model` or `provider` mutations (operator-locked axes per `PARAM_FORBIDDEN_KEYS`).

## The roster

Every dataset reconned, with its verdict. Default recon conditions: `openai/gpt-oss-20b @ reasoning_effort: low`, `temperature: 0.0`, 25 samples — Groq for the first wave until rate limits, then OpenRouter `:nitro`. **Do not re-investigate a rejected row.**

| Dataset | Slice | Measured origin | Verdict | What the number means |
|---|---|---|---|---|
| **JustLogic — `justlogic-d234`** | iid mix of depths 2/3/4 | see below | ✅ **WIRED — primary** | Synthetic 3-class deduction (`TRUE`/`FALSE`/`Uncertain`, Chen 2025): zero contamination, balanced gold distribution, so a class bias the pipeline shows is a reasoning failure rather than a label-skew coast. Cut, scoring rule and the `:nitro` speed trade are [`../../datasets/justlogic-d234/dataset.md`](../../datasets/justlogic-d234/dataset.md)'s. Origin and latency are unmeasured under the current wiring — read them off a cold-workspace ledger (`noise-floor --k 3`), not from this page. |
| **SealQA — `sealqa-longseal-12`** | LongSeal at k=12 | published no-skill 26.3–33.0%, ceiling 39.4–44.7% | ✅ **WIRED** | Rejected on the LLM-judge ground, since half-lifted. Its GPT-4o-mini auto-rater is validated by the authors at 98% agreement with two human annotators over 100 answers and ships as the `sealqa` built-in (`promptpotter/judges/simpleqa.py`). Measured on `harbor` as one containerized episode per cell, graded beside two unscreened evidence rubrics. It carries no loader **by requirement**: a connector's `experiment_file` owns its panel, so a loader registered under the name would win and the panel would never publish. |
| **PlanBench task_1** | `tasksource/planbench`, `task_1_plan_generation`, multi-domain stratified | **36% (9/25)**, 1.5s/sample | 🟡 **HELD — next priority** | PDDL-style symbolic planning, a brand-new family with no overlap against deduction / math / multi-hop QA. Obfuscated-domain variants (`paltry`, `sip`, `wretched` as action names) test reasoning against pattern-matching. Recon scorer is coarse 50% action-call overlap; wire-time needs a **PDDL plan validator** (~half-day) for credible per-cycle scoring. |
| **NaturalPlan** | `google-deepmind/natural-plan` raw GitHub (not on HF), 3-subtask stratified | **36% (9/25)** macro, 0.5s/sample | 🟡 **HELD — next priority; wire `meeting_planning` only** | The macro is a Frankenstein average and the per-subtask breakdown is the trap: `trip_planning` 0/9 (real floor at low — combinatorial flight-chain search), `calendar_scheduling` 6/9 (ceiling; bare 4-token boilerplate gold), `meeting_planning` 3/7 = **43%**, clean in-band. L1 would game calendar's boilerplate and never progress on trip. Scorer needs per-subtask dispatch. |
| **BBEH-mini @ `low`** | mini | **28%**, 1.1s median | 🟡 held | `boardgame_qa` class-collapses to `unknown` at `low` and resolves at `medium`. Class-collapse is itself a research-grade target — L1's mutation surface may be exactly anti-built against it. Held because the hedge bias is the same lever in a cleaner 3-class setting, which is now `justlogic-d234`. |
| **BBEH-mini @ `medium`** | mini | **44%** (paren-strip ~48%) | 🟡 held | In band, but 24.7s mean with a 1.8–219.9s range — operationally unviable for per-cycle wall-clock. Operator: "really only if other things don't work." |
| **BBEH @ 20b/high/8k** | mini, 12 samples | ~25% naïve / ~75% on the non-empty subset | ❌ effort rejected | At `high` the hidden reasoning trace consumes the full output budget before visible content emerges (~8/12 returned `pred=''`); the `max_tokens: 8192` override is ignored because the model enforces a ~2048-visible-token ceiling. `high` was a one-time experiment. |
| **BoardgameQA Main-depth3** | depth-3 | **64% (16/25)**, 0.5s/sample | 🟡 held | Predictions `proved`×14, `unknown`×11, `disproved`×**0**; 0/8 on disproved-gold. On the proved/unknown subset alone 16/17 = 94% — the model treats it as 2-class. Very cheap. Keep as a candidate *if* the collapse can be unblocked by mutations forcing `disproved` consideration. |
| **MuSiQue** | `dgslibisey/MuSiQue`, hop-stratified | **60% (15/25)** macro ceiling; **38% (3/8)** on 3hop | 🟡 3hop held, macro rejected | Paragraphs supplied in the prompt (not closed-book) to sidestep PopQA-style tail recall. Per-hop 2hop=89%, 3hop=38%, 4hop=57% with several near-miss partial strings. 3hop-only is the clean cut but overlaps BBEH's RC subtasks, so lower marginal diversity than PlanBench / NaturalPlan. |
| **AR-LSAT (AGIEval)** | `hails/agieval-lsat-ar`, first 25 of 230 | **72% (18/25)** | ❌ ceiling | `gpt-oss-20b @ low` solves LSAT analytical-reasoning puzzles directly at 1.8s/sample, against a literature projection of 30–50% for smaller models. First-pass recon had a field-shape bug (`gold`/`choices` are native lists, not stringified) — patched before the 72%. Could serve as a regression probe at wire-time. |
| **JustLogic d6-7** | depth ≥ 6 | 44% — **a `floor` cell under criterion 1b** | ❌ rejected | The recon verdict was wrong twice over. The ~4pp lift over the mode-predict-`Uncertain` baseline sits well inside the Wilson half-width at n=25, so it was never a signal; and label *presence* is the wrong test, because the collapse is in the *proportions*. Across full-bank origin runs the pipeline answers `Uncertain` on ~80% of samples (`gpt-oss-120b`: ~96%, exactly its constant floor). |
| **JustLogic depth ≥ 4** | 25 samples | **52% (13/25)** — VOID against the live cut | ❌ rejected | Label-bias coast: 11/25 gold = `Uncertain`, model defaulted to it on 15; the always-uncertain baseline is 44%, so real lift ≈ 8pp. A separate `dataset_name` sharing no cache key with `justlogic-d234`. |
| **FOLIO** | `tasksource/folio`, full train | **80% (20/25)** | ❌ ceiling | Model coasts on FOL premises short enough for direct evaluation. Reproduced across Groq and OpenRouter, so the number is provider-independent. |
| **MuSR** | `TAUR-Lab/MuSR`, 3 subtasks | **81%** on a 21-sample live cycle | ❌ ceiling | `murder_mysteries` ships binary A/B choices with B-skewed golds — the model coasts on frequency bias. A 2-subtask salvage was never measurable (backend cache hit on the loader change); loader and `datasets/musr/` deleted 2026-05-19. |
| **SATBench** | vars ≥ 5 ∧ clauses ≥ 5 | **100% (21/21)** | ❌ ceiling | Aces NL SAT puzzles up to 40 vars / 7 clauses — above ceiling at every filter the schema exposes. |
| **ExploreToM** | non-adversarial slice | **68% (17/25)** | ❌ ceiling | Strong on state tracking even at high difficulty. The adversarial split projects at 5–15%, a floor risk; not trialed. |
| **PopQA** | low-`s_pop` quartile | **44% (11/25)** | ❌ rejected | 10/11 hits were `Romania` — the low-popularity tail clusters by template. Per-prop stratified retry lands ~20% but still coasts on relation-modal answers. Knowledge recall is too memorized for this model. |
| **MMLU-ProX Swahili** | config `sw`, 14-cat stratified | **36% (9/25)** | ❌ rejected same day | In band, but the target is English reasoning: Swahili comprehension would dominate as the signal axis and L1 would optimize translation tricks. |
| **IFBench** | `allenai/IFBench_test` | desk-rejected | ❌ | Instruction-following compliance, not reasoning. Its sanctioned training pool has 29 IFTrain families **disjoint** from the 58 test families, so prompt-level transfer is unproven. Parked as diagnostic re-entry only. |
| **CRUXEval-O** | — | desk-rejected | ❌ | Operator: Python output prediction is "too math again" — execution reasoning is not the diversity we want. |

### Rejected without trial

Captured so they are not re-investigated. All are projections, and **the recon trail overshot published anchors five times out of five**, so bias every `<20B`-class anchor upward 10–20pp before trusting one.

- **GPQA Diamond / Main** — 56.8% (Diamond) → top of band, ~10pp ceiling-room; N=198 also sub-spec.
- **MMLU-Pro** (~63–67%), **MATH-500 / Level 5** (~40–55%), **AGIEval-EN** (~50–60%), **StrategyQA** (~55–65% origin, ~70% ceiling) — all above band.
- **GSM8K** — rejection **withdrawn**; the 78% it was rejected on was never measured at the bar, and it is a pilot candidate again → [`../research/benchmarks.md`](../research/benchmarks.md) § Order of use.
- **HLE** — 4.2% origin sounds like headroom, but the ceiling is ~17%. A floor problem in disguise.
- **ZebraLogic**, **ARC-AGI-2**, **FrontierMath** — hard floors (<15%, 0–1%, <10%).
- **SimpleBench** (private test set), **LiveBench** (monthly refreshes invalidate cross-cycle comparison mid-campaign).
- **DROP** — F1 + period-stopword tokenization bug tanks small-model signal. Broken scoring, not a real reasoning gap.
- **ReClor** — bimodal: hard split ~25% (random), easy ~80%. No usable middle.
- **ANLI R3** — in band but strictly worse than FOLIO: single axis, no subtasks.
- **LogiQA 2.0** (translation artifacts, overlaps AGIEval), **HumanEval / CRUX** (single axis, no decomposition lever), **IFEval** (superseded by IFBench).
- **Tau-Bench** — needs tool-call infrastructure, outside the current connector boundary.
- **AA-LCR** — LLM judge per sample at cycle scale. Re-read criterion 5's revision before re-rejecting it.
- **GDPval-AA** — pairwise Elo scoring breaks PoBB, artifact outputs (docs/slides/diagrams) are beyond the `llm_only` node, and its published scores are agentic. Right tool for benchmarking agents, wrong one for L1 prompt evolution.

### Two things about JustLogic a reader of this page needs

- **Depth cuts do not compare.** Each is a separate `dataset_name` sharing no cache key with another, so a cross-cut "band" reads the keying rather than the capability (`datasets/CLAUDE.md` § L4). The earlier d6-7 wiring and its recon numbers were replaced for that reason, not refined.
- **The hedge is not a prompt-shaped target.** The pipeline's dominant pathology at `low` is retreat to `Uncertain`, and it does **not** respond to being told off — anti-hedge wording, derivation procedures and personas all measure *worse* than the plain origin, because the extra text competes for the budget the derivation needs. Attack the chain, not the conclusion.

## What the trail established

Eight rounds of literature triage and empirical recon converged on one systemic finding: **model-strength projections taken from older proxies underpredict `gpt-oss-20b @ low` by 10–20pp.** Reasoning benchmarks designed for the GPT-3.5 / Llama-3-8B era are ceiling-prone for this model. The one measured exception was language transfer (Swahili), which bypasses the effect because reasoning strength in English does not carry across the language barrier — and which was rejected anyway.

## Deferred research

One candidate may still earn a slot, pending an empirical check.

**AA-Omniscience** (`ArtificialAnalysis/AA-Omniscience-Public`, N=600 public / 6000 full, 42 topics, released 2025-11). Asymmetric scoring (+correct / −hallucinated / 0 abstain) gives L2 a real second knob: a candidate that hallucinates 30% and one that abstains 30% score differently at identical correctness. **Open question — floor risk**, since AA reports even the best frontier models scoring only slightly above 0 on the Omniscience Index. But the calibration axis means lift can come from teaching abstention without raising raw correctness, and its rubric is different enough that the proxy-anchor projection issue does not apply. **Verify with a 50-sample recon before committing.**

## Update protocol

When a shortlisted dataset is trialed: record the measured origin and observed ceiling in the roster; if it falls outside the projected band, mark it rejected with the empirical reason; if it works, leave it shortlisted, since several datasets can serve as L4 inner cells at once. Once one is wired as an L4 inner benchmark, record its model + reference accuracy in [`dataset-reasoning-matrix.md`](dataset-reasoning-matrix.md) — the canonical table.

## Adding a dataset — the wiring process

For any new dataset under `datasets/{name}/`, public benchmark or private task. **Research before code.**

**1. Research the canonical protocol — first, always.** Before writing the loader, find the **author-recommended train/test split and evaluation protocol** in the published literature. For public benchmarks: the dataset card, the parent repo README, the paper's evaluation section, and any leaderboard methodology — look for "evaluation protocol", "splits", "train/test", "held-out". For private / operator tasks, ask the operator *"What slice have you reserved as test, or do you want to cut one now?"* before defining the optimization pool; don't assume the whole file is fair game.

If the canonical answer isn't obvious in 5 minutes, delegate it to a fresh agent with no project context — the fastest path to an uncontaminated read. Template:

> Research the canonical train/test split and evaluation protocol used in the published literature for *&lt;DATASET&gt;* (HuggingFace: *&lt;path&gt;*, paper: *&lt;arxiv URL&gt;*, repo: *&lt;github URL&gt;*).
>
> Specifically:
> - What split, if any, do the authors recommend in the README, paper, or dataset card?
> - What split do published papers actually score against — full set, a subset, a held-out cut?
> - Is there a sister training dataset that papers use, and if so, which one and at what sample size?
> - Cite every claim (URL + section/line).
>
> Report findings only. Do not write code. Do not propose an implementation. Under 400 words.

**2. Report findings in `dataset.md`.** The new `datasets/{name}/dataset.md` must have a **Data** section quoting the authors' protocol verbatim, with citations (URL + section). State sample counts and any sister training dataset.

**2b. If the task is TURN-STRUCTURED, decide the step schema now.** A multi-turn or multi-step task is ONE cell — a conversation is a testlet, not a series of items ([`../methods/verdict-resolution.md`](../methods/verdict-resolution.md) § Phase 3 sketch). Two things follow, both cheap now and expensive later. **Name a fixed SEMANTIC step schema** rather than a turn index: an agentic episode takes however many turns it takes, so "step 3" pools nothing across rows. And **bank each step's score as its own named term**, letting the `scoring` formula do the collapsing — that sketch owns why, and it is the one part of this that cannot be added later. Record both in `dataset.md`.

A search-augmented task should adopt the shipped `retrieve → ground → answer` schema rather than coin one: it is three `campaign_config.judges` entries, and the measurement identity folds the whole mapping, so re-keying later re-pays for every row ([`../../promptpotter/judges/CLAUDE.md`](../../promptpotter/judges/CLAUDE.md) § The step schema — which also carries the obligation to screen the two authored rubrics before funding a campaign).

**3. Operator confirms the cut — before any wire.** The cut + protocol decision is operator-directed once the canonical protocol is on the table. **Never invent a split. Never consume a canonical test set as an optimization pool** without the operator explicitly accepting that the resulting number is not leaderboard-comparable. Any deviation from the canonical protocol gets said out loud in `dataset.md`, with the reason.

**4. Then wire.** Only after 1–3: the loader, the scorer, and the `datasets/{name}/` config tree (`pipeline.yaml`, `campaign.yaml`, `task_description.md`, `prompts/{node}.yaml`, `dataset.md`). Per-dataset model defaults go in [`dataset-reasoning-matrix.md`](dataset-reasoning-matrix.md) once the candidate graduates from shortlist to wired.
