"use client";

import { CommitInput } from "@/components/ui";

// The JSON Schema subset `DraftCampaignWire.setup_schema` speaks.
export interface FieldSchema {
  type?: "object" | "string" | "integer" | "number";
  title?: string;
  description?: string;
  enum?: string[];
  minimum?: number;
  exclusiveMinimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  writeOnly?: boolean;
  default?: unknown;
  required?: string[];
  properties?: Record<string, FieldSchema>;
}

// `writeOnly`: the value rides Start and is stored nowhere; otherwise it is the draft patch at `path`.
export type Commit = (path: string[], value: unknown, writeOnly: boolean) => void;

type Json = Record<string, unknown>;

function child(values: unknown, key: string): unknown {
  return typeof values === "object" && values !== null ? (values as Json)[key] : undefined;
}

// Null clears the key: a blank field is the absent value.
export function setIn<T extends object>(target: T, path: string[], value: unknown): T {
  const [head, ...rest] = path;
  if (head === undefined) return target;
  const out: Json = { ...(target as Json) };
  const next = rest.length > 0 ? setIn((child(target, head) as Json | undefined) ?? {}, rest, value) : value;
  if (next === null || next === undefined) delete out[head];
  else out[head] = next;
  return out as T;
}

// A stored field shows the draft's value, so only a `writeOnly` one starts from the served `default`.
export function writeOnlyDefaults(schema: FieldSchema, writeOnly = false): Json {
  let out: Json = {};
  for (const [key, field] of Object.entries(schema.properties ?? {})) {
    const w = writeOnly || field.writeOnly === true;
    if (field.type === "object") {
      const inner = writeOnlyDefaults(field, w);
      if (Object.keys(inner).length > 0) out = { ...out, [key]: inner };
    } else if (w && field.default !== undefined) out = { ...out, [key]: field.default };
  }
  return out;
}

const INVALID = Symbol("invalid");

function parse(field: FieldSchema, text: string, required: boolean): unknown {
  const s = text.trim();
  if (s === "") return required ? INVALID : null;
  if (field.type === "integer" || field.type === "number") {
    const n = Number(s);
    if (!Number.isFinite(n) || (field.type === "integer" && !Number.isInteger(n))) return INVALID;
    if (field.exclusiveMinimum !== undefined && n <= field.exclusiveMinimum) return INVALID;
    return n;
  }
  return field.pattern && !new RegExp(`^(?:${field.pattern})$`).test(s) ? INVALID : s;
}

export function SchemaFields({
  schema,
  values,
  onCommit,
  writeOnly = false,
}: {
  schema: FieldSchema;
  values: unknown;
  onCommit: Commit;
  writeOnly?: boolean;
}) {
  return Object.entries(schema.properties ?? {}).map(([key, field]) => (
    <SchemaField
      key={key}
      field={field}
      value={child(values, key)}
      required={schema.required?.includes(key) ?? false}
      writeOnly={writeOnly || field.writeOnly === true}
      onCommit={(path, v, w) => onCommit([key, ...path], v, w)}
    />
  ));
}

function SchemaField({
  field,
  value,
  required,
  writeOnly,
  onCommit,
}: {
  field: FieldSchema;
  value: unknown;
  required: boolean;
  writeOnly: boolean;
  onCommit: Commit;
}) {
  const note = field.description ? <small> {field.description}</small> : null;

  if (field.type === "object") {
    const inner = <SchemaFields schema={field} values={value} writeOnly={writeOnly} onCommit={onCommit} />;
    // An untitled object only nests the patch; it draws no box of its own.
    return field.title ? (
      <fieldset>
        <legend>{field.title}</legend>
        {inner}
        {note ? <p>{note}</p> : null}
      </fieldset>
    ) : (
      inner
    );
  }

  const text = value === undefined || value === null ? "" : String(value);
  if (field.enum) {
    return (
      <p>
        <label>
          {field.title}
          {note}{" "}
          <select value={text} onChange={(e) => onCommit([], e.target.value, writeOnly)}>
            {text === "" ? (
              <option value="" disabled>
                — pick —
              </option>
            ) : null}
            {field.enum.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </label>
      </p>
    );
  }

  const integer = field.type === "integer";
  const numeric = integer || field.type === "number";
  return (
    <p>
      <label>
        {field.title}
        {note}{" "}
        <CommitInput
          value={text}
          type={numeric ? "number" : "text"}
          inputMode={integer ? "numeric" : numeric ? "decimal" : undefined}
          step={integer ? 1 : numeric ? "any" : undefined}
          min={field.minimum}
          max={field.maximum}
          minLength={field.minLength}
          maxLength={field.maxLength}
          required={required}
          validate={(draft) => parse(field, draft, required) !== INVALID}
          onCommit={(draft) => onCommit([], parse(field, draft, required), writeOnly)}
        />
      </label>
    </p>
  );
}
