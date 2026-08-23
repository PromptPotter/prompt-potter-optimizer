import { describe, expect, it } from "vitest";
import { fitnessTrend, primaryMetric } from "@/lib/derivations";
import type { RoundSummary } from "@/lib/api/types";

const round = (r: number, accuracy: number, composite_fitness: number): RoundSummary =>
  ({
    round: r,
    accuracy,
    composite_fitness,
    ability: null,
    improved: null,
    electable_count: null,
    verdict_reason: null,
    overlap: null,
    panel_precision: null,
    candidates: [],
    selection: [],
    health: null,
  }) as RoundSummary;

describe("fitnessTrend", () => {
  it("plots what each round measured, never a value no round scored", () => {
    // This test used to assert the opposite: that the trend follows `cumulative_accuracy`
    // and flattens the per-round swing. That series was a sample-keyed union of rows
    // measured by DIFFERENT configurations, so the line could sit above everything the
    // cycle had measured — the sidebar read `57%→78%` for a run whose best candidate
    // reached 0.679. A test that pins a fabricated number as "the true rate" is how the
    // defect survived review, so the assertion is inverted rather than repaired.
    const rounds = [round(0, 0.5, 0.5), round(1, 0.83, 0.83), round(2, 0.5, 0.5)];
    const { points } = fitnessTrend(rounds);

    expect(points.map((p) => p.composite)).toEqual([0.5, 0.83, 0.5]);
    // The invariant that matters: every plotted point is a number some round scored.
    const measured = new Set(rounds.map((r) => r.accuracy));
    expect(points.every((p) => measured.has(p.composite))).toBe(true);
  });

  it("running-best folds over the measured series", () => {
    const { best } = fitnessTrend([round(0, 0.4, 0.4), round(1, 0.9, 0.9), round(2, 0.3, 0.3)]);
    expect(best).toEqual([0.4, 0.9, 0.9]);
  });
});

describe("primaryMetric", () => {
  // A node paints ONE number, and it has to be the one the bars are shouting. Accuracy is
  // always seeded on and sorts first in canonical order, so before the `elected` argument
  // this answered "accuracy" forever — every dendrogram node labelled with a rate while the
  // crown beside it was decided on θ.
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
