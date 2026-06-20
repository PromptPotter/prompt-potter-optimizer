import { describe, expect, it } from "vitest";
import { candidateSearchPoint } from "../candidateSearchPoint";
import type { RoundFileDoc } from "@/lib/poll";

// A round file with two candidates, each carrying its own evolved prompt
// + COMPLETE resolved config — the shape `build_score_report` now persists.
const doc: RoundFileDoc = {
  round: 2,
  candidate_scores: [
    {
      candidate_id: "cand-a",
      prompt_fields: { instruction: "evolved A", persona: "solver" },
      resolved_pipeline_params: {
        llm_only: { model: "openai/gpt-oss-120b", reasoning_effort: "high" },
        steps: ["llm_only"],
      },
    },
    {
      candidate_id: "cand-b",
      prompt_fields: { instruction: "evolved B" },
      resolved_pipeline_params: null,
    },
  ],
};

describe("candidateSearchPoint", () => {
  it("seeds the fork from the candidate's complete resolved config (steps dropped)", () => {
    const sp = candidateSearchPoint(doc, "cand-a");
    expect(sp).toEqual({
      origin_prompt_fields: { instruction: "evolved A", persona: "solver" },
      // model carried (not just the evolved delta); the `steps` list is stripped
      // so the per-node fork-bootstrap merge can't choke on it.
      pipeline_overlay: { llm_only: { model: "openai/gpt-oss-120b", reasoning_effort: "high" } },
    });
  });

  it("defaults a null resolved config to an empty overlay", () => {
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
