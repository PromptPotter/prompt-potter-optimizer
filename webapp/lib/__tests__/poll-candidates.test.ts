import { describe, expect, it } from "vitest";
import { liveL1Candidates, type DashboardSnapshot } from "../poll";

// Regression guard for React #185 (post-login render loop): the no-candidate
// path must return a STABLE reference. A fresh `[]` per call churned the
// FitnessPanel Set chain into an unbounded setState loop.
describe("liveL1Candidates", () => {
  it("returns the same reference on the no-candidate path", () => {
    expect(liveL1Candidates(null)).toBe(liveL1Candidates(null));
    const noNodes: DashboardSnapshot = { current_round: {} };
    expect(liveL1Candidates(noNodes)).toBe(liveL1Candidates(null));
    const noL1: DashboardSnapshot = { current_round: { nodes: {} } };
    expect(liveL1Candidates(noL1)).toBe(liveL1Candidates(null));
  });

  it("returns the candidates array when present", () => {
    const dash: DashboardSnapshot = {
      current_round: { nodes: { l1_score: { output: { candidates: [{ idx: 0 }] } } } },
    };
    expect(liveL1Candidates(dash)).toHaveLength(1);
  });
});
