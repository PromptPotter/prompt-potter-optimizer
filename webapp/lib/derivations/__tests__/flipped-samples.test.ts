import { describe, expect, it } from "vitest";
import { sampleFlips } from "../flipped-samples";
import type { SampleRow, SampleStatus } from "@/lib/types";

function row(sample_id: number | null, status: SampleStatus | null, predicted = ""): SampleRow {
  return {
    key: `k${sample_id}`,
    round: 0,
    candidate_id: "c",
    sample_id,
    status,
    cached: false,
    query: "",
    predicted,
    ground_truth: "",
    scorer: "",
    elapsed_s: null,
  };
}

describe("sampleFlips", () => {
  it("splits gains from regressions and counts only what BOTH sides measured", () => {
    const origin = [row(1, "MISS"), row(2, "HIT"), row(3, "HIT"), row(4, "MISS")];
    const champion = [row(1, "HIT", "fixed"), row(2, "MISS"), row(3, "HIT"), row(9, "HIT")];
    const out = sampleFlips(origin, champion);
    expect(out.gained.map((f) => f.sample_id)).toEqual([1]);
    expect(out.lost.map((f) => f.sample_id)).toEqual([2]);
    // 4 is origin-only and 9 champion-only — neither is comparable.
    expect(out.compared).toBe(3);
    expect(out.gained[0]?.after.predicted).toBe("fixed");
  });

  it("closes the partition — gained + lost + unchanged is the compared count", () => {
    // Sample 3 answers HIT both times: the remainder, and the group that is largest in
    // every real run. A panel that omits it prints 1 and 1 over a denominator of 3.
    const origin = [row(1, "MISS"), row(2, "HIT"), row(3, "HIT")];
    const champion = [row(1, "HIT"), row(2, "MISS"), row(3, "HIT")];
    const out = sampleFlips(origin, champion);
    expect(out.unchanged).toBe(1);
    expect(out.gained.length + out.lost.length + out.unchanged).toBe(out.compared);
  });

  it("carries BOTH graded rows so the renderer needs no second lookup", () => {
    const out = sampleFlips([row(7, "MISS", "wrong")], [row(7, "HIT", "right")]);
    expect(out.gained[0]?.before.predicted).toBe("wrong");
    expect(out.gained[0]?.after.predicted).toBe("right");
  });

  it("joins on sample_id ONLY — an unidentified or ungraded row is dropped, never positionally paired", () => {
    const origin = [row(null, "MISS"), row(5, "MISS")];
    const champion = [row(null, "HIT"), row(5, null)];
    const out = sampleFlips(origin, champion);
    expect(out).toEqual({ gained: [], lost: [], compared: 0, unchanged: 0 });
  });

  it("reads a re-measured sample at its LATEST answer", () => {
    const out = sampleFlips([row(1, "HIT"), row(1, "MISS")], [row(1, "HIT")]);
    expect(out.gained.map((f) => f.sample_id)).toEqual([1]);
    expect(out.lost).toEqual([]);
  });

  it("is empty when either side measured nothing", () => {
    const empty = { gained: [], lost: [], compared: 0, unchanged: 0 };
    expect(sampleFlips([], [row(1, "HIT")])).toEqual(empty);
    expect(sampleFlips([row(1, "HIT")], [])).toEqual(empty);
  });
});
