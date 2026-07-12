import { describe, expect, it } from "vitest";
import {
  measuredUniverse,
  roundMeasuredSets,
  sameSampleSet,
  toggleInSet,
} from "@/lib/sample-set";
import { orderAtStep, seedFromOrder } from "@/lib/derivations";
import type { RoundSummary } from "@/lib/api/types";
import type { SampleOrderStep } from "@/lib/types";

const round = (r: number, selection: number[]): RoundSummary =>
  ({
    round: r,
    accuracy: 0,
    composite_fitness: 0,
    cumulative_accuracy: 0,
    cumulative_theta: null,
    calibration_model: null,
    outer_verdict: null,
    candidates: [],
    selection,
    health: null,
  }) as RoundSummary;

describe("sample-set primitives", () => {
  it("sameSampleSet is order-insensitive and null-safe", () => {
    expect(sameSampleSet([1, 2, 3], [3, 2, 1])).toBe(true);
    expect(sameSampleSet([1, 2], [1, 2, 3])).toBe(false);
    expect(sameSampleSet(null, [1])).toBe(false);
    expect(sameSampleSet([], [])).toBe(true);
  });

  it("toggleInSet adds/removes and returns a sorted set", () => {
    expect(toggleInSet([1, 3], 2)).toEqual([1, 2, 3]);
    expect(toggleInSet([1, 2, 3], 2)).toEqual([1, 3]);
  });

  it("measuredUniverse unions selections, unique + sorted", () => {
    expect(measuredUniverse([round(1, [2, 0]), round(2, [0, 1])])).toEqual([0, 1, 2]);
  });

  it("roundMeasuredSets skips empty rounds and sorts each", () => {
    expect(roundMeasuredSets([round(1, [3, 1]), round(2, [])])).toEqual([
      { round: 1, ids: [1, 3] },
    ]);
  });
});

describe("orderAtStep / seedFromOrder", () => {
  const timeline: SampleOrderStep[] = [
    { step: 0, current_sample_id: 9, computed: [], planned: [9, 4, 7] },
    { step: 1, current_sample_id: 4, computed: [9], planned: [4, 7] },
  ];

  it("reads the frozen order from the timeline when present", () => {
    const o = orderAtStep(timeline, [9, 4, 7], 4, 2);
    expect(o).toEqual({ computed: [9], current: 4, planned: [7] });
  });

  it("falls back to splitting executed selection by position", () => {
    const o = orderAtStep([], [9, 4, 7], 7, 3);
    expect(o).toEqual({ computed: [9, 4], current: 7, planned: [] });
  });

  it("seedFromOrder respects measured vs all", () => {
    const o = { computed: [9], current: 4, planned: [7] };
    expect(seedFromOrder(o, "measured")).toEqual([9, 4]);
    expect(seedFromOrder(o, "all")).toEqual([9, 4, 7]);
  });
});
