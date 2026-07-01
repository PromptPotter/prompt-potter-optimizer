import { describe, expect, it } from "vitest";
import { displayFitness, fitnessTrend } from "@/lib/derivations";
import type { RoundSummary } from "@/lib/api/types";

const round = (
  r: number,
  accuracy: number,
  composite_fitness: number,
  cumulative_accuracy: number = accuracy,
): RoundSummary =>
  ({
    round: r,
    accuracy,
    composite_fitness,
    cumulative_accuracy,
    cumulative_theta: null,
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
  it("plots the honest cumulative frontier, not the swinging per-round subset number", () => {
    // Per-round `accuracy` swings 0.5 → 0.83 → 0.5 purely from re-picking a fresh
    // hard-first subset each round (per_round_resubset), reading as a false
    // "great start → decay". `cumulative_accuracy` stays flat at 0.5 — the true
    // rate. The trend must follow cumulative so the illusion never shows.
    const { points } = fitnessTrend([
      round(0, 0.5, 0.5, 0.5),
      round(1, 0.83, 0.83, 0.5),
      round(2, 0.5, 0.5, 0.5),
    ]);
    expect(points.map((p) => p.composite)).toEqual([0.5, 0.5, 0.5]);
  });

  it("running-best folds over the cumulative series", () => {
    const { best } = fitnessTrend([
      round(0, 0.4, 0.4, 0.4),
      round(1, 0.9, 0.9, 0.6),
      round(2, 0.3, 0.3, 0.55),
    ]);
    expect(best).toEqual([0.4, 0.6, 0.6]);
  });
});
