# GSM8K — Dataset Context

## Status

Routed through TermNorm `/matches` with the `llm_only` pipeline (M9 LLM-Only
Unification, 2026-04-14). Requires a TermNorm instance with the `llm_only`
node enabled.

## Init Flags

```
--backend-url http://127.0.0.1:8000
--backend-id gsm8k
--dataset-name gsm8k
--config datasets/gsm8k/campaign.yaml
```

## Data

- Source: OpenAI GSM8K (grade school math, ~8.5K train / 1,319 test)
- Format: word problem → numeric answer in `#### N` format
- `campaign.yaml` uses `sp_budget_ttest: 30`

## Scoring

`gsm8k_match(predicted, ground_truth)` — extracts `#### N` or last number and
compares as float. Registered in
`promptpotter/application/scoring/formula/matchers.py::SCORING_FUNCTIONS`.

## Pipeline Notes

- Single `llm_only` node — prompt flows through `pipeline_params.llm_only.prompt`
- `max_tokens: 16000`, `reasoning_effort: "medium"` for reasoning-model
  compatibility on `gpt-oss-120b`
- Optimization target: prompt template (chain-of-thought, few-shot examples),
  `reasoning_effort`, `temperature`
