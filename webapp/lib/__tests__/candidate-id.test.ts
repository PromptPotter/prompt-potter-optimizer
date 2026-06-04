import { describe, it, expect } from "vitest";
import { liveCandidateId } from "@/lib/candidate-label";

// `liveCandidateId` is the single home for the in-flight candidate id
// `r{round}_{idx}` (was hand-built as a magic string across 6 sites). The
// format is selection-routing glue: construction and match sites must agree
// byte-for-byte, so lock the shape and the round-trip here.
describe("liveCandidateId", () => {
  it("builds the canonical `r{round}_{idx}` id", () => {
    expect(liveCandidateId(5, 1)).toBe("r5_1");
    expect(liveCandidateId(0, 0)).toBe("r0_0");
  });

  it("matches a constructed id when round + idx round-trip", () => {
    const id = liveCandidateId(3, 2);
    const slots = [{ idx: 0 }, { idx: 1 }, { idx: 2 }];
    expect(slots.find((s) => liveCandidateId(3, s.idx) === id)).toEqual({ idx: 2 });
  });
});
