# HotPotQA — Dataset Context

## Type

`llm-only` — pure LLM prompt optimization, no backend pipeline needed.

## Status

**Not yet implemented.** The HotPotQA dataset loader and LLM-only evaluation path are planned for post-M8.

What's needed:
- Dataset loader that fetches HotPotQA from HuggingFace or local cache
- LLM-only evaluation endpoint (context passages + question -> answer, no pipeline nodes)
- Token F1 + exact-match scorer

## Data

- Source: HotPotQA distractor setting (~113K questions, 10 passages each)
- Format: question + 10 context passages -> short answer
- campaign.json uses `sample_size: 200` (subset for optimization)

## Pipeline Notes

- No backend nodes — just context + prompt -> LLM -> answer
- Optimization target: prompt template (retrieval instructions, reasoning chain, answer format)
