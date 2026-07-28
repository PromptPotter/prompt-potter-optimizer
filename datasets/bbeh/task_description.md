# BBEH — Big-Bench Extra Hard

Solve one puzzle per task from the BIG-Bench Extra Hard benchmark and commit to a single final answer that matches a short gold string exactly.

## Domain

- Input: a self-contained reasoning puzzle from one of 23 BBEH subtasks (boardgame QA, logical deduction, causal reasoning, temporal reasoning, spatial reasoning, word problems, etc.)
- Output: one final answer — typically a single word, a short phrase, or a number
- Challenge: heterogeneous subtask formats, often requiring multi-step deductive reasoning against constraints stated in the problem

## Success criteria

- Exact Match: after stripping the last `**…**` bold span, the predicted answer equals the gold string (case-insensitive)
- All 23 subtasks share the same extraction convention: the grader reads only the last bolded span in the response
- Answers are short — the model must commit, not hedge

## Key failure modes

- `reasoning_budget_exhausted` (classified by PromptPotter from the backend's `content_empty` advisory + `finish_reason=length` + non-zero `reasoning_tokens`) on Groq reasoning models when `max_tokens` is set too low and the reasoning trace consumes the budget before the model emits visible content
- Wrong-span extraction when the model uses bold formatting inside its reasoning (extractor grabs the last bolded phrase, which is a reasoning step instead of the answer)
- Inventing new answer tokens when a subtask lists allowed values (e.g. `proved/disproved/unknown`) instead of picking one
- Over-long reasoning traces for subtasks that only need a single-word answer
- Under-committing — model hedges with "possibly X or Y" instead of emitting one final answer

## Notes

- 23 subtasks with heterogeneous answer shapes means one default prompt must accommodate integers, words, and short phrases
- `reasoning_effort` interacts strongly with `max_tokens` on Groq reasoning models — low `max_tokens` + medium/high `reasoning_effort` is the primary trap. Dataset default is `reasoning_effort: low` specifically to stay clear of it on Groq's smaller models (`gpt-oss-20b` enforces a ~2048-token output ceiling that the reasoning trace alone can exhaust). The optimizer can still mutate to higher effort settings when the model has headroom.

## Constraints

- The target model (`llm_only.model`) is pinned for this dataset and must not be proposed as a mutation by L1-generate. The single allowed value is `mistralai/mistral-small-3.2-24b-instruct` (see `datasets/bbeh/pipeline.yaml::available_models`). Provider is also pinned to `openrouter`.
- L1 may freely mutate: `reasoning_effort`, `temperature`, `max_tokens`, and any prompt field (`persona`, `task_intent`, `problem_description`, `instruction`, `thinking_style`, `answer_format`).
