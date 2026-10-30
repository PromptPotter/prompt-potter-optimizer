import type { DraftPatch, NodePrompt } from "@/lib/api/types";
import type { NodeScope } from "@/lib/SelectionContext";

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
  shots: { label: "Worked examples", hint: "Rows of your data shown with their answers" },
  plan: { label: "Plan" },
};

// A key the table does not name is labelled by its own words, with no hint.
export function promptField(key: string): PromptFieldMeta {
  const meta = PROMPT_FIELD[key];
  if (meta === undefined) return { label: key.replace(/_/g, " ") };
  return meta;
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

// One node's fields after an edit, as the draft patch the check-in sends. A target edit REPLACES
// `origin_prompts` whole; an optimizer edit is the sparse `nodes.{node}.config.{field}` overlay a knob
// rides, carrying only the fields that moved.
export function promptFieldsPatch(
  scope: NodeScope,
  node: string,
  prompts: Record<string, NodePrompt>,
  fields: Record<string, string>,
): DraftPatch {
  if (scope === "optimizer") {
    const before = prompts[node];
    const config = Object.fromEntries(
      Object.entries(fields).filter(([k, v]) => before === undefined || before.fields[k] !== v),
    );
    return { optimization_overrides: { nodes: { [node]: { config } } } };
  }
  const next: Record<string, Record<string, string>> = {};
  for (const [name, p] of Object.entries(prompts)) next[name] = p.fields;
  next[node] = Object.fromEntries(Object.entries(fields).filter(([, v]) => v.trim() !== ""));
  return { origin_prompts: next };
}

// A snippet lands after what the field already says, never over it.
export function withSnippet(existing: string, snippet: string): string {
  return existing.trim() ? `${existing.trimEnd()}\n${snippet}` : snippet;
}
