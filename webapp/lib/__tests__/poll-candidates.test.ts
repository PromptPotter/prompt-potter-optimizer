import { describe, expect, it } from "vitest";
import { currentRound, dash, liveRow } from "@/lib/test-fixtures";
import { liveCandidate, liveCandidates } from "../poll";

// The no-candidate path returns a STABLE reference, or the candidates card loops setState.
describe("liveCandidates", () => {
  it("returns the same reference on the no-candidate path", () => {
    expect(liveCandidates(null)).toBe(liveCandidates(null));
  });

  it("returns the candidates array when present", () => {
    const d = dash({ current_round: currentRound({ candidates: [liveRow()] }) });
    expect(liveCandidates(d)).toHaveLength(1);
  });
});

// The live half joins on LABEL; an empty label must not answer for one.
describe("liveCandidate label join", () => {
  const d = dash({
    current_round: currentRound({
      candidates: [liveRow({ label: "C2.1" }), liveRow({ label: "C2.2", run_id: "run-2" })],
    }),
  });

  it("resolves the in-flight candidate by its label", () => {
    expect(liveCandidate(d, "C2.2")?.run_id).toBe("run-2");
  });

  it("matches no row on an absent label", () => {
    expect(liveCandidate(d, "")).toBeNull();
    expect(liveCandidate(d, "C2.3")).toBeNull();
  });
});
