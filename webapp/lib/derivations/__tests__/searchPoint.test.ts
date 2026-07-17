import { describe, expect, it } from "vitest";
import {
  candidateObserveConfig,
  liveCandidateObserveConfig,
  liveObserveConfig,
  originObserveConfig,
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

describe("originObserveConfig", () => {
  it("reads the round-0 C0 row (index 0) resolved config", () => {
    const doc = roundDoc({
      candidate_scores: [
        scored({ candidate_id: "c0", prompt_fields: { instruction: "o" }, resolved_pipeline_params: { llm: { model: "m0" } } }),
      ],
    });
    const r = originObserveConfig(doc);
    expect(r?.label).toBe("origin");
    expect(r?.config).toEqual({ llm: { model: "m0" } });
    expect(r?.promptFields).toEqual({ instruction: "o" });
  });

  it("returns null without a round-0 doc / candidate_scores", () => {
    expect(originObserveConfig(null)).toBeNull();
    expect(originObserveConfig(roundDoc({ round: 0 }))).toBeNull();
  });
});

describe("candidateObserveConfig", () => {
  const doc = roundDoc({
    candidate_scores: [
      scored({ candidate_id: "cand-a", prompt_fields: { instruction: "a" }, resolved_pipeline_params: { llm: { model: "ma" } } }),
      scored({ candidate_id: "cand-b", prompt_fields: { instruction: "b" }, resolved_pipeline_params: { llm: { model: "mb" } } }),
    ],
  });

  it("locates a candidate by id and projects its resolved config", () => {
    const r = candidateObserveConfig(doc, "cand-b", "winner · C2.1");
    expect(r?.label).toBe("winner · C2.1");
    expect(r?.config).toEqual({ llm: { model: "mb" } });
    expect(r?.promptFields).toEqual({ instruction: "b" });
  });

  it("returns null for a missing id / doc", () => {
    expect(candidateObserveConfig(doc, "missing", "x")).toBeNull();
    expect(candidateObserveConfig(null, "cand-a", "x")).toBeNull();
    expect(candidateObserveConfig(roundDoc({ round: 1 }), "cand-a", "x")).toBeNull();
  });
});
