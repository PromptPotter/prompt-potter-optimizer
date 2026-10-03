// What a searchpoint CHANGED against the submitted origin, as a list of NAMES. Compares served
// values and invents none — no similarity score, no ranking, no "how much".

import { promptFieldLabel } from "@/lib/prompt-fields";
import type { ObserveConfig } from "./searchPoint";

export interface DiffGroup {
  kind: "prompt" | "config";
  // `null` for the prompt and for top-level config entries (`steps`), which belong to no node.
  node: string | null;
  names: string[];
}

// A missing key and a null value are one absence — not a change a reader can act on.
function same(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a == null && b == null) return true;
  if (a == null || b == null) return false;
  return JSON.stringify(a) === JSON.stringify(b);
}

function keysOf(...objs: (Record<string, unknown> | undefined)[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const o of objs) {
    for (const k of Object.keys(o ?? {})) {
      if (seen.has(k)) continue;
      seen.add(k);
      out.push(k);
    }
  }
  return out;
}

function asObj(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

export function searchPointDiff(
  origin: ObserveConfig | null,
  shown: ObserveConfig | null,
): DiffGroup[] {
  if (!origin || !shown) return [];

  const promptFields = keysOf(origin.promptFields, shown.promptFields).filter(
    (k) => !same(origin.promptFields[k], shown.promptFields[k]),
  );

  // A node present on one side only still reports per-param, never as one opaque line.
  const out: DiffGroup[] = [];
  if (promptFields.length > 0) {
    out.push({ kind: "prompt", node: null, names: promptFields.map(promptFieldLabel) });
  }
  const loose: string[] = [];
  for (const nodeKey of keysOf(origin.config, shown.config)) {
    const a = asObj(origin.config[nodeKey]);
    const b = asObj(shown.config[nodeKey]);
    if (!a && !b) {
      if (!same(origin.config[nodeKey], shown.config[nodeKey])) loose.push(nodeKey);
      continue;
    }
    const names = keysOf(a ?? {}, b ?? {}).filter((param) => !same(a?.[param], b?.[param]));
    if (names.length > 0) out.push({ kind: "config", node: nodeKey, names });
  }
  if (loose.length > 0) out.push({ kind: "config", node: null, names: loose });
  return out;
}
