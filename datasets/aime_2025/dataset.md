# AIME 2025 — Dataset Context

## Type

`backend` — uses the `llm_only` step for LLM-based evaluation.

## Prerequisites

- TermNorm backend must be running: `curl -s http://127.0.0.1:8000/status`
- If backend is down, tell the user: "Start the TermNorm backend first, then re-run `/potter-run`"

## Init Flags

```
--backend-url http://127.0.0.1:8000
--backend-id aime_2025
--config datasets/aime_2025/campaign.json
--skip-baseline
```

## Data

- Source: HuggingFace `HuggingFaceTB/aime_2025` (30 problems from AIME I and II 2025)
- Format: competition math problem -> integer answer in [0, 999]
- campaign.json uses `sp_budget_ttest: 0` (all 30 — dataset is tiny)

## Pipeline Notes

- Pipeline: `llm_only` step only — prompt flows through `pipeline_params` via PromptTemplate
- Optimization target: prompt template (reasoning strategy, verification steps, etc.)
- max_tokens: 4000 (competition math needs longer reasoning chains)
