# Dataset Reasoning Matrix — Per-Dataset `pipeline.json` Defaults

Single canonical view of the model + reasoning_effort + max_tokens defaults shipped with each dataset's `pipeline.json`. Operators tune per-cycle via `campaign.json` overrides; this table is the **starting point**, not the only valid setting.

| Dataset | model (default) | `reasoning_effort` | `max_tokens` | Notes |
|---|---|---|---|---|
| `aime_2025` | `openai/gpt-oss-120b` | `high` | absent | Competition math; needs deep reasoning. High effort is load-bearing. |
| `gsm8k` | `openai/gpt-oss-120b` | `medium` | absent | Grade-school math word problems. Medium reasoning is enough. |
| `hotpotqa` | `openai/gpt-oss-120b` | `medium` | absent | Multi-hop QA. Medium reasoning. |
| `bbeh` | `openai/gpt-oss-20b` *(prod: 120b — see below)* | `low` | absent | "Big-Bench Extra Hard" puzzles. `low` is intentional — see Groq ceiling note below. |
| `lca-termnorm` | `openai/gpt-oss-120b` | n/a | absent (`null`) | Multi-node TermNorm pipeline; not a single-call reasoning dataset. |

`max_tokens` is **never** set as a numeric default in any dataset's `pipeline.json` node config — provider ceiling applies. Enforced by `tests/test_dataset_pipeline_defaults.py`.

## Groq daily-volume model swap

`openai/gpt-oss-120b` is the canonical default for all reasoning datasets. During development, when the operator's Groq daily volume on 120b is exhausted, swap the `model` field in the relevant `pipeline.json` to `openai/gpt-oss-20b` to keep iterating. Flip back to 120b for benchmarking / publication runs.

The current value committed in `datasets/bbeh/pipeline.json` may be either — treat the field as a **live operator knob**, not a fixed default.

## Why BBEH ships `reasoning_effort: low` (not `medium`/`high`)

Groq enforces a per-model output ceiling. On `gpt-oss-20b` it's ~2048 tokens. Reasoning-trace tokens are charged against this same budget on `openai/gpt-oss-*` models. With `reasoning_effort: medium` on a hard BBEH puzzle the model burns 8000+ chars of internal reasoning and runs out of budget before any visible content emerges — `finish_reason=length`, empty content, `classify_result()` stamps `llm_only:reasoning_budget_exhausted`, the result is deprecated and retried (which trips the same trap again).

`low` keeps a chain-of-thought benefit — BBEH genuinely needs reasoning — while staying clear of the budget trap on the small model. On the larger 120b the same `low` setting works fine.

If a hard subtask still trips on either model, the operator can:
1. Override per-cycle: `pipeline_overrides.llm_only.reasoning_effort = "none"` in `campaign.json`.
2. Or override `pipeline_overrides.llm_only.max_tokens = 8192` (or higher) — but this is model-specific tuning, not a dataset default.

See `datasets/bbeh/task_description.md` for the trap description and `promptpotter/application/optimization/elimination.py` for the `classify_result()` rule table.
