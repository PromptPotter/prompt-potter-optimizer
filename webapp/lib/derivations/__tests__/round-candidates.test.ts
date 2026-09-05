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
import { liveCandidateRow } from "@/lib/poll";

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

// The in-flight branch, which the fixture above has none of. A live row is the same served
// shape as a closed one, so every field carries through — the arm used to hardcode `theta`,
// `meanFitnessCi*` and `matchedParent*` to null as "stamped at round close", which the CI is not.
describe("roundCandidates — the in-flight round", () => {
  const live = dashboard({
    current_round: currentRound({
      round: 2,
      candidates: [
        liveRow({
          label: "C2.1",
          // A FINISHED live row: scoring has landed, so the score report's lineage id is on it
          // — the state in which the two live readers' positional join used to break.
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

  // The LABEL is what both live readers join back on — `liveCandidate` (the sample tape) and
  // `liveCandidateRow` (the inspector). It is the only key a selection minted off the tree and
  // a row that has not been scored yet both carry; keyed on the lineage id instead, every
  // in-flight bar click resolves in neither and the panel reads as "still scoring".
  it("resolves both live readers on the row's label", () => {
    expect(row?.candidate_id).toBe(liveCandidateId(2, 0));
    expect(liveCandidateRow(live, row!.label)).not.toBeNull();
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

// A candidate REJECTED by validation never ran, and `INVALID_SCORES` gives it a synthetic 0.0 so
// its row is not byte-identical to one that got everything wrong. The flag saying which it is was
// dropped here for as long as this mapper existed, which is how `C4.3 · 0%` reached the strip.
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
