import { describe, expect, it } from "vitest";
import { candidateVerdicts, samplesForRow } from "../round-samples";
import type { CandidateRow, NodeBlock } from "@/lib/types";
import type { DashboardSnapshot } from "@/lib/poll";
import type { RoundResult } from "@/lib/types";
import { sampleRow } from "@/lib/test-fixtures";

// `samplesForRow` is the single live-vs-historical source switch every sample
// surface rides (candidates-card bars, MeasurementRun groups). It SELECTS one
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
  thetaCaveat: null,
    meanFitnessCiLo: null,
    meanFitnessCiHi: null,
    matchedParentLift: null,
    matchedParentLiftCiLo: null,
    matchedParentLiftCiHi: null,
    evaluators: {},
    is_winner: false,
    n_samples: null,
    n_expected: null,
    cached_samples: null,
    source,
  };
}

// The served row (`render.py::sample_row`) — already graded, so the fixture states a verdict
// rather than a rendering for the reader to recover one from.
const liveDash = {
  current_round: {
    round: 1,
    nodes: {
      l1_score: {
        output: {
          candidates: [
            {
              idx: 0,
              // The join key both halves carry — served on every live row (`candidate_label`).
              label: "C1.1",
              samples: [
                sampleRow({ qi: 0, sample_id: 2, status: "MISS", terminal_node: "token_matching" }),
              ],
            },
          ],
        },
      },
    },
  },
} as unknown as DashboardSnapshot;

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

// The other half of the same block. It is a plain `dict[str, Any]` server-side with no model
// behind it, so every read is defensive by contract, not by caution. These cases are the shapes
// the producer actually emits plus the ones a half-written block emits mid-round.
const block = (input: unknown, output: unknown): NodeBlock =>
  ({ input, output }) as NodeBlock;

describe("candidateVerdicts", () => {
  it("is empty for a block that has not arrived", () => {
    expect(candidateVerdicts(null).size).toBe(0);
    expect(candidateVerdicts(undefined).size).toBe(0);
    expect(candidateVerdicts({} as NodeBlock).size).toBe(0);
  });

  it("joins the two halves on label — what was tried, and what validation said", () => {
    const v = candidateVerdicts(
      block(
        { candidates: [{ label: "C4.3", changes_description: "Added an advertising rule." }] },
        {
          candidates: [
            {
              label: "C4.3",
              validation_failures: [
                {
                  axis: "variant",
                  value: "re-proposes the idea measured and lost in round 3",
                  allowed: ["an idea this cycle has not already lost with"],
                  reason: "repeat_variant",
                  owner: "l1",
                },
              ],
            },
          ],
        },
      ),
    );
    expect(v.get("C4.3")?.changes).toBe("Added an advertising rule.");
    expect(v.get("C4.3")?.failures).toHaveLength(1);
    expect(v.get("C4.3")?.failures[0]?.reason).toBe("repeat_variant");
  });

  it("keeps the input half when the output half has not been written yet", () => {
    // Mid-round: the input is seeded when scoring STARTS, the output filled as it finishes.
    const v = candidateVerdicts(
      block({ candidates: [{ label: "C4.1", changes_description: "Rewrote thinking_style." }] }, {}),
    );
    expect(v.get("C4.1")).toEqual({ changes: "Rewrote thinking_style.", failures: [] });
  });

  it("admits an output-only candidate rather than dropping it", () => {
    const v = candidateVerdicts(block({}, { candidates: [{ label: "C4.2" }] }));
    expect(v.get("C4.2")).toEqual({ changes: "", failures: [] });
  });

  it("reads an empty failure list and an absent one the same way — a candidate that ran", () => {
    const v = candidateVerdicts(
      block({}, { candidates: [{ label: "A", validation_failures: [] }, { label: "B" }] }),
    );
    expect(v.get("A")?.failures).toEqual([]);
    expect(v.get("B")?.failures).toEqual([]);
  });

  it("survives a malformed block without inventing a verdict", () => {
    // A rejection is a claim about a paid measurement; a row too broken to read must produce
    // NO entry, never an empty one that reads as "nothing was wrong".
    const v = candidateVerdicts(
      block({ candidates: "not an array" }, {
        candidates: [
          null,
          42,
          { label: "" },
          { label: "C1", validation_failures: [{ reason: "repeat_variant" }, "junk"] },
        ],
      }),
    );
    expect(v.has("")).toBe(false);
    expect(v.size).toBe(1);
    // The one failure lacked `value`, the field the panel renders — dropped, not half-shown.
    expect(v.get("C1")?.failures).toEqual([]);
  });
});
