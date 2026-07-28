# AIME 2025 — Dataset Context

## Status

Routed through TermNorm `/matches` with the `llm_only` pipeline (M9 LLM-Only
Unification, 2026-04-14). Requires a TermNorm instance with the `llm_only`
node enabled.

## Init Flags

```
--backend-url http://127.0.0.1:8000
--backend-id aime_2025
--dataset-name aime_2025
--config datasets/aime_2025/campaign.json
```

## Data

- Source: HuggingFace `MathArena/aime_2025` (30 problems from AIME I and II 2025)
- Format: competition math problem → integer answer in [0, 999]
- `campaign.json` uses `sp_budget_ttest: 20` (20 of 30 problems per eval round)

## Scoring

`aime_match(predicted, ground_truth)` — extracts answer from `\boxed{N}`
(primary, standard math benchmark convention) or last number in text
(fallback), then compares as integer. Matches MathArena evaluation
methodology. Registered in
`promptpotter/shared/scoring.py::SCORING_FUNCTIONS`.

## Pipeline Notes

- Single `llm_only` node — prompt flows through `pipeline_params.llm_only.prompt`
- Default target: `openrouter:mistralai/mistral-small-3.2-24b-instruct`
  (fast, no reasoning_effort knob). Switch to `groq:openai/gpt-oss-120b`
  with `reasoning_effort: "high"` when chasing accuracy on harder problems
- Optimization target: prompt template (reasoning strategy, verification
  steps, answer formatting), `temperature`
- Prompts should instruct the model to put the final answer in `\boxed{N}` format
- `max_tokens` override at 16384 in `campaign.yaml::pipeline_overrides.llm_only`:
  Groq's gpt-oss-120b default output ceiling (~3072 tokens) truncates
  AIME-difficulty reasoning chains, causing `finish_reason=length` deprecations
  on the same handful of problems across every candidate (sample #8 parabola,
  #10 piecewise linear, #6 twelve-letter combinatorics). Raise the cap so
  deeper-reasoning candidates can actually finish.
