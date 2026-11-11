import { describe, expect, it } from "vitest";
import { armSearchPoint, servedArm } from "../candidate-search-point";
import type { RoundView } from "@/lib/api/types";
import { armReading, liveRow, roundOfArms } from "@/lib/test-fixtures";

const doc: RoundView = roundOfArms(2, [
  {
    key: "cand-a",
    label: "C2.1",
    prompts: { llm_only: { fields: { instruction: "evolved A", persona: "solver" }, shots: ["d1"] } },
    resolved_params: {
      llm_only: { model: "openai/gpt-oss-120b", reasoning_effort: "high" },
      steps: ["llm_only"],
    },
  },
]);

describe("armSearchPoint", () => {
  it("seeds the fork from the candidate's complete resolved config (steps dropped)", () => {
    expect(armSearchPoint(servedArm(doc.candidates, "cand-a"))).toEqual({
      origin_prompts: {
        llm_only: { fields: { instruction: "evolved A", persona: "solver" }, shots: ["d1"] },
      },
      pipeline_overlay: { llm_only: { model: "openai/gpt-oss-120b", reasoning_effort: "high" } },
    });
  });

  it("returns null for an unknown candidate", () => {
    expect(armSearchPoint(servedArm(doc.candidates, "missing"))).toBeNull();
    expect(armSearchPoint(servedArm([], "cand-a"))).toBeNull();
  });
});

describe("servedArm key join", () => {
  const arms = [
    liveRow({ reading: armReading({ arm: { round: 2, label: "C2.1", key: "a" } }) }),
    liveRow({ reading: armReading({ arm: { round: 2, label: "C2.2", key: "b" }, panel: { scored: 3 } }) }),
  ];

  it("resolves an arm by its key, in flight or filed alike", () => {
    expect(servedArm(arms, "b")?.reading.panel.scored).toBe(3);
  });

  it("matches no row on an absent key or a label", () => {
    expect(servedArm(arms, null)).toBeNull();
    expect(servedArm(arms, "C2.2")).toBeNull();
  });
});
