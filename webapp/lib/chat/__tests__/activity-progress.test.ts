import { describe, expect, it } from "vitest";
import { projectionToActivity } from "../activity";
import type { ProjectionEnvelope } from "@/lib/api/types";

function progressEnv(detail: unknown): ProjectionEnvelope {
  return {
    kind: "llm_call_progress",
    version: 1,
    cycle_id: "c1",
    sequence: 7,
    payload: { call_id: "inner:justlogic-d67/seed-0", node: "l1_critique", elapsed_s: 30, detail },
  };
}

describe("projectionToActivity — llm_call_progress", () => {
  it("maps a detail-carrying heartbeat to one stable-id progress chip", () => {
    const item = projectionToActivity(progressEnv("inner r2/3 · best 55%"));
    expect(item).toEqual({
      id: "inner-progress",
      kind: "progress",
      icon: "·",
      label: "inner r2/3 · best 55%",
      detail: "30s",
      tone: "muted",
    });
  });

  it("drops a heartbeat with no detail (ordinary optimizer tick)", () => {
    expect(projectionToActivity(progressEnv(undefined))).toBeNull();
    expect(projectionToActivity(progressEnv(""))).toBeNull();
  });
});
