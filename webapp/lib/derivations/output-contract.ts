// The structured output a node is CONTRACTED to return, flattened out of its served JSON
// Schema into one row per parameter.
//
// Read `json_schema`, never the two thin fields beside it: `fields` is the TOP-LEVEL key list and
// `field_descriptions` is empty on every optimizer node, so together they render `l1_generate` as
// the single word `variants` while the schema declares six parameters, four descriptions and two
// length caps. The contract is not a garnish — it is emitted into the call as `response_format`
// and every byte of it is prompt text the model reads (`docs/concepts/structured-output.md`).
//
// Pure over the served schema — it resolves `$ref`, unwraps Pydantic's `anyOf … null` optionals
// and steps into arrays, and it INVENTS nothing: every string here was authored server-side.

import type { NodeOutputSchema } from "@/lib/api";

// One parameter. Nesting is a `depth`, not a tree, because the renderer is a flat list and a
// nested shape indents rather than folding — a contract read one disclosure at a time is a
// contract nobody reads.
export interface ContractField {
  // Dotted path from the root — unique, so it keys the row.
  key: string;
  name: string;
  depth: number;
  // The declared type, with a `$defs` name kept where one was referenced (`L1Variant[]` rather
  // than `array`) so the rows below it have a heading that names them.
  type: string;
  required: boolean;
  description: string;
  // The machine limits the model is held to — `≤320 chars`, `≤3 items`. Part of what the node
  // promises, and the half the prose descriptions restate by hand.
  limit: string;
  // A closed value space, where the schema declares one.
  enums: string[];
}

// Three levels reaches every optimizer contract's leaves (`variants[]` → `L1Variant` →
// `VariantEvidenceGrounding`). A deeper one is a schema that should be flattened server-side,
// not a list to scroll.
const MAX_DEPTH = 3;

function isRec(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

interface Resolved {
  // What to walk INTO — for an array that is the item schema, so its element's parameters
  // become this row's children rather than a dead end.
  schema: Record<string, unknown>;
  type: string;
  // The `$defs` name this resolved through, or null. Doubles as the cycle guard's key.
  ref: string | null;
  // Pydantic writes an optional as `anyOf: [T, null]`, which is a second way to say what the
  // `required` list already says. Both are read; either one makes the row optional.
  optional: boolean;
}

// One property's declaration → what it IS. Returns null only for a non-object declaration,
// which is a malformed schema rather than an empty one.
function resolve(raw: unknown, defs: Record<string, unknown>, hops = 0): Resolved | null {
  if (!isRec(raw)) return null;
  // A `$ref` chain longer than a couple of hops is a schema pointing at itself; stop rather
  // than recurse, and keep the name so the row still says what it pointed at.
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

// The declaration site first, its `$defs` target second — a `$ref`'d field may cap itself where
// it is used, and that cap is the one this row is held to.
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

// `[]` where the node declares no contract at all — which is a real answer (a measurement node
// returns no structured output) and the caller renders nothing rather than an empty heading.
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
  // No schema on the wire, only the flat key list — the shape a backend's `/pipeline` reports
  // for a target node. Same rows, fewer columns filled; never a second renderer.
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
