import { describe, expect, it } from "vitest";
import { armReading, currentRound, dash, liveRow } from "@/lib/test-fixtures";
import { liveCandidate, liveCandidates } from "../poll";

describe("liveCandidates", () => {
  it("returns the same reference on the no-candidate path", () => {
    expect(liveCandidates(null)).toBe(liveCandidates(null));
  });

  it("returns the candidates array when present", () => {
    const d = dash({ current_round: currentRound({ candidates: [liveRow()] }) });
    expect(liveCandidates(d)).toHaveLength(1);
  });
});

describe("liveCandidate key join", () => {
  const d = dash({
    current_round: currentRound({
      candidates: [
        liveRow({ reading: armReading({ arm: { round: 2, label: "C2.1", key: "a" } }) }),
        liveRow({
          reading: armReading({ arm: { round: 2, label: "C2.2", key: "b" }, panel: { scored: 3 } }),
        }),
      ],
    }),
  });

  it("resolves the in-flight candidate by its arm key", () => {
    expect(liveCandidate(d, "b")?.reading.panel.scored).toBe(3);
  });

  it("matches no row on an absent key or a label", () => {
    expect(liveCandidate(d, null)).toBeNull();
    expect(liveCandidate(d, "C2.2")).toBeNull();
  });
});
