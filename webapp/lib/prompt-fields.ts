import type { NodePrompt } from "@/lib/api/types";

const PROMPT_FIELD_LABEL: Record<string, string> = {
  persona: "Persona",
  task_intent: "Task intent",
  problem_description: "Problem",
  instruction: "Instructions",
  thinking_style: "Thinking style",
  answer_format: "Answer format",
  // Not decomposition fields, but they ride the same dict.
  shots: "Shots",
  plan: "Plan",
};

export function promptFieldLabel(key: string): string {
  return PROMPT_FIELD_LABEL[key] ?? key.replace(/_/g, " ");
}

// A draft's `origin_prompts` (fields per step) read as the individual's loci: a draft carries no shots.
export function draftPrompts(
  origin: Record<string, Record<string, string>>,
): Record<string, NodePrompt> {
  return Object.fromEntries(
    Object.entries(origin).map(([node, fields]) => [node, { fields, shots: [] }]),
  );
}
