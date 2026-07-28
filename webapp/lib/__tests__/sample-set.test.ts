import { describe, expect, it } from "vitest";
import {
  measuredUniverse,
  roundMeasuredSets,
  sameSampleSet,
  toggleInSet,
} from "@/lib/sample-set";
import { orderAtStep, seedFromOrder } from "@/lib/derivations";
import type { RoundSummary } from "@/lib/api/types";

const round = (r: number, selection: number[]): RoundSummary =>
  ({
    round: r,
    accuracy: 0,
    composite_fitness: 0,
    cumulative_theta: null,
    calibration_model: null,
    improved: null,
    electable_count: null,
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
  it("splits the executed selection by position", () => {
    expect(orderAtStep([9, 4, 7], 4, 2)).toEqual({ computed: [9], current: 4, planned: [7] });
    expect(orderAtStep([9, 4, 7], 7, 3)).toEqual({ computed: [9, 4], current: 7, planned: [] });
  });

  // The regression this replaced: the round's FIRST cell used to be the only one a
  // one-step `sample_order_timeline` matched, so it seeded from the round's INTENDED
  // order (including samples elimination never reached) while every other cell seeded
  // from the MEASURED order. One gesture, two different sample sets. Position 1 must
  // now derive the same way as any other position.
  it("treats the first cell like every other cell", () => {
    expect(orderAtStep([9, 4, 7], 9, 1)).toEqual({ computed: [], current: 9, planned: [4, 7] });
    const first = orderAtStep([9, 4, 7], 9, 1);
    expect(seedFromOrder(first, "measured")).toEqual([9]);
    expect(seedFromOrder(first, "all")).toEqual([9, 4, 7]);
  });

  it("seedFromOrder respects measured vs all", () => {
    const o = { computed: [9], current: 4, planned: [7] };
    expect(seedFromOrder(o, "measured")).toEqual([9, 4]);
    expect(seedFromOrder(o, "all")).toEqual([9, 4, 7]);
  });
});
