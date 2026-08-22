import { describe, expect, it } from "vitest";
import { degradedRoundNotices } from "../round-health";
import type { DashboardSnapshot } from "@/lib/poll";
import type { DegradationHealth } from "@/lib/api/types";

function health(
  grade: DegradationHealth["grade"],
  over: Partial<DegradationHealth> = {},
): DegradationHealth {
  return {
    grade,
    cause: grade === "healthy" ? null : "degraded",
    samples: 20,
    structural_count: 0,
    transient_count: 5,
    no_result_count: 0,
    hole_count: 0,
    answer_modal_share: null,
    degraded_rate: 0.25,
    consecutive_degraded_rounds: 1,
    prior_clean_rounds: 5,
    dominant_node: "web_search",
    node_failure_rates: {},
    node_warnings: {},
    // The producer fills this for `degraded` as well as `critical`; the notice renders it
    // verbatim rather than composing one, which is how the structural/transient split got
    // stated backwards in the browser.
    suggested_action:
      "web_search degraded on 25% of samples, all transient. The numbers are soft but " +
      "usable; no action needed if the next round comes back clean.",
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
    expect(out[0]!.round).toBe(2);
    expect(out[0]!.detail).toBe(health("degraded").suggested_action);
  });

  it("sorts by round", () => {
    const out = degradedRoundNotices(
      dash([
        { round: 2, health: health("degraded") },
        { round: 0, health: health("degraded") },
      ]),
    );
    expect(out.map((d) => d.round)).toEqual([0, 2]);
  });
});
