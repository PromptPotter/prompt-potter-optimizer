# GSM8K — Dataset Context

## Type

`backend` — uses the `llm_only` step for LLM-based evaluation.

## Init Flags

```
--backend-url http://127.0.0.1:8000
--backend-id gsm8k
--config datasets/gsm8k/campaign.json
--skip-baseline
```

## Data

- Source: OpenAI GSM8K (grade school math, ~8.5K train / 1,319 test)
- Format: word problem -> numeric answer in `#### N` format
- campaign.json uses `sp_budget_ttest: 30`

## Pipeline Notes

- Pipeline: `llm_only` step only — prompt flows through `pipeline_params` via PromptTemplate
- Optimization target: prompt template (chain-of-thought, few-shot examples, etc.)
