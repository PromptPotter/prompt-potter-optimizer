// Generated from `domain/search_point.py::PROMPT_STRING_FIELDS`.
export { PROMPT_STRING_FIELDS } from "@/lib/api/types.generated";

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

