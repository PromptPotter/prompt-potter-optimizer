import { describe, expect, it } from "vitest";
import {
  configRows,
  nodeOverlayPatch,
  seedOverlayFromRows,
  type ConfigRow,
} from "../nodeConfig";
import type { NodeConfigParam } from "@/lib/api";

function row(over: Partial<ConfigRow> & { key: string; kind: string }): ConfigRow {
  return {
    node: "n",
    options: [],
    value: "",
    baseValue: "",
    locked: false,
    allowed: [],
    fromCandidate: false,
    optimizerLocked: false,
    description: "",
    ...over,
  };
}

describe("nodeOverlayPatch (search-space emit)", () => {
  it("writes open params to param_keys and omits locked ones", () => {
    const patch = nodeOverlayPatch({}, true, "web_search", [
      row({ key: "max_sites", kind: "number" }),
      row({ key: "num_results", kind: "number", locked: true }),
    ]);
    const opt = (patch.pipeline_overlay!.web_search as Record<string, unknown>)
      .optimizer as Record<string, unknown>;
    expect(opt.param_keys).toEqual(["max_sites"]);
    expect(patch.optimization_overrides).toEqual({ lock_model: true });
  });

  it("locks a node to [] when every param is locked", () => {
    const patch = nodeOverlayPatch({}, false, "fuzzy_matching", [
      row({ key: "threshold", kind: "number", locked: true }),
      row({ key: "scorer", kind: "enum", locked: true, options: ["WRatio", "QRatio"] }),
    ]);
    const opt = (patch.pipeline_overlay!.fuzzy_matching as Record<string, unknown>)
      .optimizer as Record<string, unknown>;
    expect(opt.param_keys).toEqual([]);
    expect(opt.param_allowed_values).toEqual({});
  });

  it("narrows an enum's allowed-values only when a strict subset", () => {
    const patch = nodeOverlayPatch({}, false, "llm", [
      row({
        key: "reasoning_effort",
        kind: "enum",
        allowed: ["low"],
        options: ["low", "medium", "high"],
      }),
      row({ key: "response_format", kind: "enum", allowed: ["t", "j"], options: ["t", "j"] }),
    ]);
    const opt = (patch.pipeline_overlay!.llm as Record<string, unknown>).optimizer as Record<
      string,
      string[]
    >;
    // strict subset → narrowed; full set → omitted
    expect(opt.param_allowed_values).toEqual({ reasoning_effort: ["low"] });
  });

  it("carries a changed origin value into config, coerced by kind; model excluded from param_keys", () => {
    const patch = nodeOverlayPatch({}, true, "entity_profiling", [
      row({ key: "temperature", kind: "number", value: "0.2", baseValue: "0.3" }),
      row({ key: "model", kind: "model", value: "m2", baseValue: "m1", options: ["m1", "m2"] }),
    ]);
    const node = patch.pipeline_overlay!.entity_profiling as Record<string, unknown>;
    expect(node.config).toEqual({ temperature: 0.2, model: "m2" });
    // temperature (open) lands in param_keys; model never does (it rides lock_model)
    expect((node.optimizer as Record<string, unknown>).param_keys).toEqual(["temperature"]);
  });

  it("merges onto an existing overlay without clobbering other nodes", () => {
    const base = { other_node: { config: { x: 1 } } };
    const patch = nodeOverlayPatch(base, false, "web_search", [
      row({ key: "max_sites", kind: "number" }),
    ]);
    expect(patch.pipeline_overlay!.other_node).toEqual({ config: { x: 1 } });
  });
});

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
      optimizer_tunable: false,
    },
    {
      key: "reasoning_effort",
      value: "low",
      kind: "enum",
      options: ["low", "medium", "high"],
      description: "",
      optimizer_locked: false,
      optimizer_tunable: true,
    },
    {
      key: "temperature",
      value: 0,
      kind: "number",
      options: [],
      description: "",
      optimizer_locked: false,
      optimizer_tunable: true,
    },
    {
      key: "max_tokens",
      value: null,
      kind: "number",
      options: [],
      description: "",
      optimizer_locked: false,
      optimizer_tunable: true,
    },
  ],
};

describe("configRows (values mode)", () => {
  it("exposes the full config surface — model included with its options", () => {
    const rows = configRows(schema, {}, "values");
    const byKey = Object.fromEntries(rows.map((r) => [r.key, r]));
    expect(rows.map((r) => r.key).sort()).toEqual([
      "max_tokens",
      "model",
      "reasoning_effort",
      "temperature",
    ]);
    expect(byKey.model!.kind).toBe("model");
    expect(byKey.model!.options).toEqual(["openai/gpt-oss-120b", "openai/gpt-oss-20b"]);
    expect(byKey.model!.optimizerLocked).toBe(true); // shown, not dropped
    expect(byKey.reasoning_effort!.kind).toBe("enum");
    expect(byKey.temperature!.kind).toBe("number");
    expect(byKey.max_tokens!.value).toBe(""); // declared but unset
  });

  it("seeds the value from the candidate overlay over the config floor", () => {
    const rows = configRows(schema, { llm_only: { reasoning_effort: "high" } }, "values");
    const re = rows.find((r) => r.key === "reasoning_effort")!;
    expect(re.value).toBe("high");
    expect(re.fromCandidate).toBe(true);
    expect(rows.find((r) => r.key === "temperature")!.fromCandidate).toBe(false);
  });

  it("returns no rows without a schema", () => {
    expect(configRows(null, {}, "values")).toEqual([]);
  });

  it("scopes rows to one node when `node` is given (OBSERVE drill-in vs whole-pipeline)", () => {
    const multi = { web_search: schema.llm_only!, llm_only: schema.llm_only! };
    expect(new Set(configRows(multi, {}, "values", "llm_only").map((r) => r.node))).toEqual(
      new Set(["llm_only"]),
    );
    expect(new Set(configRows(multi, {}, "values").map((r) => r.node))).toEqual(
      new Set(["web_search", "llm_only"]),
    );
  });
});

describe("seedOverlayFromRows (values emit)", () => {
  const rows = configRows(schema, { llm_only: { reasoning_effort: "high" } }, "values");

  it("keeps candidate params + operator edits, drops inherited-untouched", () => {
    const overlay = seedOverlayFromRows(rows, { "llm_only.temperature": "0.7" });
    expect(overlay).toEqual({ llm_only: { reasoning_effort: "high", temperature: 0.7 } });
  });

  it("an untouched dialog re-emits exactly the candidate overlay", () => {
    expect(seedOverlayFromRows(rows, {})).toEqual({ llm_only: { reasoning_effort: "high" } });
  });

  it("lets the operator override the optimizer-locked model", () => {
    const overlay = seedOverlayFromRows(rows, { "llm_only.model": "openai/gpt-oss-20b" });
    expect(overlay.llm_only!.model).toBe("openai/gpt-oss-20b");
  });
});
