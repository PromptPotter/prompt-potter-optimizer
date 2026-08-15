# Benchmark Methodology

What we measure on, what it scored, and what we refuse to measure on. Model + `reasoning_effort`
pins → [`../operations/dataset-reasoning-matrix.md`](../operations/dataset-reasoning-matrix.md).
Selection criteria and the recon detail behind each verdict →
[`../operations/dataset-selection-rationale.md`](../operations/dataset-selection-rationale.md).
The BBEH head-to-head protocol →
[`bbeh-comparison/README.md`](bbeh-comparison/README.md). Peer systems and their published numbers →
[`related-work.md`](related-work.md).

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
- **The constant-answer floor is invisible to the bar.** On a 3-class set whose majority label holds
  40% of the bank, a pipeline that has stopped reading the input scores 40% and reads as a healthy
  in-band origin. Screen it *before* the score.

## Order of use

| # | Dataset | Role |
|---|---|---|
| 1 | **BBEH** | Headline benchmark — carries the head-to-head against peer optimizers. Measured 28% at the bar. |
| 2 | **HotpotQA** | Queued. Most valuable head-to-head addition after BBEH (MIPROv2/GEPA/adv-CoT all use it), but **not wired** — no loader, no `hotpotqa_f1` scorer. Unmeasured, and deliberately unprojected. |
| 3 | **AIME 2025** | In band at 30%, wired. Limited by size (30 problems, no split), not by headroom. |
| — | **`justlogic-d234`** | Not a publication benchmark — the **focus instrument**, where the optimizer's own behaviour is measured round over round. By far the most-measured dataset here. |
| — | **GSM8K** | Saturated (~78%). Retained for citation reproducibility only. |

BBEH is the headline but the wrong *iteration* target: at the bar every cycle ties at noise and PoBB
cannot separate candidates. That is why the focus role exists separately, and why it collapses back
into the headline role once L1 produces measurable lift on BBEH.

## Every dataset we measured

Default conditions unless noted: **`gpt-oss-20b @ low`, `temperature: 0.0`**, 25-sample slice. These
are *measurements*, not projections — the trail's one systemic finding is that literature anchors
from older proxies **underpredict `gpt-oss-20b @ low` by 10–20pp**, so bias every sub-20B-class prior
upward before trusting it.

| Dataset | Origin | Verdict |
|---|---|---|
| BBEH mini | **28%** | ✅ headline |
| `justlogic-d234` | **0.500–0.625** (floor 0.350) | ✅ focus instrument |
| AIME 2025 | **30%** | ✅ wired; too small to split |
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
| GSM8K | **~78%** (literature) | ❌ saturated |
| BBEH @ `high` | ~25% naïve | ❌ the *effort* is rejected, not the dataset — the reasoning trace exhausts the visible-token budget |
| IFBench · CRUXEval-O · MuSR (2-subtask) | — | ❌ desk-rejected / not measurable |

**Rejected without trial**, so they are not re-investigated: GPQA · MMLU-Pro · MATH-500 · HLE ·
ZebraLogic · ARC-AGI-2 · FrontierMath · SimpleBench · LiveBench · AGIEval-EN · DROP · ReClor ·
StrategyQA · ANLI R3 · LogiQA 2.0 · HumanEval/CRUX · IFEval · τ-bench · AA-LCR · GDPval-AA. The
recurring reasons are *above the bar*, *below the floor*, *unstable test set*, and *outside the
connector boundary or PoBB's cost model* (LLM-judge or tool-call dependent).

## Protocol

**Two reference models, two jobs.** `gpt-oss-20b @ low` is the admission bar and the focus-iteration
model — everything in the roster is scored there. The head-to-head target is held identical across
PromptPotter and every peer and is owned by [`bbeh-comparison/README.md`](bbeh-comparison/README.md).

**Tuning numbers and headline numbers do not mix.** Optimizer-prompt evaluation and ablation tuning
run at 50–100 samples to bound cost; a single 100-sample × 5-variant × 10-round campaign is already
~5,000 backend evaluations, and a sweep multiplies that by the variants under test. Headline numbers
use 200+ samples. Tuning is high-iteration/low-fidelity, reporting is low-iteration/high-fidelity —
**a small-sample tuning number must never appear in a main results table.**

Per-dataset scorers are declared in each `datasets/{name}/campaign.yaml::scoring`; the answer-format
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
  monthly refreshes invalidate cross-cycle comparisons mid-campaign.
- **Long-term node-type coverage.** LLM-only: MMLU-Pro, MATH, LiveBench · retrieval+LLM: HotpotQA,
  PopQA, FEVER · multi-step agent: GAIA, τ-bench · code pipeline: SWE-bench · long-context: LongBench,
  FRAMES. Aspiration: ship our own procedurally-generated instances.

See [`metrics.md`](metrics.md) for the four-metric convention (Acc, HC, SE, R₉₀) that complements
absolute accuracy.
