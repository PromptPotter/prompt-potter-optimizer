# PEvol-Bench — Benchmarks for Algorithm Configuration

**v1 draft (2026-04-29)** — definition only; instance assembly TBD. PromptPotter is the reference solver. Live trials log: [`benchmarks.md`](benchmarks.md).

## Framing

- **Algorithm Configuration (AC)** — Hutter et al.'s canonical term. An algorithm with a configuration space (prompts, parameters); search for the best config.
- More specifically **per-instance AC** when configs adapt per input.
- Family: **AutoML**; closest classical relative is **HPO (Hyperparameter Optimization)**.
- In prompt space specifically: **Automatic Prompt Optimization**.

PromptPotter is an AC solver where the configuration space is the prompt space.

## Requirements

- **Pre-assembled canonical split — hard requirement.** Otherwise every paper compares on slightly different distributions and the field can't accumulate knowledge.
- **DSPy-style compound-system pipeline description.**
- **Population large enough for a real config / test split:**
  - **Config set** — what the algorithm searches over.
  - **Test set** — held-out, same distribution, evaluates generalization of the found config.

BBEH / AIME / GSM8K are too small and/or saturated — you can't meaningfully split 250 instances and claim population representativeness.

## v1 candidates

What you actually want:

- Procedurally generated tasks where test set size is unlimited.
- **MMLU-Pro** — ~12k questions, HuggingFace, diverse domains, harder than MMLU, not yet saturated, canonical split, growing citation momentum.
- **MATH** — clean train/test baked in, 7,500 test instances, well-understood difficulty distribution.
- **LiveBench** — contamination-resistant by design (monthly updates); harder to pin a fixed test set.

**Recommendation:** MMLU-Pro for breadth + MATH for depth. Both HuggingFace-native, both with published baselines — no assembly required.

## Long-term node-type coverage

| Node types exercised | Datasets |
|---|---|
| LLM only | MMLU-Pro, MATH, LiveBench |
| Retrieval + LLM | HotpotQA, PopQA, FEVER |
| Multi-step agent | GAIA, τ-bench |
| Code pipeline | SWE-bench |
| Long-context / needle reasoning | LongBench, FRAMES (DeepMind) |

## Aspiration

Long-term: ship our own procedurally-generated instances. v1 sticks to curated existing datasets.
