import { describe, expect, it } from "vitest";
import { liveInFlightSearchPoint, resolveSearchPoint } from "../searchPoint";
import type { DashboardSnapshot } from "@/lib/poll";
import type { ConnectorView } from "@/lib/types";
import type { DraftCampaignWire } from "@/lib/api";

const cv = (over: Partial<ConnectorView> = {}): ConnectorView =>
  ({
    isLive: false,
    originPromptFields: null,
    nodeConfigSchema: null,
    nodeOutputSchema: null,
    ...over,
  }) as ConnectorView;

type InputCandidate = {
  idx?: number;
  label?: string;
  prompt_fields?: Record<string, unknown>;
  pp_override?: Record<string, unknown> | null;
};

const liveDash = (candidates: InputCandidate[]): DashboardSnapshot =>
  ({ current_round: { round: 1, nodes: { l1_score: { input: { candidates } } } } }) as DashboardSnapshot;

describe("liveInFlightSearchPoint", () => {
  it("returns null with no live candidates", () => {
    expect(liveInFlightSearchPoint(null)).toBeNull();
    expect(liveInFlightSearchPoint(liveDash([]))).toBeNull();
  });

  it("picks the latest-seeded (max idx) candidate", () => {
    const r = liveInFlightSearchPoint(
      liveDash([
        { idx: 0, label: "C1.1", prompt_fields: { instruction: "a" }, pp_override: { n: 1 } },
        { idx: 2, label: "C1.3", prompt_fields: { instruction: "c" }, pp_override: { n: 3 } },
        { idx: 1, label: "C1.2", prompt_fields: { instruction: "b" } },
      ]),
    );
    expect(r?.label).toBe("C1.3");
    expect(r?.point.origin_prompt_fields).toEqual({ instruction: "c" });
    expect(r?.point.pipeline_overlay).toEqual({ n: 3 });
  });
});

describe("resolveSearchPoint", () => {
  const draft = {
    origin_prompt_fields: { instruction: "draft" },
    pipeline_overlay: { d: 1 },
  } as unknown as DraftCampaignWire;

  it("prefers the draft (origin setup) over live + origin", () => {
    const r = resolveSearchPoint({
      draft,
      dash: liveDash([{ idx: 0, prompt_fields: { instruction: "live" } }]),
      cv: cv({ isLive: true, originPromptFields: { instruction: "origin" } }),
    });
    expect(r.mode).toBe("draft");
    expect(r.point.origin_prompt_fields).toEqual({ instruction: "draft" });
    expect(r.point.pipeline_overlay).toEqual({ d: 1 });
  });

  it("shows the live in-flight candidate when running and no draft", () => {
    const r = resolveSearchPoint({
      draft: null,
      dash: liveDash([{ idx: 1, label: "C1.2", prompt_fields: { instruction: "live" } }]),
      cv: cv({ isLive: true, originPromptFields: { instruction: "origin" } }),
    });
    expect(r.mode).toBe("live");
    expect(r.label).toBe("live — C1.2");
    expect(r.point.origin_prompt_fields).toEqual({ instruction: "live" });
  });

  it("falls back to the dataset origin when not live", () => {
    const r = resolveSearchPoint({
      draft: null,
      dash: null,
      cv: cv({ originPromptFields: { instruction: "origin" } }),
    });
    expect(r.mode).toBe("origin");
    expect(r.label).toBe("read-only");
    expect(r.point.origin_prompt_fields).toEqual({ instruction: "origin" });
  });

  it("falls back to origin when live but between rounds (no in-flight candidate)", () => {
    const r = resolveSearchPoint({
      draft: null,
      dash: liveDash([]),
      cv: cv({ isLive: true, originPromptFields: { instruction: "origin" } }),
    });
    expect(r.mode).toBe("origin");
    expect(r.label).toBe("read-only — running");
  });
});
