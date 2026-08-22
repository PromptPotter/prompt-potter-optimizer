import { describe, expect, it } from "vitest";
import { sampleBucket, sampleSpread, sampleWalk } from "../sample-walk";
import type { DashboardSnapshot } from "@/lib/poll";
import { currentRound, dash, liveRow, sampleRow } from "@/lib/test-fixtures";
import type { DashboardSample } from "@/lib/api/types";

// The live candidate as `dashboard.json` carries it — BOTH halves, because the projection
// writes both: the ROW under `current_round.candidates` (what a bar plots) and the served
// sample rows under the l1_score node block (what the walk reads).
const live = (
  samples: DashboardSample[],
  currentSampleId: number | null = null,
): DashboardSnapshot =>
  dash({
    current_sample_id: currentSampleId,
    current_round: currentRound({
      round: 1,
      candidates: [liveRow({ label: "C1.1" })],
      nodes: { l1_score: { output: { candidates: [{ idx: 0, label: "C1.1", samples }] } } },
    }),
  });

describe("sampleWalk", () => {
  // `qi` is the loop POSITION; the dataset id rides `sample_id` — a row without one carries
  // no sample id at all.
  const tape = [
    sampleRow({ qi: 1, sample_id: 99, status: "HIT" }),
    sampleRow({ qi: 2, sample_id: 455, status: "MISS" }),
  ];
  const order = [99, 455, 419, 271];

  it("walks the DECLARED order and puts the cursor on the in-flight sample", () => {
    const w = sampleWalk(live(tape, 419), order, true);
    expect(w.ids).toEqual(order);
    expect(w.cursor).toBe(2);
  });

  it("anchors on the last measured row when nothing is in flight — between samples is a real state", () => {
    const w = sampleWalk(live(tape, null), order, true);
    // 455 sits at index 1, so the run is one step past it.
    expect(w.cursor).toBe(2);
  });

  it("clamps the cursor at the end of the order", () => {
    expect(sampleWalk(live(tape, null), [99, 455], true).cursor).toBe(1);
  });

  it("falls back to the tape COUNT when the measured ids are not in the order", () => {
    const w = sampleWalk(live(tape, null), [1, 2, 3, 4], true);
    expect(w.cursor).toBe(2);
  });

  it("without an order the axis is the past only — the future is not invented", () => {
    const w = sampleWalk(live(tape, 419), null, true);
    expect(w.ids).toEqual([99, 455, 419]);
    expect(w.cursor).toBe(2);
  });

  it("has no axis when the tape carries no sample ids and nothing is in flight", () => {
    const idless = [sampleRow({ qi: 1 }), sampleRow({ qi: 2, status: "MISS" })];
    expect(sampleWalk(live(idless, null), null, true).ids).toEqual([]);
  });

  // `current_round` candidates linger after a stop, so liveness has to gate this or a
  // dead run keeps pointing at whatever it was doing when it died.
  it("is empty when the cycle is not live", () => {
    expect(sampleWalk(live(tape, 419), order, false)).toEqual({
      ids: [],
      cursor: -1,
      walkKey: "idle",
    });
  });

  it("keys the walk on the candidate, so a window scrolled away cannot survive one", () => {
    const a = sampleWalk(live(tape, 419), order, true).walkKey;
    const b = sampleWalk(
      dash({
        current_sample_id: 419,
        current_round: currentRound({
          round: 1,
          // Two rows: the walk follows the LATEST-seeded candidate, so C1.2 at position 1.
          candidates: [liveRow({ label: "C1.1" }), liveRow({ label: "C1.2" })],
          nodes: {
            l1_score: {
              output: {
                candidates: [
                  { idx: 0, label: "C1.1", samples: [] },
                  { idx: 1, label: "C1.2", samples: tape },
                ],
              },
            },
          },
        }),
      }),
      order,
      true,
    ).walkKey;
    expect(a).not.toBe(b);
  });
});

describe("sampleSpread", () => {
  it("buckets served rates and counts only measured rows", () => {
    expect(sampleSpread([0, 0, 0.33, 0.5, 1, 1, null, null])).toEqual({
      measured: 6,
      never: 2,
      partly: 2,
      always: 2,
    });
  });

  it("is all zeroes with nothing measured — an unmeasured roster is not a solved one", () => {
    expect(sampleSpread([null, null])).toEqual({
      measured: 0,
      never: 0,
      partly: 0,
      always: 0,
    });
  });

  it("buckets one rate the same way the counts do", () => {
    expect(sampleBucket(null)).toBeNull();
    expect(sampleBucket(0)).toBe("never");
    expect(sampleBucket(0.5)).toBe("partly");
    expect(sampleBucket(1)).toBe("always");
  });
});
