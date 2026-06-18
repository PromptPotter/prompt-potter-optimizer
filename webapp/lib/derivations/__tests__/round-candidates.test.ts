import { describe, expect, it } from "vitest";
import { loadCycleFixture } from "@/lib/test-utils/fixtures";
import {
  closedRoundNumbers,
  groupByRound,
  roundCandidates,
} from "../round-candidates";
import { availableRounds } from "../round-axis";

describe("roundCandidates — l2_terminal fixture", () => {
  // Reproduces the operator's justlogic__ca6d4d/cycle_2451d3cf6ebc exit
  // shape: origin (round 0) + 4 fully-scored rounds + a round-5 stub with
  // empty candidates (closed mid-L2 without l1_score firing). Per the
  // derivation contract:
  //
  //   - Origin is round 0 in `dash.rounds[]` — a single candidate labelled
  //     "C0" that flows through the same history loop as every round.
  //   - Historical rounds 1–4 each contribute their two candidates.
  //   - Round 5 is in `dash.rounds[]` but has `candidates: []` and must
  //     be skipped (no rows added AND no suppression of the in-flight
  //     branch for round 5). The in-flight L1_SCORE is also empty here
  //     because no scoring actually happened, so the final shape is
  //     origin + 8 historical candidates = 9 rows total.
  const dash = loadCycleFixture("l2_terminal");
  const rows = roundCandidates(dash);

  it("emits origin as round 0 (C0)", () => {
    const origin = rows.find((r) => r.round === 0);
    expect(origin).toBeDefined();
    expect(origin?.label).toBe("C0");
    expect(origin?.key).toBe("R0.0");
  });

  it("emits every non-empty post-origin round's candidates", () => {
    const historical = rows.filter((r) => r.round > 0);
    // 4 closed rounds × 2 candidates each = 8 rows. Round 5's
    // empty stub contributes nothing.
    expect(historical).toHaveLength(8);
    expect(new Set(historical.map((r) => r.round))).toEqual(
      new Set([1, 2, 3, 4]),
    );
  });

  it("does not emit any inflight row for the L2-terminal round 5", () => {
    const inflight = rows.filter((r) => r.source === "inflight");
    expect(inflight).toHaveLength(0);
  });

  it("preserves selection order — origin (round 0) then ascending rounds", () => {
    const ordered = rows.map((r) => r.key);
    expect(ordered).toEqual([
      "R0.0",
      "R1.0",
      "R1.1",
      "R2.0",
      "R2.1",
      "R3.0",
      "R3.1",
      "R4.0",
      "R4.1",
    ]);
  });

  it("empty round 5 does not suppress the in-flight branch for round 5", () => {
    // Direct exercise of the fix: an empty historical entry must not
    // appear in `historicalRounds` (it has nothing to double-count
    // against the in-flight branch). On this fixture the in-flight
    // L1_SCORE is also empty, so no inflight rows render — but the
    // gate must not block them on principle. Verified indirectly by
    // confirming the rows count is exactly 1 origin + 8 historical
    // with no spurious round-5 placeholder slot.
    const round5 = rows.filter((r) => r.round === 5);
    expect(round5).toHaveLength(0);
  });

  it("availableRounds excludes the empty L2-terminal round 5 from completed", () => {
    // The other half of the fix: the empty round-5 stub must not be
    // advertised as a completed round, or `useEffectiveRound` falls back
    // to it as `lastCompleted` and the round-scoped surfaces blank/hang on
    // a round with no fitness data. Terminal cycle ⇒ isLive false.
    const axis = availableRounds(dash, false);
    expect(axis.completed).toEqual([0, 1, 2, 3, 4]);
    expect(axis.live).toBeNull();
  });

  it("closedRoundNumbers is the shared 'closed with fitness data' set — excludes the empty round 5", () => {
    // Single definition behind both `availableRounds.completed` and the
    // candidate spine's in-flight suppression. An empty L2/L3-terminal round
    // carries no fitness data, so it is NOT closed here (it must not mask the
    // in-flight branch nor advertise as a selectable round). Distinct from
    // `useRoundSource`'s on-disk presence check, which DOES include it.
    expect(closedRoundNumbers(dash)).toEqual(new Set([0, 1, 2, 3, 4]));
  });

  it("groupByRound buckets the same spine rows without recomputing the merge", () => {
    const byRound = groupByRound(rows);
    // Every emitted row lands under its round; the empty round 5 has no bucket.
    expect([...byRound.keys()].sort((a, b) => a - b)).toEqual([0, 1, 2, 3, 4]);
    expect(byRound.get(0)?.map((r) => r.key)).toEqual(["R0.0"]);
    expect(byRound.get(1)?.map((r) => r.key)).toEqual(["R1.0", "R1.1"]);
    expect(byRound.get(5)).toBeUndefined();
    // Same total as the flat spine — pure regrouping, no rows added or dropped.
    const grouped = [...byRound.values()].reduce((n, b) => n + b.length, 0);
    expect(grouped).toBe(rows.length);
  });
});
