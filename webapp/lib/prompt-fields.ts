import type { NodePrompt } from "@/lib/api/types";

interface PromptFieldMeta {
  label: string;
  hint?: string;
  rows?: number;
}

const PROMPT_FIELD: Record<string, PromptFieldMeta> = {
  persona: { label: "Persona", hint: "Who the model should act as", rows: 2 },
  task_intent: { label: "Task intent", hint: "The goal, in one line", rows: 2 },
  problem_description: { label: "Problem", hint: "What each input is", rows: 2 },
  instruction: { label: "Instructions", hint: "The directive the model follows", rows: 3 },
  thinking_style: { label: "Thinking style", hint: "How to reason before answering", rows: 2 },
  answer_format: { label: "Answer format", hint: "Exact shape of the output", rows: 2 },
  // Not decomposition fields, but they ride the same dict.
  shots: { label: "Shots" },
  plan: { label: "Plan" },
};

// A key the table does not name is labelled by its own words, with no hint.
export function promptField(key: string): PromptFieldMeta {
  return PROMPT_FIELD[key] ?? { label: key.replace(/_/g, " ") };
}

export function promptFieldLabel(key: string): string {
  return promptField(key).label;
}

// A draft's `origin_prompts` (fields per step) read as the individual's loci: a draft carries no shots.
export function draftPrompts(
  origin: Record<string, Record<string, string>>,
): Record<string, NodePrompt> {
  return Object.fromEntries(
    Object.entries(origin).map(([node, fields]) => [node, { fields, shots: [] }]),
  );
}
