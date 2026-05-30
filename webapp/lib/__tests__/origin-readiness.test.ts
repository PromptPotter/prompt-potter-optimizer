import { describe, expect, it } from "vitest";
import type { DraftCampaignWire } from "../api";
import { originReadiness, plainLanguageRecap } from "../origin-readiness";

function draft(over: Partial<DraftCampaignWire> = {}): DraftCampaignWire {
  return {
    draft_id: "d_0123456789abcdef",
    slug: "labtests",
    sample_preview: [{ query: "Na", ground_truth: "Sodium" }],
    n_samples: 42,
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
    resolved: { "column.query": "unset", "column.ground_truth": "unset" },
    created_at: "2026-05-30T00:00:00Z",
    updated_at: "2026-05-30T00:00:00Z",
    ...over,
  };
}

describe("originReadiness", () => {
  it("flags both columns as unset gaps on a fresh non-literal upload", () => {
    const r = originReadiness(draft());
    expect(r.complete).toBe(false);
    expect(r.gaps.map((g) => g.field)).toEqual(["column.query", "column.ground_truth"]);
    expect(r.gaps.every((g) => g.reason === "unset")).toBe(true);
    // Hint lists the available headers so the operator knows what to pick.
    expect(r.gaps[0].hint).toContain("input, gt");
  });

  it("passes once both columns are confirmed members of headers", () => {
    const r = originReadiness(
      draft({
        column_query: "input",
        column_ground_truth: "gt",
        resolved: { "column.query": "confirmed", "column.ground_truth": "confirmed" },
      }),
    );
    expect(r.complete).toBe(true);
    expect(r.gaps).toHaveLength(0);
  });

  it("reports a proposed-unconfirmed gap distinct from unset", () => {
    const r = originReadiness(
      draft({
        column_query: "input",
        resolved: { "column.query": "proposed", "column.ground_truth": "unset" },
      }),
    );
    const q = r.gaps.find((g) => g.field === "column.query");
    expect(q?.reason).toBe("proposed_unconfirmed");
  });

  it("does not pass when the confirmed value is not a member of headers", () => {
    // A stale confirm whose column was renamed out of the upload still blocks.
    const r = originReadiness(
      draft({
        column_query: "ghost",
        column_ground_truth: "gt",
        resolved: { "column.query": "confirmed", "column.ground_truth": "confirmed" },
      }),
    );
    expect(r.complete).toBe(false);
    expect(r.gaps.map((g) => g.field)).toEqual(["column.query"]);
  });
});

describe("plainLanguageRecap", () => {
  it("restates the campaign in jargon-free terms from confirmed fields", () => {
    const text = plainLanguageRecap(
      draft({
        column_query: "input",
        column_ground_truth: "gt",
        optimizer_model: "openai/gpt-oss-120b",
        task_description: "map lab-test names to their codes",
      }),
    );
    expect(text).toContain("map lab-test names to their codes");
    expect(text).toContain("TermNorm pipeline");
    expect(text).toContain("exact match");
    expect(text).toContain("openai/gpt-oss-120b");
    expect(text).toContain("5 rounds");
  });
});
