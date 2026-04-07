# GSM8K — Dataset Context

## Type

`llm-only` — pure LLM prompt optimization, no backend pipeline needed.

## Status

**Not yet implemented.** The GSM8K dataset loader and LLM-only evaluation path are planned for post-M8.

What's needed:
- Dataset loader that fetches GSM8K from HuggingFace or local cache
- LLM-only evaluation endpoint (direct prompt -> answer, no pipeline nodes)
- Exact-match scorer that parses `#### N` format answers

## Data

- Source: OpenAI GSM8K (grade school math, ~8.5K problems)
- Format: word problem -> numeric answer
- campaign.json uses `sample_size: 200` (subset for optimization)

## Pipeline Notes

- No backend nodes — just prompt -> LLM -> answer
- Optimization target: prompt template only (chain-of-thought, few-shot examples, etc.)
