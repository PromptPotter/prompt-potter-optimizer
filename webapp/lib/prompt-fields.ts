// The PromptTemplate decomposition fields, and what each is CALLED on screen.
//
// The field SET is no longer written here — it is GENERATED from
// `config/settings.py::PROMPT_STRING_FIELDS` and re-exported below, so the "MUST stay in sync"
// note this file used to carry is a mechanism now instead of a hope. The LABELS stay, because
// they have no Python counterpart: nothing server-side names these for a human.

export { PROMPT_STRING_FIELDS } from "@/lib/api/types.generated";

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

