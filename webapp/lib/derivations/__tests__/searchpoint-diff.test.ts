import { describe, expect, it } from "vitest";
import { searchPointDiff } from "../searchpoint-diff";
import type { ObserveConfig } from "../searchPoint";

const cfg = (
  promptFields: Record<string, unknown>,
  config: Record<string, unknown> = {},
): ObserveConfig => ({ promptFields, config, label: "x" });

describe("searchPointDiff", () => {
  it("names the prompt fields that differ and stays silent on the rest", () => {
    expect(
      searchPointDiff(
        cfg({ persona: "a", task_intent: "old", instruction: "same" }),
        cfg({ persona: "a", task_intent: "new", instruction: "same" }),
      ),
    ).toEqual([{ kind: "prompt", node: null, names: ["Task intent"] }]);
  });

  it("groups config changes UNDER their node, so the node is named once", () => {
    expect(
      searchPointDiff(
        cfg({}, { llm_only: { model: "a", temperature: 0.2, top_p: 1 } }),
        cfg({}, { llm_only: { model: "b", temperature: 0.5, top_p: 1 } }),
      ),
    ).toEqual([{ kind: "config", node: "llm_only", names: ["model", "temperature"] }]);
  });

  it("reports a node present on one side only per-param, not as one opaque line", () => {
    expect(searchPointDiff(cfg({}, {}), cfg({}, { rerank: { top_k: 5, mode: "x" } }))).toEqual([
      { kind: "config", node: "rerank", names: ["top_k", "mode"] },
    ]);
  });

  it("keeps the config's top-level entries in a group with NO node — an empty node name would be a third meaning", () => {
    expect(searchPointDiff(cfg({}, { steps: ["a"] }), cfg({}, { steps: ["a", "b"] }))).toEqual([
      { kind: "config", node: null, names: ["steps"] },
    ]);
  });

  it("compares non-string values structurally — a list or a number is not a change unless it moved", () => {
    expect(
      searchPointDiff(
        cfg({ few_shot_examples: [1, 2] }, { steps: ["a", "b"] }),
        cfg({ few_shot_examples: [1, 2] }, { steps: ["a", "b"] }),
      ),
    ).toEqual([]);
    expect(
      searchPointDiff(
        cfg({ few_shot_examples: [1, 2] }, { llm_only: { model: "a" } }),
        cfg({ few_shot_examples: [1, 2, 3] }, { llm_only: { model: "b" } }),
      ),
    ).toEqual([
      { kind: "prompt", node: null, names: ["Few-shot examples"] },
      { kind: "config", node: "llm_only", names: ["model"] },
    ]);
  });

  it("treats a missing key and an explicit null as the same absence", () => {
    expect(searchPointDiff(cfg({ persona: "p" }), cfg({ persona: "p", plan: null }))).toEqual([]);
  });

  it("is empty when either side is unresolved — never a diff against nothing", () => {
    expect(searchPointDiff(null, cfg({ persona: "p" }))).toEqual([]);
    expect(searchPointDiff(cfg({ persona: "p" }), null)).toEqual([]);
  });
});
