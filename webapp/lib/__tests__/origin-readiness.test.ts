import { describe, expect, it } from "vitest";
import type { DraftCampaignWire } from "../api";
import { plainLanguageRecap, questionOptions, questionPatch } from "../origin-readiness";

function draft(over: Partial<DraftCampaignWire> = {}): DraftCampaignWire {
  return {
    draft_id: "d_0123456789abcdef",
    slug: "labtests",
    sample_preview: [{ query: "Na", ground_truth: "Sodium" }],
    n_samples: 42,
    connector: "termnorm",
    scoring_composite: "exact_match",
    optimization_overrides: {
      max_rounds: 5,
      lock_model: true,
      mechanisms: {
        selection: { per_round_resubset: true },
        elimination: {
          epsilon_elimination: true,
          deterministic_dominance: true,
          degradation_fatal_fastpath: true,
          leader_lock_in: false,
        },
      },
    },
    raw_task_description: "",
    pipeline_overlay: {},
    headers: ["input", "gt"],
    column_query: "",
    column_ground_truth: "",
    // Only the gated fields carry provenance: the two columns + task framing.
    // Config is not gated (no entry); it carries a default the operator edits.
    field_provenance: {
      "column.query": "unset",
      "column.ground_truth": "unset",
      task_description: "unset",
    },
    origin_prompt_fields: {},
    candidate_library_size: 0,
    created_at: "2026-05-30T00:00:00Z",
    updated_at: "2026-05-30T00:00:00Z",
    optimizer_locks: { pipeline: ["llm_only"], forbidden_axes: ["model", "provider"], nodes: {} },
    pipeline_view: null,
    node_config_schema: {},
    node_output_schema: {},
    dependencies: [],
    // Server-authoritative mint-gate verdict (the gate lives in
    // `origin_readiness.py`; the client reads this, never re-derives it).
    readiness: { complete: false, gaps: [] },
    ...over,
  };
}

describe("questionPatch / questionOptions (resolver answer-back loop)", () => {
  it("maps each field id to its draft-patch key", () => {
    expect(questionPatch("column.query", "input")).toEqual({ column_query: "input" });
    expect(questionPatch("task_description", "map codes")).toEqual({
      raw_task_description: "map codes",
    });
    expect(questionPatch("max_rounds", "8")).toEqual({
      optimization_overrides: { max_rounds: 8 },
    });
  });

  it("rejects un-applicable answers so the caller skips them", () => {
    expect(questionPatch("max_rounds", "lots")).toBeNull(); // non-numeric
    expect(questionPatch("max_rounds", "999")).toBeNull(); // out of 1..100
    expect(questionPatch("column.query", "  ")).toBeNull(); // blank
    expect(questionPatch("backend.node_config", "x")).toBeNull(); // not string-applicable
    expect(questionPatch("nonsense", "x")).toBeNull(); // unknown field
  });

  it("grounds a column question's options in the uploaded headers", () => {
    expect(questionOptions("column.query", [], ["a", "b"])).toEqual(["a", "b"]);
    // The resolver's own options win when supplied; non-column free-text → empty.
    expect(questionOptions("connector", ["termnorm"], ["a"])).toEqual(["termnorm"]);
    expect(questionOptions("task_description", [], ["a"])).toEqual([]);
  });
});

describe("plainLanguageRecap", () => {
  it("restates the campaign in jargon-free terms from confirmed fields", () => {
    const text = plainLanguageRecap(
      draft({
        column_query: "input",
        column_ground_truth: "gt",
        raw_task_description: "map lab-test names to their codes",
      }),
    );
    expect(text).toContain("map lab-test names to their codes");
    expect(text).toContain("TermNorm pipeline");
    expect(text).toContain("exact match");
    expect(text).toContain("5 rounds");
  });
});
