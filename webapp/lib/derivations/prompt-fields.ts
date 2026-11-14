import {
  PROMPT_FIELD_INFO,
  type DraftPatch,
  type NodePrompt,
  type PromptFieldInfo,
} from "@/lib/api/types";
import type { NodeScope } from "@/lib/address";

// A key the served table does not name is labelled by its own words, with no hint.
export function promptField(key: string): PromptFieldInfo {
  const meta = PROMPT_FIELD_INFO[key];
  if (meta === undefined) return { label: key.replace(/_/g, " "), hint: null, rows: null };
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
// `origin_prompts` whole; an optimizer edit is the sparse `config.optimization.nodes.{node}.config.{field}` overlay a knob
// rides, carrying only the fields that moved.
export function promptFieldsPatch(
  scope: NodeScope,
  node: string,
  prompts: Record<string, NodePrompt>,
  fields: Record<string, string | null>,
): DraftPatch {
  if (scope === "optimizer") {
    const before = prompts[node];
    const config = Object.fromEntries(
      Object.entries(fields).filter(([k, v]) => before === undefined || before.fields[k] !== v),
    );
    return { config: { optimization: { nodes: { [node]: { config } } } } };
  }
  const next: Record<string, Record<string, string>> = {};
  for (const [name, p] of Object.entries(prompts)) next[name] = p.fields;
  next[node] = Object.fromEntries(
    Object.entries(fields).flatMap(([k, v]) => (v === null || v.trim() === "" ? [] : [[k, v]])),
  );
  return { origin_prompts: next };
}

// A snippet lands after what the field already says, never over it.
export function withSnippet(existing: string, snippet: string): string {
  return existing.trim() ? `${existing.trimEnd()}\n${snippet}` : snippet;
}
