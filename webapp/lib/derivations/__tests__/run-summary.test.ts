import { describe, expect, it } from "vitest";
import { runSummary } from "../run-summary";
import { dash, servedLabel, summaryCandidate, summaryRound } from "@/lib/test-fixtures";

describe("runSummary", () => {
  const finished = dash({
    cycle_id: "cycle_9",
    stop_reason: "lives_exhausted",
    ability_delta: 0.41,
    rounds: [
      summaryRound({
        round: 0,
        candidates: [
          summaryCandidate({
            candidate_id: "c0",
            label: servedLabel(0, 0),
            accuracy: 0.62,
            is_winner: true,
          }),
        ],
      }),
      // Closed but empty — an L2/L3-terminal round measured nothing.
      summaryRound({ round: 1 }),
      summaryRound({
        round: 2,
        candidates: [
          summaryCandidate({ candidate_id: "a", label: servedLabel(2, 0) }),
          summaryCandidate({
            candidate_id: "b",
            label: servedLabel(2, 1),
            accuracy: 0.74,
            matched_parent_accuracy: 0.6,
            changes_description: "step-by-step thinking style",
            is_winner: true,
          }),
        ],
      }),
    ],
  });

  it("snapshots the champion off the most recent served crown", () => {
    const s = runSummary(finished);
    expect(s?.championLabel).toBe("C2.2");
    expect(s?.accuracy).toBe(0.74);
    // The floor it was JUDGED against, not round 0's full-set rate.
    expect(s?.parentAccuracy).toBe(0.6);
    expect(s?.changes).toBe("step-by-step thinking style");
  });

  it("counts only rounds that closed WITH candidates", () => {
    expect(runSummary(finished)?.rounds).toBe(2);
  });

  it("carries the SERVED lift and stop reason verbatim", () => {
    const s = runSummary(finished);
    expect(s?.abilityDelta).toBe(0.41);
    expect(s?.stopReason).toBe("lives_exhausted");
    expect(s?.cycleId).toBe("cycle_9");
  });

  it("leaves an unstamped origin floor null rather than reading it as zero", () => {
    const s = runSummary(
      dash({
        rounds: [
          summaryRound({
            round: 1,
            candidates: [summaryCandidate({ candidate_id: "x", accuracy: 0.5, is_winner: true })],
          }),
        ],
      }),
    );
    expect(s?.accuracy).toBe(0.5);
    expect(s?.parentAccuracy).toBeNull();
  });

  // The fact that keeps "Best = origin" from reading as a broken surface: a run where
  // two challengers were tried and both lost is NOT a run where nothing was tried.
  it("reports the last closed round's verdict, skipping an empty one", () => {
    expect(runSummary(finished)?.lastRound).toEqual({
      round: 2,
      candidates: 2,
      improved: null,
      verdictReason: null,
    });
    const lost = runSummary(
      dash({
        rounds: [
          summaryRound({
            round: 0,
            candidates: [
              summaryCandidate({ candidate_id: "c0", label: servedLabel(0, 0), is_winner: true }),
            ],
          }),
          summaryRound({
            round: 1,
            improved: false,
            candidates: [
              summaryCandidate({ candidate_id: "a", label: servedLabel(1, 0) }),
              summaryCandidate({ candidate_id: "b", label: servedLabel(1, 1) }),
            ],
          }),
        ],
      }),
    );
    expect(lost?.lastRound).toEqual({
      round: 1,
      candidates: 2,
      improved: false,
      verdictReason: null,
    });
    // …and the champion correctly walks back to the origin, which is the pairing the
    // surface has to render as one sentence.
    expect(lost?.championLabel).toBe("C0");
  });

  it("returns null without a cycle, and a champion-less summary before any crown", () => {
    expect(runSummary(null)).toBeNull();
    const bare = runSummary(dash({}));
    expect(bare?.championLabel).toBeNull();
    expect(bare?.rounds).toBe(0);
  });
});
