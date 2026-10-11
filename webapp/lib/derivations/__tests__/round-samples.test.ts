import { describe, expect, it } from "vitest";
import { samplesForRow } from "../round-samples";
import type { ArmRow, RoundResult } from "@/lib/types";
import {
  armReading,
  currentRound,
  dash,
  liveRow,
  sampleRow,
} from "@/lib/test-fixtures";

const reading = armReading({ arm: { round: 1, label: "C1.1", candidate_id: "r1_0" } });

function row(source: ArmRow["source"]): ArmRow {
  return { key: "R1.0", source, reading };
}

const liveDash = dash({
  current_round: currentRound({
    round: 1,
    candidates: [
      liveRow({
        reading,
        samples: [sampleRow({ qi: 0, sample_id: 2, status: "MISS", terminal_node: "token_matching" })],
      }),
    ],
  }),
});

// The second row answered and the formula could not read it: it must not be graded from its fitness.
const historicalDoc = {
  all_candidate_results: {
    r1_0: [
      { sample_id: 1, fitness: 1, status: "HIT" },
      { sample_id: 4, unscored: true, status: "UNSC" },
    ],
  },
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
    expect(out.map((s) => s.sample_id)).toEqual([1, 4]);
    expect(out.map((s) => s.status)).toEqual(["HIT", "UNSC"]);
  });

  it("returns empty (never throws) when the row's source has no data", () => {
    expect(samplesForRow(row("inflight"), null, null)).toEqual([]);
    expect(samplesForRow(row("history"), null, null)).toEqual([]);
  });
});
