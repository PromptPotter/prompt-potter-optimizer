# Dataset Selection Rationale — Meta-Campaign Signal Density

Parallel to [`dataset-reasoning-matrix.md`](dataset-reasoning-matrix.md) (per-dataset model defaults). This doc captures *which* datasets we trial during L1 meta-prompt evolution and *why* — same operator-driven format, evidence + verdicts + dates.

## Frame — BBEH is the headline; meta-campaign needs signal

**BBEH stays the headline benchmark** for publication and milestone framing (M11 Publication Benchmarks). Nothing about the candidate list below changes that.

**But** at the optimizer's current maturity, BBEH is the wrong *iteration* target for L1 meta-prompt evolution. `gpt-oss-20b @ low` scores in the floor band on BBEH (~14% public, see [BBEH score anomaly](../../README.md) and `project_bbeh_score_anomaly.md`) — every cycle ties at noise, PoBB can't separate candidates, and the L1 meta-campaign can't tell good edits from bad ones.

Framing the operator gave (2026-05-18): *we are too far from the local valley on BBEH; we have to work our way to it with more tractable signal first — improve L1 meta-prompts (and other optimizer pieces) on datasets where lift is measurable, then return to BBEH as headline with better hyperparameters and a more mature optimizer.*

## Selection criteria — L1-meta-campaign focus datasets

A focus dataset for L1 meta-prompt evolution must satisfy:

1. **Origin in band.** `gpt-oss-20b @ low` scores **15–35%** at origin. Below 15% → floor effect (BBEH problem). Above 35% → ceiling effect (no headroom for L1 lift to register against PoBB noise).
2. **Reachable ceiling.** Plausible **50–70%** under strong prompt engineering. The origin-to-ceiling gap is what L1 climbs; bigger gap = cleaner signal/noise.
3. **N ≥ 400, preferably 800+.** For stable cycle-to-cycle verdicts under PoBB (ε=0.05–0.10, n_min=4–6). Smaller N usable with per-subtask stratification.
4. **Multiple distinct subtask categories.** Each subtask is an independent L1 prompt lever (decomposition, scaffolding, role-priming, anti-shortcut framing, format pinning). Single-axis datasets give L1 only one knob to turn.
5. **Deterministic per-sample grading.** Exact match, MC, F1, regex extraction. **No LLM-judge scoring** (breaks PoBB's per-candidate independence model; cost-prohibitive at cycle scale).
6. **HF-loadable.** Single jsonl on Hugging Face = trivial loader. Custom scraper = real plumbing cost.
7. **Contamination-resistant.** 2024+ release preferred. Synthetic generation a plus.

## Headline ≠ focus

| Role | Dataset(s) | Why |
|---|---|---|
| **Headline benchmark** (M11 publication) | BBEH | Hardest reasoning benchmark, established competitor comparison, public leaderboards. |
| **Meta-campaign focus** (L1/L2/L3 iteration) | *under selection — see shortlist below* | Signal-dense, fast to verdict, cheap to iterate. Different job from headline. |
| **Connector validation** | TermNorm (lca-termnorm) | Per-connector regression, not optimizer iteration. |

When the optimizer matures enough that L1 prompts produce measurable lift on BBEH, the focus role collapses back into the headline role. Until then, they are separate jobs.

## Two parallel tracks

The shortlist splits across two L1 lever spaces that serve different jobs in the meta-campaign. They are not substitutes.

| Track | Question it answers | Why it matters |
|---|---|---|
| **A — Reasoning maturation** (toward BBEH) | Does L1 lift transfer across reasoning subtypes? Can L2 refine task_context to recover stalls? | Direct path to the BBEH headline. Lessons here transfer when we return to BBEH with a more mature optimizer. |
| **B — Instruction-following diagnostic** | Which prompt-edit *classes* can L1 reliably move? Where does the optimizer have leverage vs none? | We don't yet know which task types or which mutation classes are tractable for our optimizer. Per-constraint labeled failure modes turn the optimizer's competence map into observable data. Without this, every cycle outcome is over-interpreted — we can't tell "L1 found nothing useful to add" from "the dataset doesn't have the lever L1 reached for." |

Both tracks feed back into BBEH readiness. Track A improves the L1 prompt directly for reasoning; Track B characterizes *what L1 can do at all*, which determines how we read Track A's verdicts.

## Shortlist — under test (2026-05-18)

### Track B — Instruction-following / diagnostic

| Dataset | HF path | N | Origin band (20b-low, projected) | Ceiling (projected) | Subtask axes | Grading | Status |
|---|---|---|---|---|---|---|---|
| **IFBench** | `allenai/IFBench_test` | 300 | ~20–35% | ~70–85% | 15+ labeled constraint categories (`count:keywords_multiple`, `format:list`, `words:keywords_specific_position`, `ratio:sentence_words`, …) per sample | Per-constraint binary deterministic (rule-based checkers); aggregate as mean-pass-rate | **Wire FIRST.** Released 2025-12-23 by Ai2 — contamination-resistant. Each sample carries `instruction_id_list` + `kwargs` — critique receives named failure modes, not opaque 0/1. Diagnoses optimizer competence map. |

### Track A — Reasoning maturation (toward BBEH)

| Dataset | HF path | N | Origin band (20b-low, projected) | Ceiling (projected) | Subtask axes | Grading | Status |
|---|---|---|---|---|---|---|---|
| **MuSR** | `TAUR-Lab/MuSR` | 756 (250 + 256 + 250) | ~30–45% (avg) | ~75% (GPT-4o) | 3 — murder mysteries / object placements / team allocation | MC exact-match | **Primary reasoning focus — wire after IFBench.** |
| **OlympiadBench (en text)** | `Hothan/OlympiadBench` configs `OE_TO_maths_en_COMP` + `OE_TO_physics_en_COMP` | ~675 combined | ~18–28% | ~55–65% | 2 — math / physics | Numeric exact-match | **Math-axis option.** Wire after MuSR if MuSR's N=756 turns out noisy or if reasoning lessons need a numeric cross-check. |
| **FOLIO** | `yale-nlp/FOLIO` | ~1430 | ~30–45% | ~70% (GPT-4 w/ CoT 73.9%) | 1 — 3-class entailment | Label exact-match | **Fallback.** Largest N, single axis. Wire only if MuSR + OlympiadBench both disqualify. |

All four: deterministic grading, HF-loadable, no LLM judge required. Loaders + scorers TBD — scaffolding deferred to the trial day.

## Trial sequence

1. **IFBench** — first. Cheapest signal per LLM call (binary per-constraint, no judge), most labeled failure modes, smallest N keeps cycle cost low. **Output:** a per-constraint optimizer-competence map — which mutation classes L1 reliably moves, which it can't touch. Risk: N=300 is borderline → use per-constraint cohorts (~20 samples × 15 constraints) for stability rather than the aggregate.
2. **MuSR** — second. Once we know what L1 can actually do, run the primary reasoning focus. Read MuSR cycle outcomes through the IFBench-derived competence map: a stall on murder-mystery decomposition reads very differently if IFBench already showed L1 can't reliably inject decomposition cues.
3. **OlympiadBench** — third, or parallel to MuSR if budget allows. Numeric multi-step transfer check.
4. **FOLIO** — only if (1–3) leave a logic-entailment gap.

This sequence is operator-revisable any time — but the diagnostic-before-training ordering is the load-bearing claim: characterize the optimizer before relying on its verdicts.

## Why these four — the selection trail (2026-05-18)

Investigated in four rounds:

**Round 1 — "Goldilocks for 120b @ low" (wrong model, anchored too high).** Picked GPQA Diamond + MuSR. Rejected when operator corrected: target is 20b, not 120b.

**Round 2 — "Goldilocks for 20b @ low, origin 40–75%".** Picked MMLU-Pro (~63–67%) + MuSR (~45–60%). Rejected when operator pushed back: bigger headroom = cleaner signal/noise, want origin lower not higher.

**Round 3 — "20–30% origin, big ceiling-room, multi-subtask".** Picked OlympiadBench (math/physics) → operator asked for non-math. Re-scoped to MuSR (deductive/spatial/constraint reasoning) as primary, FOLIO as fallback. OlympiadBench kept as math-axis option.

**Round 4 — "signal content > N + ceiling-room"** (peer-review pushback from a second reviewer). Reframed: per-sample structured failure modes outrank raw accuracy resolution for L1 critique quality, because critique can only feed back what scoring lets it name. Added IFBench as a parallel Track B (diagnostic) for the optimizer-competence map. Reaffirmed Track A (reasoning) as the BBEH-readiness path. Rejected AA-LCR (LLM-judge cost, same disqualifier as GDPval-AA). Deferred AA-Omniscience pending floor check.

## Rejected candidates — one-line reasons

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
- **Tau-Bench** — needs tool-call infra. M12 territory.
- **AA-LCR** — LLM-judge per sample at cycle scale = budget-incompatible. Same disqualifier as GDPval-AA.
- **GDPval-AA** — pairwise Elo scoring (breaks PoBB), artifact outputs (docs/slides/diagrams — beyond `llm_only` node), agentic published scores (tool use, not raw completion). Right tool for benchmarking agents, wrong tool for L1 meta-prompt evolution.

## Deferred research

Candidates that may earn a shortlist slot pending an empirical check. Don't reject, don't wire — verify the open question first.

- **AA-Omniscience** (`ArtificialAnalysis/AA-Omniscience-Public`, N=600 public / 6000 full, 42 topics, released 2025-11). Asymmetric scoring (+correct / −hallucinated / 0 abstain) gives L2 a real second knob: a candidate that hallucinates 30% and one that abstains 30% score differently at identical correctness. **Open question — floor risk.** AA reports "even the best frontier models score only slightly above 0" on the Omniscience Index → gpt-oss-20b @ low is plausibly deep-negative on the index, but the calibration axis means lift can come from teaching abstention even without raising raw correctness. **Verify with a 50-sample reconnaissance slice** before committing a meta-campaign. If 20b can climb from negative to mildly positive via abstention prompts, promote to Track B (two-knob diagnostic — pairs naturally with IFBench). If it floors flat, reject.

## Update protocol

When a shortlisted dataset is trialed:
1. Record measured 20b-low origin + observed ceiling in the row above.
2. If it falls outside the projected band, move it to *Rejected* with the empirical reason.
3. If it works, leave it shortlisted — at any time multiple datasets can serve as meta-campaign focus, rotated per-cycle.
4. Once a dataset becomes the *primary* focus for a meta-campaign run, also update that campaign's NOTES.md (e.g. `.promptpotter/meta_campaigns/l1_generate/NOTES.md`) with the model + reference accuracy.

See also: [`dataset-reasoning-matrix.md`](dataset-reasoning-matrix.md) for per-dataset model defaults once a candidate graduates from shortlist to wired.
