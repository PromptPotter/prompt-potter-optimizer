import { describe, it, expect } from "vitest";
import { liveCandidateId } from "@/lib/candidate-label";

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
