import { describe, expect, it } from "vitest";
import { samplesForRow } from "../round-samples";
import type { CandidateRow } from "@/lib/types";
import type { DashboardSnapshot } from "@/lib/poll";
import type { RoundResult } from "@/lib/types";

// `samplesForRow` is the single live-vs-historical source switch every sample
// surface rides (FitnessPanel bars, RoundSamplesView groups). It SELECTS one
// source off the row's `source` tag — never merges, never falls back — so the
// two readers can't drift on routing. These tests pin that an in-flight row
// reads `dash` (ignoring the round file) and a historical row reads the round
// file (ignoring `dash`).

function row(source: CandidateRow["source"]): CandidateRow {
  return {
    key: "R1.0",
    round: 1,
    idx: 0,
    candidate_id: "r1_0",
    label: "C1.1",
    accuracy: null,
    composite: null,
    theta: null,
    theta_se: null,
    compositeCiLo: null,
    compositeCiHi: null,
    evaluators: {},
    is_winner: false,
    cumulative_accuracy: null,
    n_samples: null,
    n_expected: null,
    source,
  };
}

const liveDash = {
  current_round: {
    round: 1,
    nodes: {
      l1_score: { output: { candidates: [{ idx: 0, samples: [{ sample_id: 2, hit: false }] }] } },
    },
  },
} as unknown as DashboardSnapshot;

const historicalDoc = {
  all_candidate_results: { r1_0: [{ sample_id: 1, hit: true }] },
} as unknown as RoundResult;

describe("samplesForRow — source routing", () => {
  it("an in-flight row reads dash and ignores the round file", () => {
    const out = samplesForRow(row("inflight"), liveDash, historicalDoc);
    expect(out).toHaveLength(1);
    expect(out[0]!.sample_id).toBe(2);
    expect(out[0]!.status).toBe("MISS");
  });

  it("a historical row reads the round file and ignores dash", () => {
    const out = samplesForRow(row("history"), liveDash, historicalDoc);
    expect(out).toHaveLength(1);
    expect(out[0]!.sample_id).toBe(1);
    expect(out[0]!.status).toBe("HIT");
  });

  it("returns empty (never throws) when the row's source has no data", () => {
    expect(samplesForRow(row("inflight"), null, null)).toEqual([]);
    expect(samplesForRow(row("history"), null, null)).toEqual([]);
  });
});
