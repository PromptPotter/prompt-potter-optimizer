import { describe, expect, it } from "vitest";
import { currentRound, dash } from "@/lib/test-fixtures";
import { liveInputCandidate, liveL1Candidates, type DashboardSnapshot } from "../poll";

// Regression guard for the post-login render loop: the no-candidate path must
// return a STABLE reference. A fresh `[]` per call churned the candidates card's
// Set chain into an unbounded setState loop.
describe("liveL1Candidates", () => {
  it("returns the same reference on the no-candidate path", () => {
    expect(liveL1Candidates(null)).toBe(liveL1Candidates(null));
    const noNodes = dash({ current_round: currentRound() });
    expect(liveL1Candidates(noNodes)).toBe(liveL1Candidates(null));
    const noL1 = dash({ current_round: currentRound({ nodes: {} }) });
    expect(liveL1Candidates(noL1)).toBe(liveL1Candidates(null));
  });

  it("returns the candidates array when present", () => {
    const d = dash({
      current_round: currentRound({
        nodes: { l1_score: { output: { candidates: [{ idx: 0 }] } } },
      }),
    });
    expect(liveL1Candidates(d)).toHaveLength(1);
  });
});

// Regression: the live half joins on LABEL, the one key a tree-minted selection and a
// not-yet-scored live row both carry. A row missing its label must not answer for one.
describe("liveInputCandidate label join", () => {
  const dash = {
    current_round: {
      nodes: {
        l1_score: {
          input: { candidates: [{ idx: 0 }, { idx: 1, label: "C2.2", prompt_fields: {} }] },
        },
      },
    },
  } as unknown as DashboardSnapshot;

  it("resolves the in-flight candidate by its label", () => {
    expect(liveInputCandidate(dash, "C2.2")?.idx).toBe(1);
  });

  it("matches no row on an absent label", () => {
    expect(liveInputCandidate(dash, "")).toBeNull();
    expect(liveInputCandidate(dash, "C2.1")).toBeNull();
  });
});
