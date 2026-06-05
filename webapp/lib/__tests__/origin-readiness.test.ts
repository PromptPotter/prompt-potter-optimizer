import { describe, expect, it } from "vitest";
import type { DraftCampaignWire } from "../api";
import {
  originReadiness,
  plainLanguageRecap,
  questionOptions,
  questionPatch,
} from "../origin-readiness";

// The config knobs auto-confirm at ingest (template defaults); a fresh draft
// carries them `confirmed`. `task_description` lands `unset`. The fixture
// mirrors that server-side seed.
const CONFIG_CONFIRMED = {
  connector: "confirmed",
  scoring_composite: "confirmed",
  max_rounds: "confirmed",
  "optimizer.provider": "confirmed",
  "optimizer.model": "confirmed",
  "backend.node_config": "confirmed",
  task_description: "unset",
} as const;

function draft(over: Partial<DraftCampaignWire> = {}): DraftCampaignWire {
  return {
    draft_id: "d_0123456789abcdef",
    slug: "labtests",
    sample_preview: [{ query: "Na", ground_truth: "Sodium" }],
    n_samples: 42,
    derived_from_dataset: false,
    connector: "termnorm",
    scoring_composite: "exact_match",
    max_rounds: 5,
    task_description: "",
    pipeline_overlay: {},
    optimizer_provider: "groq",
    optimizer_model: "",
    headers: ["input", "gt"],
    column_query: "",
    column_ground_truth: "",
    resolved: {
      "column.query": "unset",
      "column.ground_truth": "unset",
      ...CONFIG_CONFIRMED,
    },
    // Config knobs auto-confirm from template defaults → AUTO source; the
    // gate ignores this axis, it is audit-only. Columns/task carry no source
    // until set, mirroring the server seed.
    sources: {
      connector: "auto",
      scoring_composite: "auto",
      max_rounds: "auto",
      "optimizer.provider": "auto",
      "optimizer.model": "auto",
      "backend.node_config": "auto",
    },
    starting_prompt: {},
    lock_model: true,
    created_at: "2026-05-30T00:00:00Z",
    updated_at: "2026-05-30T00:00:00Z",
    optimizer_locks: { pipeline: ["llm_only"], forbidden_axes: ["model", "provider"], nodes: {} },
    ...over,
  };
}

// A fully-resolved draft: columns confirmed + framing stated. Helper for the
// "passes" cases so each test states only what it changes.
function resolved(over: Partial<DraftCampaignWire> = {}): DraftCampaignWire {
  return draft({
    column_query: "input",
    column_ground_truth: "gt",
    task_description: "map names to codes",
    resolved: {
      "column.query": "confirmed",
      "column.ground_truth": "confirmed",
      ...CONFIG_CONFIRMED,
      task_description: "confirmed",
    },
    ...over,
  });
}

describe("originReadiness", () => {
  it("flags unset columns AND the unset task framing; config knobs auto-pass", () => {
    const r = originReadiness(draft());
    expect(r.complete).toBe(false);
    // The config knobs auto-confirm, so only the columns + task framing gap.
    expect(new Set(r.gaps.map((g) => g.field))).toEqual(
      new Set(["column.query", "column.ground_truth", "task_description"]),
    );
    const col = r.gaps.find((g) => g.field === "column.query");
    expect(col?.hint).toContain("input, gt");
  });

  it("passes once columns are confirmed members AND the framing is stated", () => {
    const r = originReadiness(resolved());
    expect(r.complete).toBe(true);
    expect(r.gaps).toHaveLength(0);
  });

  it("blocks on a proposed (low-confidence) framing until confirmed", () => {
    const r = originReadiness(
      resolved({
        task_description: "maybe map codes",
        resolved: {
          "column.query": "confirmed",
          "column.ground_truth": "confirmed",
          ...CONFIG_CONFIRMED,
          task_description: "proposed",
        },
      }),
    );
    const t = r.gaps.find((g) => g.field === "task_description");
    expect(t?.reason).toBe("proposed_unconfirmed");
  });

  it("does not pass when a confirmed column is not a member of headers", () => {
    const r = originReadiness(resolved({ column_query: "ghost" }));
    expect(r.complete).toBe(false);
    expect(r.gaps.map((g) => g.field)).toEqual(["column.query"]);
  });
});

describe("questionPatch / questionOptions (resolver answer-back loop)", () => {
  it("maps each field id to its draft-patch key", () => {
    expect(questionPatch("column.query", "input")).toEqual({ column_query: "input" });
    expect(questionPatch("task_description", "map codes")).toEqual({
      task_description: "map codes",
    });
    expect(questionPatch("max_rounds", "8")).toEqual({ max_rounds: 8 });
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
        task_description: "map lab-test names to their codes",
      }),
    );
    expect(text).toContain("map lab-test names to their codes");
    expect(text).toContain("TermNorm pipeline");
    expect(text).toContain("exact match");
    expect(text).toContain("5 rounds");
  });
});
