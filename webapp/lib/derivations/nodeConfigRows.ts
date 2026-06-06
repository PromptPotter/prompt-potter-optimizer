// Node-config rows for the operator-steered fork's value editor. The steer
// panel lets the operator edit a fork's node-config VALUES — the flat
// `{node:{param:value}}` overlay that `OperatorForkOverride.pipeline_overlay` merges onto
// `session.pipeline_params` at bootstrap.
//
// Source is the server's `node_config_schema` (`GET /datasets/{name}/pipeline`)
// — the FULL operator-editable config per node (model/temperature/thinking/
// max_tokens), built from `pipeline.json::param_keys` minus the prompt fields.
// This is the operator's surface, NOT the optimizer's narrower `optimizer_locks`
// permutation subset: `model`/`provider` ARE editable here (the operator may set
// them on a steered fork via the seed overlay), just flagged `optimizerLocked`.
//
// Per param the `kind` drives the widget: `model`/`enum` → a <select> of
// `options`; `number`/`bool`/`string` → a typed input. The starting value is the
// candidate's overlay value if it set this param, else the node's current config
// value from the schema. `fromCandidate` marks the former so the emitted overlay
// stays sparse (candidate delta + operator edits only).

import type { NodeConfigParam } from "@/lib/api";

export interface NodeConfigRow {
  node: string;
  param: string;
  // "model" | "enum" | "number" | "bool" | "string" — drives the input widget.
  kind: string;
  // Closed choice set for model/enum kinds; empty otherwise.
  options: string[];
  // The seeded current value, stringified for the input. "" when neither the
  // candidate overlay nor the schema carries it (declared but unset, e.g.
  // max_tokens).
  value: string;
  // The param was present in the candidate's overlay (vs the config floor) —
  // keep it in the emitted overlay even when the operator leaves it.
  fromCandidate: boolean;
  // The optimizer is locked out of permuting this (model/provider under a strict
  // campaign). Shown as a hint; the operator may still set it.
  optimizerLocked: boolean;
  description: string;
}

function nodeOverlay(seedOverlay: Record<string, unknown>, node: string): Record<string, unknown> {
  const n = seedOverlay[node];
  return n && typeof n === "object" && !Array.isArray(n) ? (n as Record<string, unknown>) : {};
}

export function nodeConfigRows(
  schema: Record<string, NodeConfigParam[]> | null,
  seedOverlay: Record<string, unknown>,
): NodeConfigRow[] {
  if (!schema) return [];
  const rows: NodeConfigRow[] = [];
  for (const [node, params] of Object.entries(schema)) {
    const candCfg = nodeOverlay(seedOverlay, node);
    for (const p of params) {
      const fromCandidate = p.key in candCfg;
      const seedVal = fromCandidate ? candCfg[p.key] : p.value;
      rows.push({
        node,
        param: p.key,
        kind: p.kind,
        options: p.options,
        value: seedVal == null ? "" : String(seedVal),
        fromCandidate,
        optimizerLocked: p.optimizer_locked,
        description: p.description,
      });
    }
  }
  return rows;
}

// Build the sparse `{node:{param:value}}` overlay the fork is seeded with, from
// the rows + the operator's edited string values (keyed `"{node}.{param}"`). A
// param lands when it came from the candidate OR the operator changed it from
// the seeded value — inherited-untouched params stay out (they already live in
// `session.pipeline_params`). Values coerce by `kind`: number → Number, bool →
// the "true"/"false" select value; an unparseable number falls back to the raw
// string.
export function seedOverlayFromRows(
  rows: NodeConfigRow[],
  edits: Record<string, string>,
): Record<string, Record<string, unknown>> {
  const overlay: Record<string, Record<string, unknown>> = {};
  for (const r of rows) {
    const key = `${r.node}.${r.param}`;
    const edited = key in edits;
    const raw = edited ? edits[key] : r.value;
    if (!r.fromCandidate && (!edited || raw === r.value)) continue; // inherited + untouched
    if (raw === "") continue; // empty = drop (inherit)
    let value: unknown = raw;
    if (r.kind === "number") {
      const n = Number(raw);
      if (Number.isFinite(n)) value = n;
    } else if (r.kind === "bool") {
      value = raw === "true";
    }
    (overlay[r.node] ??= {})[r.param] = value;
  }
  return overlay;
}
