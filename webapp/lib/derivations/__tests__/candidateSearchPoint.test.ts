import { describe, expect, it } from "vitest";
import { candidateSearchPoint } from "../candidateSearchPoint";
import type { RoundFileDoc } from "@/lib/poll";

// A round file with two candidates, each carrying its own evolved prompt
// + node-config delta — the shape `build_score_report` now persists.
const doc: RoundFileDoc = {
  round: 2,
  candidate_scores: [
    {
      candidate_id: "cand-a",
      prompt_fields: { instruction: "evolved A", persona: "solver" },
      pipeline_params_override: { llm_only: { reasoning_effort: "high" } },
    },
    {
      candidate_id: "cand-b",
      prompt_fields: { instruction: "evolved B" },
      pipeline_params_override: null,
    },
  ],
};

describe("candidateSearchPoint", () => {
  it("projects the selected candidate's prompt + overlay", () => {
    const sp = candidateSearchPoint(doc, "cand-a");
    expect(sp).toEqual({
      origin_prompt_fields: { instruction: "evolved A", persona: "solver" },
      pipeline_overlay: { llm_only: { reasoning_effort: "high" } },
    });
  });

  it("defaults a null override to an empty overlay", () => {
    const sp = candidateSearchPoint(doc, "cand-b");
    expect(sp?.origin_prompt_fields).toEqual({ instruction: "evolved B" });
    expect(sp?.pipeline_overlay).toEqual({});
  });

  it("returns null for an unknown candidate or unloaded doc", () => {
    expect(candidateSearchPoint(doc, "missing")).toBeNull();
    expect(candidateSearchPoint(null, "cand-a")).toBeNull();
    expect(candidateSearchPoint({ round: 1 }, "cand-a")).toBeNull();
  });
});
