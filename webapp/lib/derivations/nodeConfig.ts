import type {
  CapabilityMenu,
  DraftPatch,
  KnobRow,
  ModelCapability,
  NodeConfigParam,
} from "@/lib/api";
import type { ManifestNodeOverlay, ParamIntent } from "@/lib/api/types";
import { SCHEMA_DESCRIPTION_PREFIX } from "@/lib/api/types.generated";

export type ConfigMode = "search-space" | "values";

export interface ConfigRow {
  node: string;
  key: string;
  // "model" | "enum" | "number" | "bool" | "string" | "nested" | "prompt"; `nested` round-trips as JSON.
  kind: string;
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
  description: string;
}

export function pickedRoute(
  rows: ConfigRow[],
  menu: CapabilityMenu | undefined,
): { model: string; caps: ModelCapability | undefined } {
  const picked = rows.find((r) => r.kind === "model");
  const model = picked?.value ?? "";
  const provider = rows.find((r) => r.key === "provider" && r.node === picked?.node)?.value ?? "";
  return { model, caps: menu?.[provider]?.[model] };
}

// Display only: emitted as ticks, it would save the model's refusals as the campaign's choice.
export function effortLadder(row: ConfigRow, caps: ModelCapability | undefined): string[] {
  if (row.key !== "reasoning_effort") return row.options;
  return caps?.reasoning_efforts ?? row.options;
}

// Only what the editor may OFFER; the verdict is `POST /campaigns/{id}/fork-preview`'s.
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

export function parseNested(raw: string): unknown {
  if (raw.trim() === "") return "";
  try {
    return JSON.parse(raw);
  } catch {
    return undefined;
  }
}

// The draft's own overlay, never the served resolution, which is empty while the node is `text`.
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

export function descriptionSubtree(keys: readonly string[], path: string): string[] {
  const key = SCHEMA_DESCRIPTION_PREFIX + path;
  return path === "" ? [...keys] : keys.filter((k) => k === key || k.startsWith(`${key}.`));
}

// `valuesSeed` is read in `values` mode only: search-space rows are served (`frontend-surface-contract.md::I9`).
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

export function rowIntents(rows: ConfigRow[]): ParamIntent[] {
  return rows.map((r) => ({ key: r.key, open: !r.locked, allowed: r.allowed }));
}

export function nodeOverlayPatch(
  base: Record<string, unknown>,
  node: string,
  rows: ConfigRow[],
): DraftPatch {
  const config: Record<string, unknown> = {};
  for (const r of rows) {
    if (r.value !== r.baseValue && r.value !== "") {
      const value = coerce(r.kind, r.value);
      if (value !== undefined) config[r.key] = value;
    }
  }
  const narrowing = { node_narrowing: { [node]: rowIntents(rows) } };
  if (Object.keys(config).length === 0) return narrowing;
  const prev = asObj(base[node]);
  return {
    pipeline_overlay: { ...base, [node]: { ...prev, config: { ...asObj(prev.config), ...config } } },
    ...narrowing,
  };
}

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
    if (r.kind === "prompt") continue; // the fork's prompt rides `origin_prompt_fields`, never config
    const edit = edits[flatConfigKey(r.node, r.key)];
    if (!r.inSeed && (edit === undefined || edit === r.value)) continue;
    const raw = edit ?? r.value;
    if (raw === "") continue;
    const value = coerce(r.kind, raw);
    if (value === undefined) continue;
    (overlay[r.node] ??= {})[r.key] = value;
  }
  return overlay;
}

export function knobValue(
  nodes: Record<string, ManifestNodeOverlay> | null,
  node: string,
  knob: KnobRow,
): unknown {
  const set = nodes?.[node]?.config;
  return set && knob.key in set ? set[knob.key] : knob.value;
}

export function knobText(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function withinBounds(knob: KnobRow, n: number): boolean {
  return (
    (knob.minimum === null || n >= knob.minimum) &&
    (knob.exclusive_minimum === null || n > knob.exclusive_minimum) &&
    (knob.maximum === null || n <= knob.maximum) &&
    (knob.exclusive_maximum === null || n < knob.exclusive_maximum)
  );
}

export function knobRange(knob: KnobRow): string | null {
  const low = knob.exclusive_minimum ?? knob.minimum;
  const high = knob.exclusive_maximum ?? knob.maximum;
  if (low === null && high === null) return null;
  const open = knob.exclusive_minimum !== null || low === null ? "(" : "[";
  const close = knob.exclusive_maximum !== null || high === null ? ")" : "]";
  return `${open}${low ?? "−∞"}, ${high ?? "∞"}${close}`;
}

export function parseKnob(knob: KnobRow, text: string): unknown {
  const t = text.trim();
  if (knob.type === "string") return t === "" && knob.nullable ? null : text;
  if (t === "") return knob.nullable ? null : undefined;
  if (knob.type === "integer" || knob.type === "number") {
    const n = Number(t);
    if (!Number.isFinite(n)) return undefined;
    if (knob.type === "integer" && !Number.isInteger(n)) return undefined;
    return withinBounds(knob, n) ? n : undefined;
  }
  const parsed = parseNested(t);
  return parsed === "" ? undefined : parsed;
}
