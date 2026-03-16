# api/models — Data Models

## PromptState (`prompt_state.py`)

Immutable prompt configuration organized into 3 optimization layers:

- **Layer 1 (Generate)**: persona, task_intent, thinking_style, answer_format, etc. — change every pass
- **Layer 2 (Refine Context)**: context, parameters — adjust when Layer 1 stalls
- **Layer 3 (Modify Plan)**: plan — rarely changed (strategy defaults)
- **Layer 4 (Meta-Optimize)**: the optimizer's own prompts/params — it's just another pipeline. Only pays off with lots of campaign data.

See [`../services/CLAUDE.md`](../services/CLAUDE.md) for store layout and evaluation flow.
