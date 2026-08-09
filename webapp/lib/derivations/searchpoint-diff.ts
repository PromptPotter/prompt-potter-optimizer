// What this searchpoint CHANGED, relative to the origin the operator submitted.
//
// The answer a reader actually wants first is a list of NAMES — "task intent, model"
// — not two prompts to read side by side. So this returns the changed keys, and the
// full text stays one disclosure away.
//
// Pure over two already-served `ObserveConfig`s (`searchPoint.ts`). It compares
// values and invents none: no similarity score, no ranking, no "how much" — those
// would be numbers this app is not allowed to author. Equality is structural
// (`JSON.stringify` on a canonical key order), which is the honest test for values
// that may be strings, numbers, booleans or lists.

import { promptFieldLabel } from "@/lib/prompt-fields";
import type { ObserveConfig } from "./searchPoint";

export interface DiffGroup {
  // Which dict the names came from — the prompt, or the pipeline config.
  kind: "prompt" | "config";
  // The node they belong to, when they belong to one. `null` for the prompt, and for
  // the config's top-level entries (`steps`), which belong to no node: an empty string
  // there would be a third meaning smuggled into a name field.
  node: string | null;
  // The changed keys inside it, as display labels.
  names: string[];
}

// Structural equality over anything the two dicts can hold. `undefined` and a
// missing key are the same absence here: a param the origin never declared and one
// it declared as absent are not a change a reader can act on.
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

  // The config is `{node: {param: value}}` plus whatever top-level entries the
  // resolved shape carries (`steps`). A node present on one side only still reports
  // per-param, so "the candidate added a node" reads as its params rather than as
  // one opaque line. Grouping happens HERE, where the node key is already in hand —
  // the alternative is a summary line splitting `node.param` back apart.
  const out: DiffGroup[] = [];
  if (promptFields.length > 0) {
    out.push({ kind: "prompt", node: null, names: promptFields.map(promptFieldLabel) });
  }
  // Top-level entries (`steps`) belong to no node; they ride one unnamed group so a
  // reader never sees a node heading invented for them.
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
