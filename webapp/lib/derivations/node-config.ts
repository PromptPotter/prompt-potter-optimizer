import {
  type CapabilityMenu,
  type DraftPatch,
  type ModelCapability,
  type NodeConfigParam,
  type OptimizerSetup,
  type ParamIntent,
  SCHEMA_DESCRIPTION_PREFIX,
} from "@/lib/api/types";

export type ConfigMode = "search-space" | "values";

// What a typed-in value is read as: the served row's kind and bounds; `nested` round-trips as JSON.
export type ValueType = Pick<
  NodeConfigParam,
  "kind" | "integer" | "nullable" | "minimum" | "exclusive_minimum" | "maximum" | "exclusive_maximum"
>;

function valueType(p: NodeConfigParam): ValueType {
  return {
    kind: p.kind,
    integer: p.integer,
    nullable: p.nullable,
    minimum: p.minimum,
    exclusive_minimum: p.exclusive_minimum,
    maximum: p.maximum,
    exclusive_maximum: p.exclusive_maximum,
  };
}

export interface ConfigRow extends ValueType {
  node: string;
  key: string;
  role: NodeConfigParam["role"];
  options: string[];
  value: string;
  baseValue: string;
  // Only a free-valued param carries this: an enumerable axis locks by `allowed.length <= 1`.
  locked: boolean;
  allowed: string[];
  // A transport fact (kept in the emitted seed even untouched), never provenance: that is `source`.
  inSeed: boolean;
  source: NodeConfigParam["source"];
  neverAxis: NodeConfigParam["never_axis"];
  movableBy: NodeConfigParam["movable_by"];
  held: boolean;
  // False: this account sets only what `permitted` names, nothing on a free-valued row.
  editable: boolean;
  permitted: string[];
  description: string;
}

export function pickedRoute(
  rows: ConfigRow[],
  menu: CapabilityMenu | undefined,
): { model: string; caps: ModelCapability | undefined } {
  const picked = rows.find((r) => r.kind === "model");
  const model = picked?.value ?? "";
  const provider = rows.find((r) => r.role === "provider" && r.node === picked?.node)?.value ?? "";
  return { model, caps: menu?.[provider]?.[model] };
}

// Display only: emitted as ticks, it would save the model's refusals as the campaign's choice.
export function effortLadder(row: ConfigRow, caps: ModelCapability | undefined): string[] {
  if (row.role !== "effort") return row.options;
  return caps?.reasoning_efforts ?? row.options;
}

function asObj(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function asRowValue(kind: string, value: unknown): string {
  if (value == null) return "";
  return kind === "nested" ? JSON.stringify(value, null, 1) : String(value);
}

export function parseNested(raw: string): unknown {
  if (raw.trim() === "") return "";
  try {
    return JSON.parse(raw);
  } catch {
    return undefined;
  }
}

// Off the served `params` (what each node runs, a reused dataset's own contract included), never
// `node_output_schema`, which is empty while the node answers as `text`.
export function authoredOutputSchema(
  overlay: Record<string, unknown>,
  node: string,
): Record<string, unknown> | undefined {
  const schema = asObj(asObj(overlay[node]).output_schema);
  return Object.keys(schema).length > 0 ? schema : undefined;
}

export function authoredAnswerFields(
  overlay: Record<string, unknown>,
  node: string,
): string[] | undefined {
  const fields = asObj(overlay[node]).answer_fields;
  return Array.isArray(fields) ? fields.filter((f): f is string => typeof f === "string") : undefined;
}

export function descriptionSubtree(keys: readonly string[], path: string): string[] {
  const key = SCHEMA_DESCRIPTION_PREFIX + path;
  return path === "" ? [...keys] : keys.filter((k) => k === key || k.startsWith(`${key}.`));
}

// `valuesSeed` is read in `values` mode only: search-space rows are served (`webapp/CLAUDE.md` I9).
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
      const baseValue = asRowValue(p.kind, p.value);
      if (mode === "search-space") {
        rows.push({
          ...valueType(p),
          node: n,
          key: p.key,
          role: p.role,
          options: p.options,
          value: baseValue,
          baseValue,
          locked: p.movable_by.length === 0,
          allowed: p.permitted,
          inSeed: false,
          source: p.source,
          neverAxis: p.never_axis,
          movableBy: p.movable_by,
          held: p.held,
          editable: p.editable,
          permitted: p.permitted,
          description: p.description,
        });
      } else {
        const inSeed = p.key in nodeSeed;
        const seedVal = inSeed ? nodeSeed[p.key] : p.value;
        rows.push({
          ...valueType(p),
          node: n,
          key: p.key,
          role: p.role,
          options: p.options,
          value: asRowValue(p.kind, seedVal),
          baseValue,
          locked: false,
          // The whole menu: a fork may be steered outside the permitted set, taking the grade-C taint.
          allowed: p.options,
          inSeed,
          source: p.source,
          neverAxis: p.never_axis,
          movableBy: p.movable_by,
          held: p.held,
          editable: p.editable,
          permitted: p.permitted,
          description: p.description,
        });
      }
    }
  }
  return rows;
}

export function rowIntents(rows: ConfigRow[]): ParamIntent[] {
  return rows.map((r) => ({ key: r.key, open: !r.locked, allowed: r.allowed }));
}

// Only the node's changed knobs: the server lays `config` over the draft's by its paths.
export function nodeOverlayPatch(node: string, rows: ConfigRow[]): DraftPatch {
  const config: Record<string, unknown> = {};
  for (const r of rows) {
    if (r.value !== r.baseValue && r.value !== "") {
      const value = parseParam(r, r.value);
      if (value !== undefined) config[r.key] = value;
    }
  }
  const narrowing = { node_narrowing: { [node]: rowIntents(rows) } };
  if (Object.keys(config).length === 0) return narrowing;
  return { config: { pipeline_overlay: { [node]: config } }, ...narrowing };
}

export function nodeLockPatch(
  schema: Record<string, NodeConfigParam[]> | null,
  node: string,
  keys: readonly string[],
  locked: boolean,
): DraftPatch {
  return nodeOverlayPatch(
    node,
    configRows(schema, {}, "search-space", node).map((r) =>
      keys.includes(r.key) ? { ...r, locked } : r,
    ),
  );
}

// The server's `flatten_sp_summary` spelling; mint it nowhere else.
export function flatConfigKey(node: string, param: string): string {
  return `${node}.${param}`;
}

// Diffed against the browser's own seed, never `SubjectReading.config`: its Python `str` reads a bool `"True"`.
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

export function seedOverlayFromRows(
  rows: ConfigRow[],
  edits: Record<string, string>,
): Record<string, Record<string, unknown>> {
  const overlay: Record<string, Record<string, unknown>> = {};
  for (const r of rows) {
    if (r.kind === "prompt") continue; // the fork's prompt rides `origin_prompts`, never config
    const edit = edits[flatConfigKey(r.node, r.key)];
    if (!r.inSeed && (edit === undefined || edit === r.value)) continue;
    const raw = edit ?? r.value;
    if (raw === "") continue;
    const value = parseParam(r, raw);
    if (value === undefined) continue;
    (overlay[r.node] ??= {})[r.key] = value;
  }
  return overlay;
}

export interface KnobGroup {
  node: string;
  kind: string;
  knobs: NodeConfigParam[];
}

// Each member's knobs as its `node_config_schema` rows, in the member's declared order.
export function knobGroups(setup: OptimizerSetup): KnobGroup[] {
  return setup.knobs.map(({ node, kind, keys }) => {
    const rows = setup.node_config_schema[node];
    const knobs = keys.flatMap((key) => {
      const row = rows?.find((r) => r.key === key);
      return row ? [row] : [];
    });
    return { node, kind, knobs };
  });
}

export function knobText(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function withinBounds(knob: ValueType, n: number): boolean {
  return (
    (knob.minimum === null || n >= knob.minimum) &&
    (knob.exclusive_minimum === null || n > knob.exclusive_minimum) &&
    (knob.maximum === null || n <= knob.maximum) &&
    (knob.exclusive_maximum === null || n < knob.exclusive_maximum)
  );
}

export function knobRange(knob: ValueType): string | null {
  const low = knob.exclusive_minimum ?? knob.minimum;
  const high = knob.exclusive_maximum ?? knob.maximum;
  if (low === null && high === null) return null;
  const open = knob.exclusive_minimum !== null || low === null ? "(" : "[";
  const close = knob.exclusive_maximum !== null || high === null ? ")" : "]";
  return `${open}${low ?? "−∞"}, ${high ?? "∞"}${close}`;
}

// The one reading of a typed-in value, by the served row's type: `undefined` is unrepresentable
// (off its bounds, a bad nested draft), which every emitter drops; a blank is null where legal.
export function parseParam(type: ValueType, text: string): unknown {
  const t = text.trim();
  if (type.kind === "number" || type.kind === "nested") {
    if (t === "") return type.nullable ? null : undefined;
    if (type.kind === "nested") return parseNested(t);
    const n = Number(t);
    if (!Number.isFinite(n) || (type.integer && !Number.isInteger(n))) return undefined;
    return withinBounds(type, n) ? n : undefined;
  }
  if (type.kind === "bool") return t === "true";
  return t === "" && type.nullable ? null : text;
}
