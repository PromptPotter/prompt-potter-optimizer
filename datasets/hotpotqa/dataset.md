# HotPotQA — Dataset Context

## Type

`llm-only` — pure LLM prompt optimization, no backend pipeline needed.

## Status

**Not yet implemented.** Two registry entries needed:

1. `DATASET_LOADERS["hotpotqa"]` in `dataset_builder.py` — fetch from HuggingFace, return `[{"query", "ground_truth"}]`
2. `SCORING_FUNCTIONS["hotpotqa_f1"]` in `scoring.py` — token-level F1 with SQuAD-style normalization

Everything else (adapter, prompt variants, scoring framework) is shared.

## Data

- Source: HotPotQA distractor setting (~113K questions, 10 passages each)
- Format: question + 10 context passages -> short answer
- campaign.json uses `eval_sample_size: 200` (subset for optimization)

## Pipeline Notes

- No backend nodes — just context + prompt -> LLM -> answer
- Optimization target: prompt template (retrieval instructions, reasoning chain, answer format)
