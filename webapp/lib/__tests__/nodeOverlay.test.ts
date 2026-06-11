import { describe, expect, it } from "vitest";
import { nodeOverlayPatch, type ParamState } from "../derivations/nodeOverlay";

function state(over: Partial<ParamState> & { key: string; kind: string }): ParamState {
  return {
    locked: false,
    allowed: [],
    options: [],
    value: "",
    baseValue: "",
    ...over,
  };
}

describe("nodeOverlayPatch", () => {
  it("writes open params to param_keys and omits locked ones", () => {
    const patch = nodeOverlayPatch({}, true, "web_search", [
      state({ key: "max_sites", kind: "number" }),
      state({ key: "num_results", kind: "number", locked: true }),
    ]);
    const opt = (patch.pipeline_overlay!.web_search as Record<string, unknown>)
      .optimizer as Record<string, unknown>;
    expect(opt.param_keys).toEqual(["max_sites"]);
    expect(patch.lock_model).toBe(true);
  });

  it("locks a node to [] when every param is locked", () => {
    const patch = nodeOverlayPatch({}, false, "fuzzy_matching", [
      state({ key: "threshold", kind: "number", locked: true }),
      state({ key: "scorer", kind: "enum", locked: true, options: ["WRatio", "QRatio"] }),
    ]);
    const opt = (patch.pipeline_overlay!.fuzzy_matching as Record<string, unknown>)
      .optimizer as Record<string, unknown>;
    expect(opt.param_keys).toEqual([]);
    expect(opt.param_allowed_values).toEqual({});
  });

  it("narrows an enum's allowed-values only when a strict subset", () => {
    const patch = nodeOverlayPatch({}, false, "llm", [
      state({
        key: "reasoning_effort",
        kind: "enum",
        allowed: ["low"],
        options: ["low", "medium", "high"],
      }),
      state({ key: "response_format", kind: "enum", allowed: ["t", "j"], options: ["t", "j"] }),
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
      state({ key: "temperature", kind: "number", value: "0.2", baseValue: "0.3" }),
      state({ key: "model", kind: "model", value: "m2", baseValue: "m1", options: ["m1", "m2"] }),
    ]);
    const node = patch.pipeline_overlay!.entity_profiling as Record<string, unknown>;
    expect(node.config).toEqual({ temperature: 0.2, model: "m2" });
    // temperature (open) lands in param_keys; model never does (it rides lock_model)
    expect((node.optimizer as Record<string, unknown>).param_keys).toEqual(["temperature"]);
  });

  it("merges onto an existing overlay without clobbering other nodes", () => {
    const base = { other_node: { config: { x: 1 } } };
    const patch = nodeOverlayPatch(base, false, "web_search", [
      state({ key: "max_sites", kind: "number" }),
    ]);
    expect(patch.pipeline_overlay!.other_node).toEqual({ config: { x: 1 } });
  });
});
