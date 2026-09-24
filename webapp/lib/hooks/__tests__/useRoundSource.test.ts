import { describe, it, expect } from "vitest";
import { isLiveRound } from "@/lib/hooks/useRoundSource";
import type { DashboardSnapshot } from "@/lib/poll";

// `current_round.round` lingers on a closed round until the next one scores, so the guard keys
// off closure into `rounds[]`.

function dash(currentRoundNum: number, closedRounds: number[]): DashboardSnapshot {
  return {
    current_round: { round: currentRoundNum },
    rounds: closedRounds.map((round) => ({ round, candidates: [] })),
  } as unknown as DashboardSnapshot;
}

describe("isLiveRound closure guard", () => {
  it("treats a closed round as historical even when it equals current_round.round", () => {
    expect(isLiveRound(dash(3, [1, 2, 3]), 3)).toBe(false);
  });

  it("treats the genuine in-flight round (not yet in rounds[]) as live", () => {
    expect(isLiveRound(dash(4, [1, 2, 3]), 4)).toBe(true);
  });

  it("treats an explicitly selected earlier completed round as historical", () => {
    expect(isLiveRound(dash(4, [1, 2, 3]), 2)).toBe(false);
  });

  it("is never live for a null round", () => {
    expect(isLiveRound(dash(4, [1, 2, 3]), null)).toBe(false);
  });
});
