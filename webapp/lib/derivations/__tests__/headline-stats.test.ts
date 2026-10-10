import { describe, expect, it } from "vitest";
import { fitnessTrend, primaryMetric } from "@/lib/derivations";
import type { ServedRound } from "@/lib/api/types";
import { summaryRound } from "@/lib/test-fixtures";

const round = (r: number, accuracy: number, composite_fitness: number, total = 20): ServedRound =>
  summaryRound({ round: r, accuracy, composite_fitness, total });

describe("fitnessTrend", () => {
  it("plots what each round measured, never a value no round scored", () => {
    const rounds = [round(0, 0.5, 0.5), round(1, 0.83, 0.83), round(2, 0.5, 0.5)];
    const { points } = fitnessTrend(rounds);

    expect(points.map((p) => p.composite)).toEqual([0.5, 0.83, 0.5]);
    const measured = new Set(rounds.map((r) => r.accuracy));
    expect(points.every((p) => measured.has(p.composite))).toBe(true);
  });

  // A held round crowns nobody, and a peer optimizer's round has no crowned arm to read n off.
  it("reads each point's n off the round's own measured total", () => {
    const { points } = fitnessTrend([round(0, 0.4, 0.4, 30), round(1, 0.5, 0.5, 12)]);
    expect(points.map((p) => p.n)).toEqual([30, 12]);
  });

  it("draws the round composite, a gap staying a gap", () => {
    const blank = { ...round(2, 0.9, 0.0), accuracy: null, composite_fitness: null } as ServedRound;
    const { points } = fitnessTrend([round(0, 0.5, 0.4), round(1, 0.8, 0.6), blank]);
    expect(points.map((p) => p.composite)).toEqual([0.4, 0.6, null]);
  });

});

describe("primaryMetric", () => {
  it("prefers the metric the campaign elects on", () => {
    expect(primaryMetric(new Set(["accuracy", "ability"]), "ability")).toBe("ability");
    expect(primaryMetric(new Set(["accuracy", "composite"]), "composite")).toBe("composite");
  });

  it("falls back to canonical order when the elected metric is not shown", () => {
    expect(primaryMetric(new Set(["accuracy", "composite"]), "ability")).toBe("accuracy");
    expect(primaryMetric(new Set(["composite"]), "ability")).toBe("composite");
    expect(primaryMetric(new Set())).toBe("accuracy");
  });
});
