import { describe, expect, it } from "vitest";
import { candidateSearchPoint } from "../candidateSearchPoint";
import type { RoundView } from "@/lib/types";
import { measuredArm, roundDoc, roundOfArms } from "@/lib/test-fixtures";

const doc: RoundView = roundOfArms(2, [
  {
    key: "cand-a",
    label: "C2.1",
    arm: measuredArm({
      prompts: { llm_only: { fields: { instruction: "evolved A", persona: "solver" }, shots: ["d1"] } },
      resolved_params: {
        llm_only: { model: "openai/gpt-oss-120b", reasoning_effort: "high" },
        steps: ["llm_only"],
      },
    }),
  },
]);

describe("candidateSearchPoint", () => {
  it("seeds the fork from the candidate's complete resolved config (steps dropped)", () => {
    const sp = candidateSearchPoint(doc, "cand-a");
    expect(sp).toEqual({
      origin_prompts: {
        llm_only: { fields: { instruction: "evolved A", persona: "solver" }, shots: ["d1"] },
      },
      pipeline_overlay: { llm_only: { model: "openai/gpt-oss-120b", reasoning_effort: "high" } },
    });
  });

  it("returns null for an unknown candidate or unloaded doc", () => {
    expect(candidateSearchPoint(doc, "missing")).toBeNull();
    expect(candidateSearchPoint(null, "cand-a")).toBeNull();
    expect(candidateSearchPoint(roundDoc({ round: 1 }), "cand-a")).toBeNull();
  });
});
