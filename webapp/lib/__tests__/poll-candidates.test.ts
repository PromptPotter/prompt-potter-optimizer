import { describe, expect, it } from "vitest";
import { dash } from "@/lib/test-fixtures";
import { liveInputCandidate, liveL1Candidates, type DashboardSnapshot } from "../poll";

// Regression guard for the post-login render loop: the no-candidate path must
// return a STABLE reference. A fresh `[]` per call churned the FitnessPanel
// Set chain into an unbounded setState loop.
describe("liveL1Candidates", () => {
  it("returns the same reference on the no-candidate path", () => {
    expect(liveL1Candidates(null)).toBe(liveL1Candidates(null));
    const noNodes = dash({ current_round: {} });
    expect(liveL1Candidates(noNodes)).toBe(liveL1Candidates(null));
    const noL1 = dash({ current_round: { nodes: {} } });
    expect(liveL1Candidates(noL1)).toBe(liveL1Candidates(null));
  });

  it("returns the candidates array when present", () => {
    const d = dash({
      current_round: { nodes: { l1_score: { output: { candidates: [{ idx: 0 }] } } } },
    });
    expect(liveL1Candidates(d)).toHaveLength(1);
  });
});

// Regression: `liveInputCandidate` applies the same idx guard as `liveCandidate`
// — a malformed idx must not stringify to `r{round}_undefined` and false-match.
describe("liveInputCandidate idx guard", () => {
  const dash = {
    current_round: {
      nodes: {
        l1_score: {
          input: { candidates: [{ idx: undefined }, { idx: 1, prompt_fields: {} }] },
        },
      },
    },
  } as unknown as DashboardSnapshot;

  it("does not match a malformed idx via the r{round}_undefined string", () => {
    expect(liveInputCandidate(dash, 2, "r2_undefined")).toBeNull();
  });

  it("still resolves a well-formed input candidate by id", () => {
    expect(liveInputCandidate(dash, 2, "r2_1")?.idx).toBe(1);
  });
});
