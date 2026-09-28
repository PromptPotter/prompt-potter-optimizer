import { describe, expect, it } from "vitest";
import { samplesForRow } from "../round-samples";
import type { CandidateRow } from "@/lib/types";
import type { RoundResult } from "@/lib/types";
import { currentRound, dash, liveRow, sampleRow } from "@/lib/test-fixtures";

// `samplesForRow` SELECTS one source off the row's `source` tag — never merges, never falls back:
// an in-flight row reads `dash`, a historical row the round file.

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
  thetaCaveat: null,
    meanFitnessCiLo: null,
    meanFitnessCiHi: null,
    referenceLift: null,
    referenceLiftCiLo: null,
    referenceLiftCiHi: null,
    evaluators: {},
    is_selected: false,
    n_samples: null,
    n_expected: null,
    cached_samples: null,
    source,
  };
}

// The served row (`blocks.py::sample_row`) — already graded, so the fixture states a verdict
// rather than a rendering for the reader to recover one from.
const liveDash = dash({
  current_round: currentRound({
    round: 1,
    candidates: [
      liveRow({
        label: "C1.1",
        samples: [sampleRow({ qi: 0, sample_id: 2, status: "MISS", terminal_node: "token_matching" })],
      }),
    ],
  }),
});

const historicalDoc = {
  all_candidate_results: { r1_0: [{ sample_id: 1, fitness: 1 }] },
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
