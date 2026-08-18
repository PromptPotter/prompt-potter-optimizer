// The six PromptTemplate string fields — the decomposition scheme the optimizer
// evolves. Canonical MEMBERSHIP only — Python orders each prompt kind for itself
// (`PromptTemplate.RENDER_ORDER`), so the sequence below is this editor's grid, not a
// render order. MUST stay in sync with
// `PROMPT_STRING_FIELDS` in `promptpotter/config/settings.py` (the TS/Py seam).
//
// Single source for every webapp consumer: the `PromptFieldsEditor` grid and the
// observe read model (`searchPoint.ts`, which projects an optimizer prompt node's evolved
// fields out of its per-node resolved params). Do not re-list these keys elsewhere.

export const PROMPT_STRING_FIELDS = [
  "persona",
  "task_intent",
  "problem_description",
  "instruction",
  "thinking_style",
  "answer_format",
] as const;

// What each field is CALLED on screen. Here rather than in the editor because more
// than one surface names these now — the editor's grid and the run card's
// "changed vs origin" summary — and two spellings of one field's name is exactly the
// synonym this file exists to prevent. The editor's per-field hint and box height
// stay with the editor: those are authoring layout, not the field's name.
export const PROMPT_FIELD_LABEL: Record<string, string> = {
  persona: "Persona",
  task_intent: "Task intent",
  problem_description: "Problem",
  instruction: "Instructions",
  thinking_style: "Thinking style",
  answer_format: "Answer format",
  // Not decomposition fields, but they ride the same dict and a reader has to be
  // able to see them change.
  few_shot_examples: "Few-shot examples",
  plan: "Plan",
};

// A field the label table does not name (a backend that carries its own) still has
// to render as something a human can read.
export function promptFieldLabel(key: string): string {
  return PROMPT_FIELD_LABEL[key] ?? key.replace(/_/g, " ");
}

