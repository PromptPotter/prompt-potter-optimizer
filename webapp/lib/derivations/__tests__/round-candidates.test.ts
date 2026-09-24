import { describe, expect, it } from "vitest";
import { loadCycleFixture } from "@/lib/test-utils/fixtures";
import { currentRound, dash as dashboard, liveRow } from "@/lib/test-fixtures";
import {
  closedRoundNumbers,
  groupByRound,
  roundCandidates,
} from "../round-candidates";
import { availableRounds } from "../round-axis";
import { liveCandidateId } from "@/lib/candidate-label";

describe("roundCandidates — l2_terminal fixture", () => {
  // Fixture: origin + 4 scored rounds of two + an empty round-5 stub (closed mid-L2).
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
    const round5 = rows.filter((r) => r.round === 5);
    expect(round5).toHaveLength(0);
  });

  it("availableRounds excludes the empty L2-terminal round 5 from completed", () => {
    const axis = availableRounds(dash, false);
    expect(axis.completed).toEqual([0, 1, 2, 3, 4]);
    expect(axis.live).toBeNull();
  });

  it("closedRoundNumbers is the shared 'closed with fitness data' set — excludes the empty round 5", () => {
    // Excludes the empty round, unlike `useRoundSource`'s on-disk presence check.
    expect(closedRoundNumbers(dash)).toEqual(new Set([0, 1, 2, 3, 4]));
  });

  it("groupByRound buckets the same spine rows without recomputing the merge", () => {
    const byRound = groupByRound(rows);
    expect([...byRound.keys()].sort((a, b) => a - b)).toEqual([0, 1, 2, 3, 4]);
    expect(byRound.get(0)?.map((r) => r.key)).toEqual(["R0.0"]);
    expect(byRound.get(1)?.map((r) => r.key)).toEqual(["R1.0", "R1.1"]);
    expect(byRound.get(5)).toBeUndefined();
    const grouped = [...byRound.values()].reduce((n, b) => n + b.length, 0);
    expect(grouped).toBe(rows.length);
  });
});

// A live row is the same served shape as a closed one, so every field carries through.
describe("roundCandidates — the in-flight round", () => {
  const live = dashboard({
    current_round: currentRound({
      round: 2,
      candidates: [
        liveRow({
          label: "C2.1",
          // A FINISHED live row: the score report's lineage id has landed on it.
          candidate_id: "9f2c1b7e-4a80-4d55-9c31-0b6ad2f11e03",
          accuracy: 0.6,
          composite_fitness: 0.55,
          mean_fitness_ci_lo: 0.41,
          mean_fitness_ci_hi: 0.69,
          scored_samples: 8,
          expected_samples: 20,
        }),
      ],
    }),
  });
  const row = roundCandidates(live).find((r) => r.source === "inflight");

  it("carries the whisker off the same row as the bar", () => {
    expect(row?.accuracy).toBe(0.6);
    expect(row?.meanFitnessCiLo).toBe(0.41);
    expect(row?.meanFitnessCiHi).toBe(0.69);
  });

  // An in-flight `candidate_id` is POSITIONAL, a row key only; a live reader joins on the LABEL.
  it("keys an in-flight row on the positional id and carries its label", () => {
    expect(row?.candidate_id).toBe(liveCandidateId(2, 0));
    expect(row?.label).toBeTruthy();
  });

  it("holds no crown — the election is a round-scoped fit that has not run", () => {
    expect(row?.is_winner).toBe(false);
    expect(row?.theta).toBeNull();
  });

  it("reports the partial panel it has measured so far", () => {
    expect(row?.n_samples).toBe(8);
    expect(row?.n_expected).toBe(20);
  });
});

// A rejected candidate never ran; `INVALID_SCORES` gives it a synthetic 0.0, so the flag saying
// which it is must carry through.
describe("roundCandidates — a rejected candidate", () => {
  const live = dashboard({
    current_round: currentRound({
      round: 4,
      candidates: [
        liveRow({ label: "C4.1", accuracy: 0.65, composite_fitness: 0.65, scored_samples: 20 }),
        liveRow({
          label: "C4.3",
          invalid: true,
          // What the producer actually serves for one: the synthetic score, over no rows at all.
          accuracy: 0,
          composite_fitness: 0,
          scored_samples: 0,
        }),
      ],
    }),
  });
  const rows = roundCandidates(live).filter((r) => r.source === "inflight");

  it("carries the flag through, so a renderer can tell the two apart", () => {
    expect(rows.find((r) => r.label === "C4.1")?.invalid).toBe(false);
    expect(rows.find((r) => r.label === "C4.3")?.invalid).toBe(true);
  });

  it("leaves the served synthetic score untouched — it feeds selection, and only the RENDER changes", () => {
    const rejected = rows.find((r) => r.label === "C4.3");
    expect(rejected?.accuracy).toBe(0);
    expect(rejected?.n_samples).toBe(0);
  });
});
