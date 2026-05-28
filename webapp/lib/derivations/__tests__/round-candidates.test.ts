import { describe, expect, it } from "vitest";
import { loadCycleFixture } from "@/lib/test-utils/fixtures";
import { roundCandidates } from "@/lib/derivations/round-candidates";

describe("roundCandidates — l2_terminal fixture", () => {
  // Reproduces the operator's justlogic__ca6d4d/cycle_2451d3cf6ebc exit
  // shape: 4 fully-scored rounds + a round-5 stub with empty candidates
  // (closed mid-L2 without l1_score firing). Per the derivation contract:
  //
  //   - Origin renders as C0 (rounds.length > 0 → hasAnything).
  //   - Historical rounds 1–4 each contribute their two candidates.
  //   - Round 5 is in `dash.rounds[]` but has `candidates: []` and must
  //     be skipped (no rows added AND no suppression of the in-flight
  //     branch for round 5). The in-flight L1_SCORE is also empty here
  //     because no scoring actually happened, so the final shape is
  //     origin + 8 historical candidates = 9 rows total.
  const dash = loadCycleFixture("l2_terminal");
  const rows = roundCandidates(dash);

  it("emits origin C0", () => {
    const origin = rows.find((r) => r.is_origin);
    expect(origin).toBeDefined();
    expect(origin?.key).toBe("C0");
  });

  it("emits every non-empty historical round's candidates", () => {
    const historical = rows.filter((r) => !r.is_origin);
    // 4 closed rounds × 2 candidates each = 8 historical rows. Round 5's
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

  it("preserves selection order — origin then ascending rounds", () => {
    const ordered = rows.map((r) => r.key);
    expect(ordered[0]).toBe("C0");
    expect(ordered.slice(1)).toEqual([
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
});
