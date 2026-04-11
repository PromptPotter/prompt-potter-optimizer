# HotPotQA — Dataset Context

## Type

`backend` — uses the `llm_only` step for LLM-based evaluation.

## Status

**Not yet implemented.** Two registry entries needed:

1. `DATASET_LOADERS["hotpotqa"]` in `dataset_builder.py` — fetch from HuggingFace, return `[{"query", "ground_truth"}]`
2. `SCORING_FUNCTIONS["hotpotqa_f1"]` in `scoring.py` — token-level F1 with SQuAD-style normalization

Everything else (prompt variants, scoring framework) is shared.

## Prerequisites

- Pipeline backend must be running: `curl -s http://127.0.0.1:8000/status`

## Init Flags

```
--backend-url http://127.0.0.1:8000
--backend-id hotpotqa
--config datasets/hotpotqa/campaign.json
--skip-baseline
```

## Data

- Source: HotPotQA distractor setting (~113K questions, 10 passages each)
- Format: question + 10 context passages -> short answer
- campaign.json uses `sp_budget_ttest: 200` (subset for optimization)

## Pipeline Notes

- Pipeline: `llm_only` step only — context + prompt -> LLM -> answer
- Optimization target: prompt template (retrieval instructions, reasoning chain, answer format)
