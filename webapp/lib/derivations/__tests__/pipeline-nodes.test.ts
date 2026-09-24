import { describe, expect, it } from "vitest";
import { nodeOriginPrompt } from "../pipeline-nodes";
import type { PipelineDoc } from "@/components/workflow";

// Keys are `"{node}/{version}"`, split on the LAST slash: `startsWith` would take
// `l1_generate_extra` for `l1_generate`, and a lexical sort puts `"10"` before `"2"`.
const doc = (resolved_prompts: Record<string, Record<string, unknown>>): PipelineDoc => ({
  resolved_prompts,
  view: null,
  node_config_schema: {},
  node_output_schema: {},
  model_capabilities: {},
  reach: {},
});

describe("nodeOriginPrompt", () => {
  it("returns the node's declared prompt", () => {
    const got = nodeOriginPrompt(doc({ "l1_critique/1": { persona: "a critic" } }), "l1_critique");
    expect(got).toEqual({ fields: { persona: "a critic" }, version: "1", count: 1 });
  });

  it("takes the LOWEST version and reports how many the node declares", () => {
    // No `/1` on purpose: with one present, a lexical sort answers correctly by luck
    // and the numeric compare goes untested. Lexically "10" sorts before "2".
    const got = nodeOriginPrompt(
      doc({ "checkin/10": { persona: "tenth" }, "checkin/2": { persona: "second" } }),
      "checkin",
    );
    expect(got?.fields).toEqual({ persona: "second" });
    expect(got?.version).toBe("2");
    expect(got?.count).toBe(2);
  });

  it("does not match a node whose id merely starts the same way", () => {
    expect(nodeOriginPrompt(doc({ "l1_generate_v2/1": { persona: "x" } }), "l1_generate")).toBeNull();
  });

  it("is null when the manifest declares no prompt for the node", () => {
    expect(nodeOriginPrompt(doc({ "l1_generate/1": { persona: "x" } }), "l1_score")).toBeNull();
    expect(nodeOriginPrompt(doc({}), "l1_generate")).toBeNull();
    expect(nodeOriginPrompt(null, "l1_generate")).toBeNull();
    expect(nodeOriginPrompt(doc({ "l1_generate/1": {} }), null)).toBeNull();
  });
});
