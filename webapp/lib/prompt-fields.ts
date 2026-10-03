// The field set is generated from `config/settings.py::PROMPT_STRING_FIELDS`; only the on-screen
// labels live here.

export { PROMPT_STRING_FIELDS } from "@/lib/api/types.generated";

// One label per field for every surface; the editor keeps only its authoring hints and heights.
export const PROMPT_FIELD_LABEL: Record<string, string> = {
  persona: "Persona",
  task_intent: "Task intent",
  problem_description: "Problem",
  instruction: "Instructions",
  thinking_style: "Thinking style",
  answer_format: "Answer format",
  // Not decomposition fields, but they ride the same dict.
  shot_ids: "Shots",
  plan: "Plan",
};

export function promptFieldLabel(key: string): string {
  return PROMPT_FIELD_LABEL[key] ?? key.replace(/_/g, " ");
}

