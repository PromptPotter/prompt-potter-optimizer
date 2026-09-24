// The one node-config row model and its two emitters, one per transport: search-space → the draft
// `pipeline_overlay` (split at mint by `launcher.split_overlay`); values → `OperatorForkOverride.pipeline_overlay`.

import type { DraftPatch, ModelCapability, NodeConfigParam } from "@/lib/api";
import type { NodeSearchNarrowing } from "@/lib/api/types";

export type ConfigMode = "search-space" | "values";

export interface ConfigRow {
  node: string;
  key: string;
  // "model" | "enum" | "number" | "bool" | "string" | "nested" | "prompt"; `nested` round-trips as JSON.
  kind: string;
  options: string[];
  // "" when neither the overlay nor the schema carries it (declared but unset).
  value: string;
  baseValue: string;
  // Only a free-valued param carries this: an enumerable axis locks by `allowed.length <= 1`.
  locked: boolean;
  // Also what a human fork may steer `model` to without the grade-C taint.
  allowed: string[];
  // The server stated `permitted`, so the emit writes the set even where it equals the menu.
  stated: boolean;
  // A transport fact (keep in the emitted seed even untouched), never provenance: that is `source`.
  inSeed: boolean;
  source: NodeConfigParam["source"];
  // Served, "" where none: the key's name alone cannot tell which construction forbids the axis.
  neverAxis: NodeConfigParam["never_axis"];
  movableBy: string[];
  // The dataset offered this axis and THIS campaign closed it — the only shut state a person caused.
  held: boolean;
  description: string;
}

/** Display only, never the engine's `param_options` resolve: the ticks stay the operator's, or a
 *  repaint saves the model's refusals as the campaign's choice. Unknown model → the node's list. */
export function effortLadder(row: ConfigRow, caps: ModelCapability | undefined): string[] {
  if (row.key !== "reasoning_effort") return row.options;
  return caps?.reasoning_efforts ?? row.options;
}

/** What the editor may OFFER only. The babysit verdict and the models a warning names come from
 *  `POST /campaigns/{id}/fork-preview`, never from this. */
export function permittedModels(
  schema: Record<string, NodeConfigParam[]> | null | undefined,
): Record<string, readonly string[]> {
  const out: Record<string, readonly string[]> = {};
  for (const [node, params] of Object.entries(schema ?? {})) {
    const row = params.find((p) => p.key === "model");
    if (row) out[node] = row.permitted ?? row.options;
  }
  return out;
}

export function agentLabel(agent: string): string {
  return agent === "l1"
    ? "the generator, every round"
    : agent === "l2"
      ? "escalation, when a round stalls"
      : agent;
}

function asObj(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function asRowValue(kind: string, value: unknown): string {
  if (value == null) return "";
  return kind === "nested" ? JSON.stringify(value, null, 1) : String(value);
}

// `undefined` = unrepresentable (a bad nested draft), which the emitters drop; `""` = inherit.
function coerce(kind: string, raw: string): unknown {
  if (kind === "number") {
    const n = Number(raw);
    return Number.isFinite(n) ? n : raw;
  }
  if (kind === "bool") return raw === "true";
  if (kind === "nested") return parseNested(raw);
  return raw;
}

/** Exported so the widget's commit gate and the emitters share one "is this valid JSON". */
export function parseNested(raw: string): unknown {
  if (raw.trim() === "") return "";
  try {
    return JSON.parse(raw);
  } catch {
    return undefined;
  }
}

/** The draft's own overlay, never the served resolution, which is empty while the node is `text`. */
export function authoredOutputSchema(
  overlay: Record<string, unknown>,
  node: string,
): Record<string, unknown> | undefined {
  const schema = asObj(asObj(asObj(overlay[node]).config).output_schema);
  return Object.keys(schema).length > 0 ? schema : undefined;
}

export function authoredAnswerField(
  overlay: Record<string, unknown>,
  node: string,
): string | undefined {
  const field = asObj(asObj(overlay[node]).config).answer_field;
  return typeof field === "string" ? field : undefined;
}

/** Mirrors `domain/pipeline_schema.py::SCHEMA_DESCRIPTION_PREFIX`. */
export const DESCRIPTION_PREFIX = "output_schema_descriptions.";

/** This click IS the lock's inheritance: nothing else writes these keys, so a descendant unlocked
 *  afterwards holds for its own subtree. */
export function descriptionSubtree(keys: readonly string[], path: string): string[] {
  const key = DESCRIPTION_PREFIX + path;
  return path === "" ? [...keys] : keys.filter((k) => k === key || k.startsWith(`${key}.`));
}

/** The answer slot falls back to the LAST field: fields generate in order, reasoning first. A first
 *  schema re-opens `response_format`, which a pre-schema narrowing could only tick `text`. */
export function nodeSchemaPatch(
  base: Record<string, unknown>,
  node: string,
  schema: Record<string, unknown>,
  answer?: string,
): DraftPatch {
  const overlay = JSON.parse(JSON.stringify(base)) as Record<string, Record<string, unknown>>;
  const prev = asObj(overlay[node]);
  const fields = Object.keys(asObj(schema.properties));
  const first = authoredOutputSchema(base, node) === undefined;
  const chosen = [answer, authoredAnswerField(base, node)].find(
    (f) => f !== undefined && fields.includes(f),
  );
  const optimizer = { ...asObj(prev.optimizer) };
  if (prev.optimizer !== undefined && first) {
    const allowed = { ...asObj(optimizer.param_allowed_values) };
    delete allowed.response_format;
    optimizer.param_allowed_values = allowed;
    if (Array.isArray(optimizer.param_keys)) {
      optimizer.param_keys = [...new Set([...optimizer.param_keys, "response_format"])];
    }
  }
  overlay[node] = {
    ...prev,
    ...(prev.optimizer === undefined ? {} : { optimizer }),
    config: {
      ...asObj(prev.config),
      output_schema: schema,
      answer_field: chosen ?? (fields.includes("answer") ? "answer" : fields[fields.length - 1]),
      ...(first ? { response_format: "json" } : {}),
    },
  };
  return { pipeline_overlay: overlay };
}

// `valuesSeed` is read in `values` mode only: search-space rows are served, and re-deriving them
// from a client overlay breaks `frontend-surface-contract.md::I9`.
export function configRows(
  schema: Record<string, NodeConfigParam[]> | null,
  valuesSeed: Record<string, unknown>,
  mode: ConfigMode,
  node?: string,
): ConfigRow[] {
  if (!schema) return [];
  const scoped = node != null ? schema[node] : undefined;
  const entries: [string, NodeConfigParam[]][] =
    node != null ? (scoped ? [[node, scoped]] : []) : Object.entries(schema);
  const rows: ConfigRow[] = [];
  for (const [n, params] of entries) {
    const nodeSeed = asObj(valuesSeed[n]);
    for (const p of params) {
      // Nothing subtracted: a `prompt` field's lock is a `param_keys` membership, so every emit lists it.
      const baseValue = asRowValue(p.kind, p.value);
      // `null` = same as the menu; `[]` = nothing may be picked. `??` keeps them apart.
      const permitted = p.permitted ?? p.options;
      if (mode === "search-space") {
        rows.push({
          node: n,
          key: p.key,
          kind: p.kind,
          options: p.options,
          value: baseValue,
          baseValue,
          locked: p.movable_by.length === 0,
          allowed: permitted,
          stated: p.permitted !== null,
          inSeed: false,
          source: p.source,
          neverAxis: p.never_axis,
          movableBy: p.movable_by,
          held: p.held,
          description: p.description,
        });
      } else {
        const inSeed = p.key in nodeSeed;
        const seedVal = inSeed ? nodeSeed[p.key] : p.value;
        rows.push({
          node: n,
          key: p.key,
          kind: p.kind,
          options: p.options,
          value: asRowValue(p.kind, seedVal),
          baseValue,
          locked: false,
          // The whole menu: a fork may be steered outside the permitted set, taking the grade-C taint.
          allowed: p.options,
          stated: false,
          inSeed,
          source: p.source,
          neverAxis: p.never_axis,
          movableBy: p.movable_by,
          held: p.held,
          description: p.description,
        });
      }
    }
  }
  return rows;
}

/** DIFFERS, never "is a subset": an operator may widen an axis, and `PipelineSchema.narrow`
 *  replaces `param_allowed_values`, so the widened list survives the mint. */
function sameMembers(a: readonly string[], b: readonly string[]): boolean {
  if (a.length !== b.length) return false;
  const set = new Set(b);
  return a.every((v) => set.has(v));
}

/** An absent `param_allowed_values` entry resolves to the DECLARATION. The draft wraps this in a
 *  `DraftPatch`; the steer fork sends it bare as `OperatorForkOverride.optimizer_narrowing`. */
export function nodeNarrowing(rows: ConfigRow[]): NodeSearchNarrowing {
  const paramKeys: string[] = [];
  const allowedValues: Record<string, string[]> = {};
  for (const r of rows) {
    const enumerable = r.kind === "enum" || r.kind === "model";
    const locked = enumerable ? r.allowed.length <= 1 : r.locked;
    if (!locked) paramKeys.push(r.key);
    if (enumerable && r.allowed.length > 0 && (r.stated || !sameMembers(r.allowed, r.options))) {
      // Written even when pinned: it is also what a human fork may steer to un-tainted.
      allowedValues[r.key] = r.allowed;
    }
  }
  return { param_keys: paramKeys, param_allowed_values: allowedValues };
}

export function nodeOverlayPatch(
  base: Record<string, unknown>,
  node: string,
  rows: ConfigRow[],
): DraftPatch {
  const overlay = JSON.parse(JSON.stringify(base)) as Record<string, Record<string, unknown>>;
  const config: Record<string, unknown> = {};
  for (const r of rows) {
    if (r.value !== r.baseValue && r.value !== "") {
      const value = coerce(r.kind, r.value);
      if (value !== undefined) config[r.key] = value;
    }
  }

  const prev = (overlay[node] ?? {}) as Record<string, unknown>;
  const prevConfig = (prev.config ?? {}) as Record<string, unknown>;
  overlay[node] = {
    ...prev,
    optimizer: nodeNarrowing(rows),
    ...(Object.keys(config).length > 0 ? { config: { ...prevConfig, ...config } } : {}),
  };
  return { pipeline_overlay: overlay };
}

/** The one lock emitter for a surface outside the grid, through the grid's own emitter. */
export function nodeLockPatch(
  schema: Record<string, NodeConfigParam[]> | null,
  overlay: Record<string, unknown>,
  node: string,
  keys: readonly string[],
  locked: boolean,
): DraftPatch {
  return nodeOverlayPatch(
    overlay,
    node,
    configRows(schema, overlay, "search-space", node).map((r) =>
      keys.includes(r.key) ? { ...r, locked } : r,
    ),
  );
}

/** The server's `flatten_sp_summary` spelling; mint it nowhere else. */
export function flatConfigKey(node: string, param: string): string {
  return `${node}.${param}`;
}

// The emission is the WHOLE running config, not a diff. Compare against the browser's own seed,
// never `SubjectReading.config`, whose Python `str` reads a bool `"True"`.
export function overlayEdits(
  emitted: Record<string, Record<string, unknown>>,
  seed: Record<string, unknown>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [node, params] of Object.entries(emitted)) {
    const seeded = asObj(seed[node]);
    for (const [param, value] of Object.entries(params)) {
      const was = param in seeded ? String(seeded[param]) : "";
      if (String(value) !== was) out[flatConfigKey(node, param)] = String(value);
    }
  }
  // A seeded param the emission dropped was cleared to "inherit" — still an edit.
  for (const [node, params] of Object.entries(seed)) {
    const seeded = asObj(params);
    for (const param of Object.keys(seeded)) {
      if (!(param in (emitted[node] ?? {}))) out[flatConfigKey(node, param)] = "";
    }
  }
  return out;
}

/** `NodeConfigEditor` drops its draft when the seed changes, so clearing an edit restores the input. */
export function applyFlatEdits(
  seed: Record<string, unknown>,
  flat: ReadonlyMap<string, string>,
): Record<string, unknown> {
  if (flat.size === 0) return seed;
  const out: Record<string, unknown> = { ...seed };
  for (const [key, value] of flat) {
    const cut = key.indexOf(".");
    if (cut <= 0) continue;
    const node = key.slice(0, cut);
    const param = key.slice(cut + 1);
    out[node] = { ...asObj(out[node]), [param]: value };
  }
  return out;
}

// Inherited-untouched params stay out: they already live in `pipeline_params`.
export function seedOverlayFromRows(
  rows: ConfigRow[],
  edits: Record<string, string>,
): Record<string, Record<string, unknown>> {
  const overlay: Record<string, Record<string, unknown>> = {};
  for (const r of rows) {
    if (r.kind === "prompt") continue; // the fork's prompt rides `origin_prompt_fields`, never config
    const edit = edits[flatConfigKey(r.node, r.key)];
    if (!r.inSeed && (edit === undefined || edit === r.value)) continue;
    const raw = edit ?? r.value;
    if (raw === "") continue; // "" = inherit
    const value = coerce(r.kind, raw);
    if (value === undefined) continue;
    (overlay[r.node] ??= {})[r.key] = value;
  }
  return overlay;
}
