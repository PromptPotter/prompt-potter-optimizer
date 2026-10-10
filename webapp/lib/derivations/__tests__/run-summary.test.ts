import { describe, expect, it } from "vitest";
import { READING_STATE_LABELS } from "@/lib/api/types.generated";
import { runSummary } from "../run-summary";
import {
  armReading,
  dash,
  pairedReading,
  runStanding,
  servedLabel,
  summaryCandidate,
  summaryRound,
} from "@/lib/test-fixtures";

const arm = (round: number, idx: number, changes_description = "") =>
  summaryCandidate({
    reading: armReading({
      arm: { round, label: servedLabel(round, idx), candidate_id: `r${round}c${idx}` },
      changes_description,
    }),
  });

describe("runSummary", () => {
  const finished = dash({
    cycle_id: "cycle_9",
    stop_reason: "lives_exhausted",
    next_step: "Raise the lives and resume.",
    rounds: [
      summaryRound({ round: 0, candidates: [arm(0, 0)] }),
      summaryRound({ round: 1 }),
      summaryRound({
        round: 2,
        candidates: [arm(2, 0, "a losing arm's edit"), arm(2, 1, "step-by-step thinking style")],
      }),
    ],
    run_standing: runStanding(2, "C2.2", {
      vs_origin: pairedReading(0.12, [0.01, 0.23], { rateA: 0.5 }),
    }),
  });

  it("snapshots the served winner against its origin, never its own round's level", () => {
    const s = runSummary(finished);
    expect(s?.winnerLabel).toBe("C2.2");
    expect(s?.vsOrigin).toBe("50% → 62%");
    expect(s?.changes).toBe("step-by-step thinking style");
  });

  it("takes the served count of closed rounds", () => {
    expect(runSummary(finished)?.rounds).toBe(2);
  });

  it("carries the served status word, the next step and the cycle it froze verbatim", () => {
    const s = runSummary(finished);
    expect(s?.state).toBe(finished.status.label);
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
    );
    expect(lost?.winnerLabel).toBe("C0");
    expect(lost?.rounds).toBe(1);
    expect(lost?.vsOrigin).toBe(READING_STATE_LABELS.same_individual);
  });

  it("returns null without a cycle, and a winner-less summary before round 0 closes", () => {
    expect(runSummary(null)).toBeNull();
    const bare = runSummary(dash({}));
    expect(bare?.winnerLabel).toBeNull();
    expect(bare?.rounds).toBeNull();
  });
});
