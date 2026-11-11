import { describe, expect, it } from "vitest";
import { currentRound, dash, liveRow } from "@/lib/test-fixtures";
import { liveCandidates } from "../live-candidates";

describe("liveCandidates", () => {
  it("returns the same reference on the no-candidate path", () => {
    expect(liveCandidates(null)).toBe(liveCandidates(null));
  });

  it("returns the candidates array when present", () => {
    const d = dash({ current_round: currentRound({ candidates: [liveRow()] }) });
    expect(liveCandidates(d)).toHaveLength(1);
  });
});
