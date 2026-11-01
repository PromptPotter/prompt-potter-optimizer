import { CAP_AXIS_INFO } from "@/lib/api/types.generated";
import { capText, isCapAxis } from "@/lib/run-limits";

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

export function childOf(values: unknown, key: string): unknown {
  return typeof values === "object" && values !== null
    ? (values as Record<string, unknown>)[key]
    : undefined;
}

function said(value: unknown): value is string | number {
  return (typeof value === "string" && value !== "") || typeof value === "number";
}

function answers(schema: FieldSchema, values: unknown): string[] {
  const out: string[] = [];
  for (const [key, field] of Object.entries(schema.properties ?? {})) {
    const value = childOf(values, key);
    if (field.type === "object") {
      const inner = answers(field, value);
      // A blank subtree reads as what its served description says a blank binds.
      out.push(...(inner.length > 0 ? inner : field.description ? [field.description] : []));
    } else if (!said(value)) {
      continue;
    } else if (isCapAxis(key) && typeof value === "number") {
      out.push(`${CAP_AXIS_INFO[key].title} ${capText(CAP_AXIS_INFO[key], value)}`);
    } else {
      out.push(field.type === "string" ? String(value) : `${field.title ?? key} ${value}`);
    }
  }
  return out;
}

// One line naming a served step's current answer, in the schema's own order and words.
export function stepAnswer(schema: FieldSchema, values: unknown): string {
  return answers(schema, values).join(" · ");
}
