// The six PromptTemplate string fields — the decomposition scheme the optimizer
// evolves. Canonical order + membership; MUST stay in sync with
// `PROMPT_STRING_FIELDS` in `promptpotter/config/settings.py` (the TS/Py seam).
//
// Single source for every webapp consumer: the `PromptFieldsEditor` grid and the
// observe read model (`searchPoint.ts`, which projects a meta-prompt node's evolved
// fields out of its per-node resolved params). Do not re-list these keys elsewhere.

export const PROMPT_STRING_FIELDS = [
  "persona",
  "task_intent",
  "problem_description",
  "instruction",
  "thinking_style",
  "answer_format",
] as const;

export type PromptStringField = (typeof PROMPT_STRING_FIELDS)[number];
