import { describe, it, expect } from "vitest";
import { isLiveRound } from "@/lib/hooks/useRoundSource";
import type { DashboardSnapshot } from "@/lib/poll";

// Regression: the live/historical guard must key off round *closure*, not
// topology equality with `current_round.round`. The round counter advances
// only at scoring/close, so `current_round.round` lingers on an already-closed
// round number (between a round closing and the next round scoring, after an
// interrupt during next-round prep, and at some finish states). A round that
// has migrated into `dash.rounds[]` is historical even while it still equals
// `roundOf(dash)` — otherwise its samples/freq/node-detail get misrouted to the
// now-empty in-flight projection.

function dash(currentRoundNum: number, closedRounds: number[]): DashboardSnapshot {
  return {
    current_round: { round: currentRoundNum },
    rounds: closedRounds.map((round) => ({ round, candidates: [] })),
  } as unknown as DashboardSnapshot;
}

describe("isLiveRound closure guard", () => {
  it("treats a closed round as historical even when it equals current_round.round", () => {
    // round 3 closed into rounds[] AND current_round.round still 3 (interrupted
    // during round-4 prep) — the reported bug's exact shape.
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
