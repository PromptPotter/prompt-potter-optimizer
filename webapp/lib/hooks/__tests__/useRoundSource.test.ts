// @vitest-environment jsdom
import { describe, it, expect } from "vitest";
import { renderHook } from "@testing-library/react";
import { useRoundSource } from "@/lib/hooks/useRoundSource";
import type { DashboardSnapshot } from "@/lib/poll";

// Regression: the live/historical guard must key off round *closure*, not
// topology equality with `current_round.round`. The round counter advances
// only at scoring/close, so `current_round.round` lingers on an already-closed
// round number (between a round closing and the next round scoring, after an
// interrupt during next-round prep, and at some finish states). A round that
// has migrated into `dash.rounds[]` is historical even while it still equals
// `roundOf(dash)` — otherwise its samples/freq/node-detail get misrouted to the
// now-empty in-flight projection. The viewed path is null so the inner
// round-file fetch idles and we assert the synchronous `isLive` derivation.

function dash(currentRoundNum: number, closedRounds: number[]): DashboardSnapshot {
  return {
    current_round: { round: currentRoundNum },
    rounds: closedRounds.map((round) => ({ round, candidates: [] })),
  } as unknown as DashboardSnapshot;
}

describe("useRoundSource closure guard", () => {
  it("treats a closed round as historical even when it equals current_round.round", () => {
    // round 3 closed into rounds[] AND current_round.round still 3 (interrupted
    // during round-4 prep) — the reported bug's exact shape.
    const { result } = renderHook(() =>
      useRoundSource(null, 3, dash(3, [1, 2, 3])),
    );
    expect(result.current.isLive).toBe(false);
  });

  it("treats the genuine in-flight round (not yet in rounds[]) as live", () => {
    const { result } = renderHook(() =>
      useRoundSource(null, 4, dash(4, [1, 2, 3])),
    );
    expect(result.current.isLive).toBe(true);
  });

  it("treats an explicitly selected earlier completed round as historical", () => {
    const { result } = renderHook(() =>
      useRoundSource(null, 2, dash(4, [1, 2, 3])),
    );
    expect(result.current.isLive).toBe(false);
  });

  it("is never live for a null round", () => {
    const { result } = renderHook(() =>
      useRoundSource(null, null, dash(4, [1, 2, 3])),
    );
    expect(result.current.isLive).toBe(false);
  });
});
