# Benchmark Methodology

## PEvol-Bench — the AC-grade bench definition (v1 draft)

PromptPotter is an Algorithm Configuration solver in prompt space, and what this bench requires — canonical split, population-grade size, non-saturated or procgen — is what separates a credible AC benchmark from a method-comparison harness. The datasets we have actually run were chosen for headroom and head-to-head comparability against peer optimizers, not as PEvol-Bench-grade instances; which dataset is live and why is owned by [`../operations/dataset-selection-rationale.md`](../operations/dataset-selection-rationale.md).

Definition only; instance assembly TBD. PromptPotter is the reference solver.

- **Framing.** Algorithm Configuration (Hutter et al.) — an algorithm with a configuration space, searched for the best config; *per-instance* AC when configs adapt per input. Family: AutoML; closest classical relative HPO; in prompt space specifically, Automatic Prompt Optimization.
- **Requirements.** (1) **Pre-assembled canonical split — hard requirement** (else every paper compares on slightly different distributions and the field can't accumulate knowledge); (2) DSPy-style compound-system pipeline description; (3) population large enough for a real **config set** (what the algorithm searches over) / **test set** (held-out, same distribution, evaluates generalization) split. BBEH / AIME / GSM8K are too small and/or saturated — you can't meaningfully split 250 instances and claim population representativeness.
- **v1 candidates.** Procedurally generated tasks (unlimited test set) are the ideal. Curated picks: **MMLU-Pro** (~12k questions, diverse, harder than MMLU, unsaturated, canonical split) for breadth + **MATH** (7,500 test instances, clean baked-in split, understood difficulty distribution) for depth — both HuggingFace-native, no assembly required. **LiveBench** is the contamination-resistant watch item (monthly updates make a fixed test set harder).
- **Long-term node-type coverage.** LLM-only: MMLU-Pro, MATH, LiveBench · retrieval+LLM: HotpotQA, PopQA, FEVER · multi-step agent: GAIA, τ-bench · code pipeline: SWE-bench · long-context: LongBench, FRAMES. Aspiration: ship our own procedurally-generated instances; v1 sticks to curated existing datasets.

## Datasets

### BBEH (Big-Bench Extra Hard) — Primary

Successor to BBH from Google DeepMind. Replaces each of the 23 BBH tasks with a harder variant probing similar reasoning capabilities. Best general-purpose models score ~24%, reasoning-specialized ~54%. Ample headroom for prompt optimization to matter.

| Property | Value |
|----------|-------|
| Task | 23 diverse reasoning tasks |
| Split | Single (evaluation-only); we create train/test with seed=42 |
| Size | 4,520 full / 460 mini (20 per task) |
| Source | [BBEH/bbeh](https://huggingface.co/datasets/BBEH/bbeh) |
| Metrics | Exact Match (case-insensitive), macro-average across tasks |
| Format | `{"task": str, "input": str, "target": str, "mini": int}` |
| Paper | [arXiv:2502.19187](https://arxiv.org/abs/2502.19187) (ACL 2025) |
| Leaderboards | [Official (DeepMind, 11 models)](https://github.com/google-deepmind/bbeh/blob/main/leaderboard.md) · [Community (PricePerToken, broader coverage)](https://pricepertoken.com/leaderboards/benchmark/bbeh) |

**Head-to-head infrastructure:** [`bbeh-comparison/`](bbeh-comparison/) contains Colab notebooks (`bbeh_capo.ipynb`, `bbeh_dspy.ipynb`) running CAPO, GEPA, MIPROv2, and BootstrapFewShot against the identical `gpt-oss-120b` model and identical 10/task train + 10/task test split (seed=42). PromptPotter runs via its own CLI producing the same JSON output schema. This is the M11 primary benchmark.

**Optimization-loop model.** BBEH problem statements are verbose; running the optimization loop against `gpt-oss-120b` (Groq) trips the reasoning-budget ceiling on a non-trivial fraction of queries (`classify_result()` flags `llm_only:reasoning_budget_exhausted`; trace archived, candidate retried). The loop runs against `mistralai/mistral-small-3.2-24b-instruct` via OpenRouter at `reasoning_effort: low`. The head-to-head table uses the same model across all peer methods — the swap is a property of the BBEH dataset config, not a PromptPotter knob. See [`datasets/bbeh/dataset.md`](../../datasets/bbeh/dataset.md) and [`docs/operations/dataset-reasoning-matrix.md`](../operations/dataset-reasoning-matrix.md).

#### Reading BBEH results

The 23 BBEH tasks are **not ordered by difficulty** — numbering is organizational. Group by cognitive cluster when reading per-task results:

1. **Linguistic & Semantic** — synonyms, antonyms, word analogies. Modern models handle these with high accuracy.
2. **Logical & Mathematical Reasoning** — boolean logic, arithmetic, sequence completion. Moderate; spikes with larger numbers or longer logic chains.
3. **Commonsense & World Knowledge** — physical trajectories, social situations. High for small models — requires world modeling, not just text prediction.
4. **Algorithmic & Symbolic** — shuffled-object tracking (the "shell game"), complex grid navigation. Highest difficulty; biggest standard-vs-reasoning-model gap.

Difficulty is largely a function of model *scale* (a task at 0% can jump to 90% past a size threshold); the algorithmic/symbolic cluster is where reasoning-model gains and prompt-level headroom are largest.

### HotPotQA — Secondary (saturation probe pending)

Multi-hop question answering over Wikipedia paragraphs. Requires reasoning across multiple documents to produce a short answer.

| Property | Value |
|----------|-------|
| Task | Multi-hop QA |
| Split | Validation (distractor setting) |
| Size | 7,405 questions |
| Source | [Yang et al., 2018](https://hotpotqa.github.io/) |
| Metrics | Token F1, Exact Match (EM) |
| Format | `{"question": str, "answer": str, "context": list[list]}` |

Used in: MIPROv2, GEPA, adv-CoT. **Saturation status at `gpt-oss-120b`: unknown — probe scheduled in M11 Wave 1.**

Per-setting SOTA: the [HotpotQA homepage leaderboard](https://hotpotqa.github.io/). Headroom is tighter in the distractor setting than in fullwiki, because there the hard work — retrieval — is already done.

### GSM8K, AIME 2025 — Saturated, cited only

Both effectively saturated at `gpt-oss-120b` (2026-04-12). Cited in literature tables for context; no new runs planned. **GSM8K** — 1,319 grade-school math problems, [Cobbe et al., 2021](https://github.com/openai/grade-school-math), used by MIPROv2 / Promptomatix / adv-CoT. **AIME 2025** — 30 competition math problems, [MathArena/aime_2025](https://huggingface.co/datasets/MathArena/aime_2025), used by GEPA.

### Phase 2 (planned)

| Dataset | Task | Why |
|---------|------|-----|
| IFBench | Instruction following | Multi-criteria scoring, 2025 credibility signal |

---

## Evaluation Protocol

### Sample sizing for tuning vs. final numbers

Optimizer prompt evaluation and ablation tuning use **50–100 sample** runs to keep the tuning cost bounded — a single 100-sample × 5-variant × 10-round campaign is already ~5,000 backend evaluations, and tuning sweeps multiply that by the number of optimizer prompt variants under test. Final headline numbers in published tables use **200+ sample** runs for tighter CIs. The split is intentional: tuning is high-iteration, low-fidelity; final reporting is low-iteration, high-fidelity. Don't mix the two — small-sample tuning numbers should never appear in the main results table.

### Controlled variables

All methods evaluated under identical conditions:

| Variable | Value |
|----------|-------|
| Model | gpt-oss-120b (primary) |
| Temperature | 0.0 (deterministic) |
| Max tokens | Dataset-specific |
| Dataset split | Fixed per dataset (see above) |
| Eval sample | Full test/validation set for final numbers; `sample_size=200` during optimization |
| Seeds | n=3 independent optimization runs per method |

### Scoring

| Dataset | Primary Metric | Secondary | Scorer |
|---------|---------------|-----------|--------|
| BBEH | Exact Match (case-insensitive), macro-avg across 23 tasks | Per-task accuracy | `bbeh_match` |
| HotPotQA | Token F1 | Exact Match | Token-level precision/recall (planned: `F1Evaluator`) |
| GSM8K (deprioritized) | Exact Match | — | `gsm8k_match` |
| AIME 2025 (deprioritized) | Exact Match | — | `aime_match` |

## Origins

The bbeh-comparison harness is deliberately scoped to **prompt-optimization peers in the algorithm-configuration umbrella** (CAPO, GEPA, MIPROv2, BootstrapFewShot vs. PromptPotter). AlphaEvolve and OpenEvolve target source-code algorithms rather than prompts on a fixed pipeline. See [`related-work.md`](related-work.md)

| Method | Description | Source |
|--------|-------------|--------|
| Zero-shot | Raw question, no system prompt | Manual |
| Few-shot (manual) | Hand-crafted 3-shot examples | Manual |
| PromptPotter | LLM-driven program evolution | This work |
| AlphaEvolve | LLM-driven program evolution | [DeepMind, 2025](https://arxiv.org/abs/2506.13131) |
| OpenEvolve | Open re-implementation of AlphaEvolve | [repo](https://github.com/algorithmicsuperintelligence/openevolve) |
| DSPy Bootstrap | DSPy's bootstrap few-shot optimizer | [DSPy library](https://github.com/stanfordnlp/dspy) |
| MIPROv2 | DSPy's MIPRO v2 instruction + demo optimizer (cited) | [Opsahl-Ong et al., 2024](https://arxiv.org/abs/2406.11695) |
| GEPA | Reflective prompt evolution with trajectory feedback (cited) | [GEPA, 2025](https://github.com/stanfordnlp/dspy) |
| Promptomatix | Optimizer prompt + DSPy compiler, cost-aware (cited) | Salesforce, 2025 |
| adv-CoT | Adversarial generator-discriminator for reasoning (cited) | adv-CoT, 2025 |
| PromptWizard | Critique-guided prompt optimization (cited) | [Microsoft, 2024](https://arxiv.org/abs/2405.18369) |

---

## Infrastructure Notes

Wall-clock numbers in this document rely on prior-result reuse from `measurements/` (addressed by `PipelineSchema.node_configs`).

See [metrics.md](metrics.md) for the four-metric reporting convention (Acc, HC, SE, R₉₀) that complements absolute accuracy. See [related-work.md](related-work.md) for the algorithm-configuration umbrella, the feature matrices, the head-to-head numbers, and (§ Algorithm configuration: the classical lineage) the classical AutoML racing ancestry.
