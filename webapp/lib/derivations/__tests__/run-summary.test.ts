import { describe, expect, it } from "vitest";
import { READING_STATE_LABELS } from "@/lib/api/types.generated";
import { runSummary } from "../run-summary";
import {
  armReading,
  dash,
  hierarchyCourse,
  hierarchyState,
  pairedReading,
  runStanding,
  servedLabel,
  summaryCandidate,
  summaryRound,
} from "@/lib/test-fixtures";

const arm = (round: number, idx: number, changes_description = "") =>
  summaryCandidate({
    reading: armReading({
      arm: { round, label: servedLabel(round, idx), key: `r${round}c${idx}` },
      changes_description,
    }),
  });

describe("runSummary", () => {
  const course = hierarchyCourse({
    address: "camp::cycle_9",
    state: hierarchyState({
      status: { label: "served out of lives", mark: "failed" },
      stop_reason: "lives_exhausted",
      next_step: "Raise the lives and resume.",
    }),
  });
  const finished = dash({
    cycle_id: "cycle_9",
    stop_reason: "lives_exhausted",
    rounds: [
      summaryRound({ round: 0, candidates: [arm(0, 0)] }),
      summaryRound({ round: 1 }),
      summaryRound({
        round: 2,
        candidates: [arm(2, 0, "a losing arm's edit"), arm(2, 1, "step-by-step thinking style")],
      }),
    ],
    run_standing: runStanding(2, "C2.2", {
      selection: { round: 2, label: "C2.2", key: "r2c1" },
      vs_origin: pairedReading(0.12, [0.01, 0.23], { rateA: 0.5 }),
    }),
  });

  it("snapshots the served winner against its origin, never its own round's level", () => {
    const s = runSummary(finished, course);
    expect(s?.winnerLabel).toBe("C2.2");
    expect(s?.vsOrigin).toBe("50% → 62%");
    expect(s?.changes).toBe("step-by-step thinking style");
  });

  it("takes the served count of closed rounds", () => {
    expect(runSummary(finished, course)?.rounds).toBe(2);
  });

  it("carries the course's served status word and next step, and the cycle it froze", () => {
    const s = runSummary(finished, course);
    expect(s?.state).toBe("served out of lives");
    expect(s?.nextStep).toBe("Raise the lives and resume.");
    expect(s?.cycleId).toBe("cycle_9");
  });

  it("names the origin when every challenger lost", () => {
    const lost = runSummary(
      dash({
        run_standing: runStanding(0, "C0", { rounds_closed: 1 }),
        rounds: [
          summaryRound({ round: 0, candidates: [arm(0, 0)] }),
          summaryRound({ round: 1, improved: false, candidates: [arm(1, 0), arm(1, 1)] }),
        ],
      }),
      course,
    );
    expect(lost?.winnerLabel).toBe("C0");
    expect(lost?.rounds).toBe(1);
    expect(lost?.vsOrigin).toBe(READING_STATE_LABELS.same_individual);
  });

  it("returns null without a cycle, and a winner-less summary before round 0 closes", () => {
    expect(runSummary(null, course)).toBeNull();
    expect(runSummary(finished, null)).toBeNull();
    const bare = runSummary(dash({}), course);
    expect(bare?.winnerLabel).toBeNull();
    expect(bare?.rounds).toBeNull();
  });
});
