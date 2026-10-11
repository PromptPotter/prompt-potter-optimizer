import { describe, it, expect } from "vitest";
import { isRoundUnfiled } from "@/lib/hooks/useRound";
import { currentRound, dash as dashboard, summaryRound } from "@/lib/test-fixtures";

// `current_round.round` lingers on a filed round until the next one scores.

const dash = (currentRoundNum: number, filedRounds: number[]) =>
  dashboard({
    current_round: currentRound({ round: currentRoundNum }),
    rounds: filedRounds.map((round) => summaryRound({ round })),
  });

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
