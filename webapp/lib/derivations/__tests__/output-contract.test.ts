import { describe, expect, it } from "vitest";
import type { NodeOutputSchema } from "@/lib/api";
import { outputContract } from "../output-contract";

// The fixture is `l1_generate`'s served schema, trimmed to the shapes that decide the walk:
// a `$ref` behind an array, a Pydantic `anyOf … null` optional, a map-valued object, a
// `maxLength`, and a `required` list that names one of six.
const L1_GENERATE: NodeOutputSchema = {
  fields: ["variants"],
  field_descriptions: {},
  json_schema: {
    name: "l1_generate",
    strict: false,
    schema: {
      $defs: {
        L1Variant: {
          type: "object",
          title: "L1Variant",
          required: ["changes_description"],
          properties: {
            evidence_grounding: {
              anyOf: [{ $ref: "#/$defs/VariantEvidenceGrounding" }, { type: "null" }],
              default: null,
            },
            prompt_fields_override: {
              type: "object",
              additionalProperties: { type: "string" },
              description: "Top-level prompt-template fields.",
            },
            changes_description: { type: "string", maxLength: 320 },
          },
        },
        VariantEvidenceGrounding: {
          type: "object",
          required: ["field", "citation"],
          properties: {
            field: { type: "string", description: "A citable panel named in the prompt." },
            citation: { type: "string", maxLength: 320 },
          },
        },
      },
      type: "object",
      required: ["variants"],
      properties: { variants: { type: "array", items: { $ref: "#/$defs/L1Variant" } } },
    },
  },
};

describe("outputContract", () => {
  it("is empty for a node that declares no structured output", () => {
    // A measurement node returns none — a real answer, not a missing one.
    expect(outputContract(null)).toEqual([]);
    expect(outputContract(undefined)).toEqual([]);
    expect(outputContract({ fields: [], field_descriptions: {}, json_schema: {} })).toEqual([]);
  });

  it("names the referenced type on an array rather than the word `array`", () => {
    const rows = outputContract(L1_GENERATE);
    const variants = rows.find((r) => r.key === "variants");
    expect(variants?.type).toBe("L1Variant[]");
    expect(variants?.required).toBe(true);
    expect(variants?.depth).toBe(0);
  });

  it("steps THROUGH the array into the element's own parameters", () => {
    // The whole point: `fields` says `variants` and stops. Six parameters live one hop in.
    const rows = outputContract(L1_GENERATE);
    expect(rows.map((r) => r.key)).toEqual([
      "variants",
      "variants.evidence_grounding",
      "variants.evidence_grounding.field",
      "variants.evidence_grounding.citation",
      "variants.prompt_fields_override",
      "variants.changes_description",
    ]);
  });

  it("reads an `anyOf … null` optional as its live branch, and as NOT required", () => {
    const rows = outputContract(L1_GENERATE);
    const eg = rows.find((r) => r.key === "variants.evidence_grounding");
    expect(eg?.type).toBe("VariantEvidenceGrounding");
    expect(eg?.required).toBe(false);
    // Resolved through the `$ref`, so its own two fields are rows of their own.
    expect(rows.find((r) => r.key === "variants.evidence_grounding.field")?.required).toBe(true);
  });

  it("carries the description and the machine limit the model is held to", () => {
    const rows = outputContract(L1_GENERATE);
    expect(rows.find((r) => r.key === "variants.changes_description")?.limit).toBe("≤320 chars");
    expect(rows.find((r) => r.key === "variants.prompt_fields_override")?.description).toBe(
      "Top-level prompt-template fields.",
    );
    // Required by the element's OWN list, one level down — not the root's.
    expect(rows.find((r) => r.key === "variants.changes_description")?.required).toBe(true);
  });

  it("falls back to the flat field list when no schema is on the wire", () => {
    // What a backend's `/pipeline` reports for a target node: keys and prose, no JSON Schema.
    const rows = outputContract({
      fields: ["answer", "reasoning"],
      field_descriptions: { answer: "The normalized term." },
      json_schema: {},
    });
    expect(rows).toEqual([
      { key: "answer", name: "answer", depth: 0, type: "", required: false, description: "The normalized term.", limit: "", enums: [] },
      { key: "reasoning", name: "reasoning", depth: 0, type: "", required: false, description: "", limit: "", enums: [] },
    ]);
  });

  it("reads a bare JSON Schema as well as a response-format envelope", () => {
    const rows = outputContract({
      fields: [],
      field_descriptions: {},
      json_schema: {
        type: "object",
        required: ["verdict"],
        properties: { verdict: { type: "string", enum: ["pass", "fail"] } },
      },
    });
    expect(rows).toHaveLength(1);
    expect(rows[0]?.enums).toEqual(["pass", "fail"]);
  });

  it("stops on a self-referential schema instead of recursing", () => {
    const rows = outputContract({
      fields: [],
      field_descriptions: {},
      json_schema: {
        $defs: { Node: { type: "object", properties: { child: { $ref: "#/$defs/Node" } } } },
        type: "object",
        properties: { root: { $ref: "#/$defs/Node" } },
      },
    });
    expect(rows.map((r) => r.key)).toEqual(["root", "root.child"]);
  });
});
