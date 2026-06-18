import { describe, expect, it } from "vitest";
import { degradedRoundNotices } from "../round-health";
import type { DashboardSnapshot } from "@/lib/poll";
import type { DegradationHealth } from "@/lib/api/types";

function health(grade: string, over: Partial<DegradationHealth> = {}): DegradationHealth {
  return {
    grade,
    reasons: grade === "degraded" ? ["degraded"] : [],
    samples: 20,
    structural_count: 0,
    transient_count: 5,
    degraded_rate: 0.25,
    consecutive_degraded_rounds: 1,
    prior_clean_rounds: 5,
    dominant_node: "web_search",
    node_failure_rates: {},
    ci_lo: 0.5,
    ci_hi: 0.9,
    suggested_action: null,
    ...over,
  };
}

function dash(rounds: { round: number; health: DegradationHealth | null }[]): DashboardSnapshot {
  return {
    rounds: rounds.map((r) => ({
      round: r.round,
      accuracy: 0.6,
      composite_fitness: 0.6,
      candidates: [],
      selection: [],
      health: r.health,
    })),
  } as unknown as DashboardSnapshot;
}

describe("degradedRoundNotices", () => {
  it("returns nothing for null / empty / all-healthy", () => {
    expect(degradedRoundNotices(null)).toEqual([]);
    expect(degradedRoundNotices(dash([{ round: 1, health: health("healthy") }]))).toEqual([]);
    expect(degradedRoundNotices(dash([{ round: 1, health: null }]))).toEqual([]);
  });

  it("surfaces only `degraded` rounds — `critical` is owned by the banner", () => {
    const out = degradedRoundNotices(
      dash([
        { round: 1, health: health("healthy") },
        { round: 2, health: health("degraded") },
        { round: 3, health: health("critical") },
      ]),
    );
    expect(out).toHaveLength(1);
    expect(out[0].round).toBe(2);
    expect(out[0].detail).toContain("web_search");
    expect(out[0].detail).toContain("25%");
  });

  it("phrases the under-probed (untested) origin distinctly and sorts by round", () => {
    const out = degradedRoundNotices(
      dash([
        { round: 2, health: health("degraded") },
        { round: 0, health: health("degraded", { reasons: ["untested"], dominant_node: null }) },
      ]),
    );
    expect(out.map((d) => d.round)).toEqual([0, 2]);
    expect(out[0].detail).toContain("under-probed");
  });
});
