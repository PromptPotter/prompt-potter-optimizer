import { describe, expect, it } from "vitest";
import { foldStepTimings, shownSeconds } from "../sample-clock";
import type { SampleRow } from "@/lib/types";

function row(over: Partial<SampleRow>): SampleRow {
  return {
    key: "k",
    round: 0,
    candidate_id: "c",
    sample_id: 1,
    status: "HIT",
    cached: false,
    query: "",
    predicted: "",
    ground_truth: "",
    terminal_node: "",
    elapsed_s: null,
    cost_s: null,
    cache_share: null,
    ...over,
  };
}

describe("foldStepTimings", () => {
  it("sums the per-node entries", () => {
    expect(foldStepTimings({ agent: 113.2, judge: 1.8 })).toBeCloseTo(115);
  });

  // The subtlety worth a test: an unpriced cell must not read as a free one, so an empty map is
  // ABSENT rather than 0.0 — the same arm `recorded_cost_s` takes.
  it("keeps an empty or absent map null rather than 0", () => {
    expect(foldStepTimings({})).toBeNull();
    expect(foldStepTimings(undefined)).toBeNull();
    expect(foldStepTimings([1, 2])).toBeNull();
  });
});

describe("shownSeconds", () => {
  it("shows what a fresh row took", () => {
    expect(shownSeconds(row({ elapsed_s: 317.1, cost_s: 316.8 }))).toBe(317.1);
  });

  // A replay occupied no clock, so its own elapsed reading is a true 0.0 and the number worth
  // showing is what the cell cost when it was measured.
  it("shows what a replayed row cost when it was measured", () => {
    expect(shownSeconds(row({ cached: true, elapsed_s: 0, cost_s: 113.2 }))).toBe(113.2);
  });

  it("stays null where the row recorded no clock at all", () => {
    expect(shownSeconds(row({ cached: true }))).toBeNull();
    expect(shownSeconds(row({}))).toBeNull();
  });
});
