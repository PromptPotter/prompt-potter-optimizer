import { describe, it, expect } from "vitest";
import { isRoundUnfiled } from "@/lib/hooks/useRoundSource";
import type { DashboardSnapshot } from "@/lib/poll";

// `current_round.round` lingers on a filed round until the next one scores, so the guard keys
// off the round's presence in `rounds[]`.

function dash(currentRoundNum: number, filedRounds: number[]): DashboardSnapshot {
  return {
    current_round: { round: currentRoundNum },
    rounds: filedRounds.map((round) => ({ round, candidates: [] })),
  } as unknown as DashboardSnapshot;
}

describe("isRoundUnfiled", () => {
  it("reads a round in rounds[] as filed even when it equals current_round.round", () => {
    expect(isRoundUnfiled(dash(3, [1, 2, 3]), 3)).toBe(false);
  });

  it("reads the in-flight round (not yet in rounds[]) as unfiled", () => {
    expect(isRoundUnfiled(dash(4, [1, 2, 3]), 4)).toBe(true);
  });

  it("reads an explicitly selected earlier round as filed", () => {
    expect(isRoundUnfiled(dash(4, [1, 2, 3]), 2)).toBe(false);
  });

  it("is never unfiled for a null round", () => {
    expect(isRoundUnfiled(dash(4, [1, 2, 3]), null)).toBe(false);
  });
});
