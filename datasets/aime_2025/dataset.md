# AIME 2025 — Dataset Context

## Type

`backend` — uses the `llm_only` step for LLM-based evaluation.

## Init Flags

```
--backend-url http://127.0.0.1:8000
--backend-id aime_2025
--config datasets/aime_2025/campaign.json
--skip-baseline
```

## Data

- Source: HuggingFace `MathArena/aime_2025` (30 problems from AIME I and II 2025)
- Format: competition math problem -> integer answer in [0, 999]
- campaign.json uses `sp_budget_ttest: 20` (20 of 30 problems per eval round)

## Scoring

`aime_match(predicted, ground_truth)` — extracts answer from `\boxed{N}` (primary, standard math benchmark convention) or last number in text (fallback), then compares as integer. Binary 1.0/0.0. Matches MathArena evaluation methodology.

## Pipeline Notes

- Pipeline: `llm_only` step only — prompt flows through `pipeline_params` via PromptTemplate
- Optimization target: prompt template (reasoning strategy, verification steps, answer formatting)
- max_tokens: 4000 (competition math needs longer reasoning chains)
- Prompts should instruct the model to put the final answer in `\boxed{N}` format

