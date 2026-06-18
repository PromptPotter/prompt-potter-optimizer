import { describe, expect, it } from "vitest";
import { displayFitness, fitnessTrend } from "@/lib/derivations";
import type { RoundSummary } from "@/lib/api/types";

const round = (r: number, accuracy: number, composite_fitness: number): RoundSummary =>
  ({
    round: r,
    accuracy,
    composite_fitness,
    candidates: [],
    selection: [],
    health: null,
  }) as RoundSummary;

describe("displayFitness", () => {
  it("keeps an honest 0 composite — never masks it with accuracy", () => {
    // A validation-failed candidate scores a real 0.0; degrading it to accuracy
    // would hide the failure on the trend/sparkline.
    expect(displayFitness(0, 0.7)).toBe(0);
  });

  it("degrades to accuracy only on genuine absence", () => {
    expect(displayFitness(null, 0.7)).toBe(0.7);
    expect(displayFitness(undefined, 0.7)).toBe(0.7);
  });

  it("uses the active-formula composite when present", () => {
    expect(displayFitness(0.85, 0.7)).toBe(0.85);
  });

  it("returns null when both composite and accuracy are absent (unranked bar)", () => {
    expect(displayFitness(null, null)).toBeNull();
  });
});

describe("fitnessTrend", () => {
  it("plots a real 0 composite as 0, not its accuracy", () => {
    const { points } = fitnessTrend([round(0, 0.5, 0.5), round(1, 0.7, 0)]);
    expect(points.map((p) => p.composite)).toEqual([0.5, 0]);
  });
});
