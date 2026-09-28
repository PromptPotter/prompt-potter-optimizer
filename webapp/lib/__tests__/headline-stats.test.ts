import { describe, expect, it } from "vitest";
import { fitnessTrend, primaryMetric } from "@/lib/derivations";
import type { BenchReading, BenchScore, RoundSummary } from "@/lib/api/types";

const round = (r: number, accuracy: number, composite_fitness: number, total = 20): RoundSummary =>
  ({
    round: r,
    accuracy,
    composite_fitness,
    total,
    ability: null,
    improved: null,
    electable_count: null,
    verdict_reason: null,
    separable: null,
    stamps_theta: true,
    overlap: null,
    panel_precision: null,
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

  it("running-best folds over the measured series", () => {
    const { best } = fitnessTrend([round(0, 0.4, 0.4), round(1, 0.9, 0.9), round(2, 0.3, 0.3)]);
    expect(best).toEqual([0.4, 0.9, 0.9]);
  });

  // The trend's round line is the composite column; the masthead's BEST stays accuracy's.
  it("draws the round composite and folds best over accuracy, a gap staying a gap", () => {
    const blank = { ...round(2, 0.9, 0.0), accuracy: null } as RoundSummary;
    const { points, best } = fitnessTrend([round(0, 0.5, 0.4), round(1, 0.8, 0.6), blank]);
    expect(points.map((p) => p.composite)).toEqual([0.4, 0.6, null]);
    expect(best).toEqual([0.5, 0.8, 0.8]);
  });

  it("plots θ only on a round whose selector elects on it", () => {
    const ability = {
      theta: 0.3,
      se: null,
      ruler_id: "r1",
      ruler_n: 20,
      ruler_span: null,
      round_span: null,
      calibration_model: null,
      caveat: null,
    };
    const potter = { ...round(0, 0.5, 0.5), ability } as RoundSummary;
    const peer = { ...round(1, 0.6, 0.6), ability, stamps_theta: false } as RoundSummary;
    expect(fitnessTrend([potter, peer]).points.map((p) => p.theta)).toEqual([0.3, null]);
  });

  // The bench grades two picks, never every round: each reading lands on the round it names.
  it("places the bench's origin and selection on their own rounds and nowhere else", () => {
    const reading = (r: number, composite_fitness: number): BenchReading => ({
      round: r,
      sp_hash: `sp${r}`,
      accuracy: composite_fitness,
      composite_fitness,
      ci_lo: null,
      ci_hi: null,
      n_scored: 10,
      run_id: `bench_${r}`,
      stopped: null,
    });
    const bench: BenchScore = {
      bench_size: 10,
      origin: reading(0, 0.35),
      selected: reading(2, 0.7),
      missing_reason: null,
      lift: 0.35,
      lift_ci_lo: null,
      lift_ci_hi: null,
    };
    const rounds = [round(0, 0.5, 0.5), round(1, 0.6, 0.6), round(2, 0.9, 0.9)];
    expect(fitnessTrend(rounds, null, bench).points.map((p) => p.bench)).toEqual([0.35, null, 0.7]);
    const kept = { ...bench, selected: reading(0, 0.35), lift: 0.0 };
    expect(fitnessTrend(rounds, null, kept).points.map((p) => p.bench)).toEqual([0.35, null, null]);
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
