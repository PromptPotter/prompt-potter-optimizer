import { describe, expect, it } from "vitest";
import {
  armReading,
  currentRound,
  dash as dashboard,
  liveRow,
  ownLevel,
  servedLabel,
  summaryCandidate,
  summaryRound,
} from "@/lib/test-fixtures";
import { groupByRound, roundCandidates } from "../round-candidates";
import { roundReading } from "../round-reading";

function l2Terminal() {
  const scoredRound = (round: number, crowned: string | null) =>
    summaryRound({
      round,
      improved: round === 0 ? null : crowned !== null,
      candidates: Array.from({ length: round === 0 ? 1 : 3 }, (_, idx) => {
        const label = servedLabel(round, idx);
        return summaryCandidate({
          reading: armReading({
            arm: { round, label, candidate_id: `sp_${round}_${idx}` },
            election: { held: true, selected: label === crowned },
            ability: { theta: 0.1 * (idx - 1), se: 0.3, ci_lo: null, ci_hi: null, caveat: null },
            panel: { scored: 20, expected: 20 },
          }),
        });
      }),
      optimizer_facts: [
        { key: "critique", label: "Critique", text: `critique ${round}`, value: null, kind: "note" },
      ],
    });
  return dashboard({
    state: "stopped",
    run_phase: "terminal",
    round: 4,
    current_round: currentRound({ round: 4 }),
    rounds: [
      scoredRound(0, "C0"),
      scoredRound(1, "C1.2"),
      scoredRound(2, null),
      scoredRound(3, null),
      summaryRound({ round: 4 }),
    ],
    // As served: the round that closed before measuring is not on the axis.
    round_axis: { completed: [0, 1, 2, 3], live: null, position: 3 },
  });
}

describe("roundCandidates — a run that stopped mid-L2", () => {
  const dash = l2Terminal();
  const rows = roundCandidates(dash);
  const roundOf = (r: (typeof rows)[number]) => r.reading.arm.round;

  it("emits origin as round 0 (C0)", () => {
    const origin = rows.find((r) => roundOf(r) === 0);
    expect(origin).toBeDefined();
    expect(origin?.reading.arm.label).toBe("C0");
    expect(origin?.key).toBe("R0.0");
  });

  it("emits every non-empty post-origin round's candidates", () => {
    const historical = rows.filter((r) => roundOf(r) > 0);
    expect(historical).toHaveLength(9);
    expect(new Set(historical.map(roundOf))).toEqual(new Set([1, 2, 3]));
  });

  it("does not emit any inflight row for the L2-terminal round 4", () => {
    const inflight = rows.filter((r) => r.source === "inflight");
    expect(inflight).toHaveLength(0);
  });

  it("preserves selection order — origin (round 0) then ascending rounds", () => {
    const ordered = rows.map((r) => r.key);
    expect(ordered).toEqual([
      "R0.0",
      "R1.0",
      "R1.1",
      "R1.2",
      "R2.0",
      "R2.1",
      "R2.2",
      "R3.0",
      "R3.1",
      "R3.2",
    ]);
  });

  it("empty round 4 does not suppress the in-flight branch for round 4", () => {
    const round4 = rows.filter((r) => roundOf(r) === 4);
    expect(round4).toHaveLength(0);
  });

  it("groupByRound buckets the same spine rows without recomputing the merge", () => {
    const byRound = groupByRound(rows);
    expect([...byRound.keys()].sort((a, b) => a - b)).toEqual([0, 1, 2, 3]);
    expect(byRound.get(0)?.map((r) => r.key)).toEqual(["R0.0"]);
    expect(byRound.get(1)?.map((r) => r.key)).toEqual(["R1.0", "R1.1", "R1.2"]);
    expect(byRound.get(4)).toBeUndefined();
    const grouped = [...byRound.values()].reduce((n, b) => n + b.length, 0);
    expect(grouped).toBe(rows.length);
  });

  it("crowns exactly the rows the rounds served as selected", () => {
    const crowned = rows.filter((r) => r.reading.election.selected);
    expect(crowned.map((r) => r.reading.arm.label)).toEqual(["C0", "C1.2"]);
  });

  it("reads the last closed round's optimizer facts, never the empty stub's", () => {
    expect(roundReading(dash, 4)).toBeNull();
    const last = roundReading(dash, 3);
    expect(last?.round).toBe(3);
    expect(last?.facts).toEqual(dash.rounds[3]?.optimizer_facts);
    expect(last?.facts.length).toBeGreaterThan(0);
  });
});

describe("roundCandidates — the in-flight round", () => {
  const served = liveRow({
    reading: armReading({
      // A FINISHED live row: the score report's lineage id has landed on it.
      arm: { round: 2, label: "C2.1", candidate_id: "9f2c1b7e-4a80-4d55-9c31-0b6ad2f11e03" },
      own: { ...ownLevel(0.6, 8), accuracy: { value: 0.6, ci_lo: 0.41, ci_hi: 0.69 } },
      panel: { scored: 8, expected: 20 },
    }),
  });
  const live = dashboard({ current_round: currentRound({ round: 2, candidates: [served] }) });
  const row = roundCandidates(live).find((r) => r.source === "inflight");

  it("hands the served reading over whole, keyed by its position", () => {
    expect(row?.reading).toBe(served.reading);
    expect(row?.key).toBe("R2.0");
  });

  it("is absent once the round has closed with measurements", () => {
    const closed = dashboard({
      current_round: currentRound({ round: 2, candidates: [served] }),
      rounds: [summaryRound({ round: 2, candidates: [summaryCandidate()] })],
      round_axis: { completed: [2], live: null, position: 2 },
    });
    expect(roundCandidates(closed).map((r) => r.source)).toEqual(["history"]);
  });
});
