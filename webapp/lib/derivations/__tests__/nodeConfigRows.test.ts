import { describe, expect, it } from "vitest";
import { nodeConfigRows, seedOverlayFromRows } from "@/lib/derivations/nodeConfigRows";
import type { NodeConfigParam } from "@/lib/api";

// The server `node_config_schema` for an llm_only node: model (a select of
// available_models, optimizer-locked but operator-editable), reasoning_effort
// (enum), temperature (number), max_tokens (number, declared but unset).
const schema: Record<string, NodeConfigParam[]> = {
  llm_only: [
    {
      key: "model",
      value: "openai/gpt-oss-120b",
      kind: "model",
      options: ["openai/gpt-oss-120b", "openai/gpt-oss-20b"],
      description: "",
      optimizer_locked: true,
    },
    {
      key: "reasoning_effort",
      value: "low",
      kind: "enum",
      options: ["low", "medium", "high"],
      description: "",
      optimizer_locked: false,
    },
    { key: "temperature", value: 0, kind: "number", options: [], description: "", optimizer_locked: false },
    { key: "max_tokens", value: null, kind: "number", options: [], description: "", optimizer_locked: false },
  ],
};

describe("nodeConfigRows", () => {
  it("exposes the full config surface — model included with its options", () => {
    const rows = nodeConfigRows(schema, {});
    const byParam = Object.fromEntries(rows.map((r) => [r.param, r]));
    expect(rows.map((r) => r.param).sort()).toEqual([
      "max_tokens",
      "model",
      "reasoning_effort",
      "temperature",
    ]);
    expect(byParam.model.kind).toBe("model");
    expect(byParam.model.options).toEqual(["openai/gpt-oss-120b", "openai/gpt-oss-20b"]);
    expect(byParam.model.optimizerLocked).toBe(true); // shown, not dropped
    expect(byParam.reasoning_effort.kind).toBe("enum");
    expect(byParam.temperature.kind).toBe("number");
    expect(byParam.max_tokens.value).toBe(""); // declared but unset
  });

  it("seeds the value from the candidate overlay over the config floor", () => {
    const rows = nodeConfigRows(schema, { llm_only: { reasoning_effort: "high" } });
    const re = rows.find((r) => r.param === "reasoning_effort")!;
    expect(re.value).toBe("high");
    expect(re.fromCandidate).toBe(true);
    expect(rows.find((r) => r.param === "temperature")!.fromCandidate).toBe(false);
  });

  it("returns no rows without a schema", () => {
    expect(nodeConfigRows(null, {})).toEqual([]);
  });
});

describe("seedOverlayFromRows", () => {
  const rows = nodeConfigRows(schema, { llm_only: { reasoning_effort: "high" } });

  it("keeps candidate params + operator edits, drops inherited-untouched", () => {
    const overlay = seedOverlayFromRows(rows, { "llm_only.temperature": "0.7" });
    expect(overlay).toEqual({ llm_only: { reasoning_effort: "high", temperature: 0.7 } });
  });

  it("an untouched dialog re-emits exactly the candidate overlay", () => {
    expect(seedOverlayFromRows(rows, {})).toEqual({ llm_only: { reasoning_effort: "high" } });
  });

  it("lets the operator override the optimizer-locked model", () => {
    const overlay = seedOverlayFromRows(rows, { "llm_only.model": "openai/gpt-oss-20b" });
    expect(overlay.llm_only.model).toBe("openai/gpt-oss-20b");
  });
});
