# GSM8K — Dataset Context

## Type

`llm-only` — pure LLM prompt optimization, no backend pipeline needed.

## Status

**Ready.** Registry entries:
- `DATASET_LOADERS["gsm8k"]` — fetches from HuggingFace `openai/gsm8k`
- `SCORING_FUNCTIONS["gsm8k_match"]` — numeric extraction from `#### N` format

**Prerequisites:** `pip install datasets`, LLM API access configured.

## Init Flags

```
--backend-id gsm8k
--config configs/datasets/gsm8k/campaign.json
--skip-baseline
```

## Data

- Source: OpenAI GSM8K (grade school math, ~8.5K train / 1,319 test)
- Format: word problem -> numeric answer in `#### N` format
- campaign.json uses `eval_sample_size: 200`

## Pipeline Notes

- Prompt flows through `pipeline_params` via PromptTemplate — same path as any backend
- Optimization target: prompt template (chain-of-thought, few-shot examples, etc.)
- No per-node caching — every eval is a fresh LLM call
