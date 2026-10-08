import { describe, expect, it } from "vitest";
import { fitnessTrend, primaryMetric } from "@/lib/derivations";
import type { RoundSummary } from "@/lib/api/types";

const round = (r: number, accuracy: number, composite_fitness: number, total = 20): RoundSummary =>
  ({
    round: r,
    accuracy,
    composite_fitness,
    total,
    ability: null,
    ability_on_series_ruler: false,
    best_so_far: null,
    bench: null,
    improved: null,
    electable_count: null,
    verdict_reason: null,
    separable: null,
    stamps_theta: true,
    overlap: null,
    panel_precision: null,
    panel_precision_verdict: null,
    optimizer_facts: [],
    candidates: [],
    selection: [],
    health: null,
  }) as RoundSummary;

describe("fitnessTrend", () => {
  it("plots what each round measured, never a value no round scored", () => {
    const rounds = [round(0, 0.5, 0.5), round(1, 0.83, 0.83), round(2, 0.5, 0.5)];
    const { points } = fitnessTrend(rounds);

    expect(points.map((p) => p.composite)).toEqual([0.5, 0.83, 0.5]);
    // The invariant that matters: every plotted point is a number some round scored.
    const measured = new Set(rounds.map((r) => r.accuracy));
    expect(points.every((p) => measured.has(p.composite))).toBe(true);
  });

  // A held round crowns nobody, and a peer optimizer's round has no crowned arm to read n off.
  it("reads each point's n off the round's own measured total", () => {
    const { points } = fitnessTrend([round(0, 0.4, 0.4, 30), round(1, 0.5, 0.5, 12)]);
    expect(points.map((p) => p.n)).toEqual([30, 12]);
  });

  // The trend's round line is the composite column; a round with nothing readable is a gap.
  it("draws the round composite, a gap staying a gap", () => {
    const blank = { ...round(2, 0.9, 0.0), accuracy: null } as RoundSummary;
    const { points } = fitnessTrend([round(0, 0.5, 0.4), round(1, 0.8, 0.6), blank]);
    expect(points.map((p) => p.composite)).toEqual([0.4, 0.6, null]);
  });

});

describe("primaryMetric", () => {
  // A node paints the number the crown was elected on, not accuracy's canonical-first slot.
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
