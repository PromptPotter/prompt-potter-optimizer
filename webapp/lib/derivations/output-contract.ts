// A node's contracted output, one row per parameter of its served JSON Schema. Read `json_schema`,
// never `fields`/`field_descriptions` — top-level keys only, and empty on every optimizer node.

import type { NodeOutputSchema } from "@/lib/api";

export interface ContractField {
  key: string;
  name: string;
  depth: number;
  type: string;
  required: boolean;
  description: string;
  limit: string;
  enums: string[];
}

// Reaches every optimizer contract's leaves; a deeper schema should be flattened server-side.
const MAX_DEPTH = 3;

function isRec(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

interface Resolved {
  schema: Record<string, unknown>;
  type: string;
  ref: string | null;
  optional: boolean;
}

function resolve(raw: unknown, defs: Record<string, unknown>, hops = 0): Resolved | null {
  if (!isRec(raw)) return null;
  if (typeof raw.$ref === "string") {
    const name = raw.$ref.slice(raw.$ref.lastIndexOf("/") + 1);
    const target = defs[name];
    if (hops > 4 || !isRec(target)) return { schema: {}, type: name, ref: name, optional: false };
    const inner = resolve(target, defs, hops + 1);
    return inner ? { ...inner, type: name, ref: name } : null;
  }

  if (Array.isArray(raw.anyOf)) {
    const live = raw.anyOf.filter((b) => !(isRec(b) && b.type === "null"));
    const optional = live.length < raw.anyOf.length;
    const only = live.length === 1 ? live[0] : undefined;
    if (only !== undefined) {
      const inner = resolve(only, defs, hops + 1);
      return inner ? { ...inner, optional: inner.optional || optional } : null;
    }
    const type = live.map((b) => resolve(b, defs, hops + 1)?.type ?? "?").join(" | ");
    return { schema: {}, type: type || "any", ref: null, optional };
  }

  if (raw.type === "array") {
    const item = resolve(raw.items, defs, hops + 1);
    return {
      schema: item?.schema ?? {},
      type: `${item?.type ?? "any"}[]`,
      ref: item?.ref ?? null,
      optional: false,
    };
  }

  return { schema: raw, type: str(raw.type) || "object", ref: null, optional: false };
}

// Declaration site first: a `$ref`'d field may cap itself where used, and that cap binds.
function limitOf(decl: unknown, target: unknown): string {
  const parts: string[] = [];
  for (const s of [decl, target]) {
    if (!isRec(s)) continue;
    if (typeof s.maxLength === "number") parts.push(`≤${s.maxLength} chars`);
    if (typeof s.maxItems === "number") parts.push(`≤${s.maxItems} items`);
    if (typeof s.minItems === "number") parts.push(`≥${s.minItems} items`);
    if (typeof s.minimum === "number") parts.push(`≥${s.minimum}`);
    if (typeof s.maximum === "number") parts.push(`≤${s.maximum}`);
    if (parts.length > 0) break;
  }
  return parts.join(" · ");
}

function enumsOf(decl: unknown, target: unknown): string[] {
  for (const s of [decl, target]) {
    if (isRec(s) && Array.isArray(s.enum)) return s.enum.map((v) => String(v));
  }
  return [];
}

function walk(
  obj: Record<string, unknown>,
  depth: number,
  prefix: string,
  defs: Record<string, unknown>,
  seen: ReadonlySet<string>,
  out: ContractField[],
): void {
  if (!isRec(obj.properties)) return;
  const required = new Set(
    (Array.isArray(obj.required) ? obj.required : []).filter((r): r is string => typeof r === "string"),
  );
  for (const [name, raw] of Object.entries(obj.properties)) {
    const r = resolve(raw, defs);
    if (!r) continue;
    const key = prefix ? `${prefix}.${name}` : name;
    out.push({
      key,
      name,
      depth,
      type: r.type,
      required: required.has(name) && !r.optional,
      // The declaration site wins over the `$defs` target: a `$ref`'d field describes its ROLE
      // here and its shape there, and the role is what the reader wants beside the name.
      description: str(isRec(raw) ? raw.description : "") || str(r.schema.description),
      limit: limitOf(raw, r.schema),
      enums: enumsOf(raw, r.schema),
    });
    if (depth + 1 >= MAX_DEPTH) continue;
    if (r.ref !== null && seen.has(r.ref)) continue;
    walk(r.schema, depth + 1, key, defs, r.ref === null ? seen : new Set([...seen, r.ref]), out);
  }
}

// `[]` is a real answer: a measurement node returns no structured output.
export function outputContract(schema: NodeOutputSchema | null | undefined): ContractField[] {
  if (!schema) return [];
  // A response-format envelope (`{name, strict, schema}`) and a bare JSON Schema both arrive
  // here — the optimizer manifest serves the first, a backend's `/pipeline` the second.
  const js = schema.json_schema;
  const root = isRec(js) ? (isRec(js.schema) ? js.schema : js) : null;
  const out: ContractField[] = [];
  if (root) {
    const defs = isRec(root.$defs) ? root.$defs : {};
    walk(root, 0, "", defs, new Set(), out);
  }
  if (out.length > 0) return out;
  // Only the flat key list — what a backend's `/pipeline` reports for a target node.
  return schema.fields.map((f) => ({
    key: f,
    name: f,
    depth: 0,
    type: "",
    required: false,
    description: schema.field_descriptions[f] ?? "",
    limit: "",
    enums: [],
  }));
}
