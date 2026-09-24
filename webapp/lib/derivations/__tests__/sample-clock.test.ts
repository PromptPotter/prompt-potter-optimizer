import { describe, expect, it } from "vitest";
import { foldStepTimings } from "../sample-clock";
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
