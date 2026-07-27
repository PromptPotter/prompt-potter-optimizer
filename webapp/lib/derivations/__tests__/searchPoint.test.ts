import { describe, expect, it } from "vitest";
import {
  candidateObserveConfig,
  liveCandidateObserveConfig,
  liveObserveConfig,
} from "../searchPoint";
import type { DashboardSnapshot } from "@/lib/poll";
import { dash, roundDoc, scored } from "@/lib/test-fixtures";

type InputCandidate = {
  idx?: number;
  label?: string;
  prompt_fields?: Record<string, unknown>;
  resolved_pipeline_params?: Record<string, unknown> | null;
};

const liveDash = (candidates: InputCandidate[]): DashboardSnapshot =>
  dash({ current_round: { round: 1, nodes: { l1_score: { input: { candidates } } } } });

describe("liveObserveConfig", () => {
  it("returns null with no live candidates", () => {
    expect(liveObserveConfig(null)).toBeNull();
    expect(liveObserveConfig(liveDash([]))).toBeNull();
  });

  it("picks the latest-seeded (max idx) candidate's resolved config", () => {
    const r = liveObserveConfig(
      liveDash([
        { idx: 0, label: "C1.1", prompt_fields: { instruction: "a" }, resolved_pipeline_params: { llm: { model: "x" } } },
        { idx: 2, label: "C1.3", prompt_fields: { instruction: "c" }, resolved_pipeline_params: { llm: { model: "z" } } },
        { idx: 1, label: "C1.2", prompt_fields: { instruction: "b" } },
      ]),
    );
    expect(r?.label).toBe("live — C1.3");
    expect(r?.promptFields).toEqual({ instruction: "c" });
    expect(r?.config).toEqual({ llm: { model: "z" } });
  });

  it("defaults config to {} when the candidate carries none yet", () => {
    const r = liveObserveConfig(liveDash([{ idx: 0, label: "C1.1", prompt_fields: { instruction: "a" } }]));
    expect(r?.config).toEqual({});
  });
});

describe("liveCandidateObserveConfig", () => {
  const snap = liveDash([
    { idx: 0, label: "C1.1", prompt_fields: { instruction: "a" }, resolved_pipeline_params: { llm: { model: "x" } } },
    { idx: 1, label: "C1.2", prompt_fields: { instruction: "b" }, resolved_pipeline_params: { llm: { model: "y" } } },
  ]);

  it("locates the in-flight candidate by id (not the latest-seeded one)", () => {
    const r = liveCandidateObserveConfig(snap, "r1_0", "C1.1");
    expect(r?.label).toBe("live — C1.1");
    expect(r?.promptFields).toEqual({ instruction: "a" });
    expect(r?.config).toEqual({ llm: { model: "x" } });
  });

  it("returns null for an unknown / not-yet-seeded id", () => {
    expect(liveCandidateObserveConfig(snap, "r1_9", "C1.10")).toBeNull();
    expect(liveCandidateObserveConfig(snap, "", "x")).toBeNull();
  });

  it("returns null without a live round", () => {
    expect(liveCandidateObserveConfig(null, "r1_0", "C1.1")).toBeNull();
    expect(liveCandidateObserveConfig(dash({}), "r1_0", "C1.1")).toBeNull();
  });
});

// The origin has no reader of its own. C0 is a candidate of round 0, so it resolves through
// the SAME join every other candidate does — the whole premise of deleting
// `originObserveConfig`. The join is POSITIONAL, and C0 is where that matters most: a resume
// re-scores the origin and mints a new lineage id, while `round_0000.json` keeps the id the
// first run wrote.
describe("the origin as an ordinary candidate", () => {
  const round0 = roundDoc({
    round: 0,
    candidate_scores: [
      scored({ candidate_id: "c0", label: "C0", prompt_fields: { instruction: "o" }, resolved_pipeline_params: { llm: { model: "m0" } } }),
    ],
  });

  it("resolves C0 out of round 0 by its positional label", () => {
    const r = candidateObserveConfig(round0, "C0", "selected · C0");
    expect(r?.label).toBe("selected · C0");
    expect(r?.config).toEqual({ llm: { model: "m0" } });
    expect(r?.promptFields).toEqual({ instruction: "o" });
  });

  it("still resolves when the id on disk is from an earlier run", () => {
    const remintedIdIsIrrelevant = candidateObserveConfig(round0, "C0", "selected · C0");
    expect(remintedIdIsIrrelevant?.config).toEqual({ llm: { model: "m0" } });
  });

  it("returns null before round 0 is written", () => {
    expect(candidateObserveConfig(null, "C0", "C0")).toBeNull();
    expect(candidateObserveConfig(roundDoc({ round: 0 }), "C0", "C0")).toBeNull();
  });
});

describe("candidateObserveConfig", () => {
  const doc = roundDoc({
    candidate_scores: [
      scored({ candidate_id: "cand-a", label: "C2.1", prompt_fields: { instruction: "a" }, resolved_pipeline_params: { llm: { model: "ma" } } }),
      scored({ candidate_id: "cand-b", label: "C2.2", prompt_fields: { instruction: "b" }, resolved_pipeline_params: { llm: { model: "mb" } } }),
    ],
  });

  it("locates a candidate by position and projects its resolved config", () => {
    const r = candidateObserveConfig(doc, "C2.2", "winner · C2.2");
    expect(r?.label).toBe("winner · C2.2");
    expect(r?.config).toEqual({ llm: { model: "mb" } });
    expect(r?.promptFields).toEqual({ instruction: "b" });
  });

  it("returns null for a missing label / doc", () => {
    expect(candidateObserveConfig(doc, "C9.9", "x")).toBeNull();
    expect(candidateObserveConfig(null, "C2.1", "x")).toBeNull();
    expect(candidateObserveConfig(roundDoc({ round: 1 }), "C2.1", "x")).toBeNull();
  });
});
