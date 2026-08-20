import { describe, expect, it } from "vitest";
import { projectionToActivity, type ProjectionEnvelope } from "../activity";

// The L4 fix: an `llm_call_progress` heartbeat carrying `detail` (the inner
// campaign's live "inner rX/Y · best Z%") becomes ONE ticking progress chip so
// the outer L4 chat never reads as silent; a detail-less optimizer heartbeat
// stays curated out.
function progressEnv(detail: unknown): ProjectionEnvelope {
  return {
    kind: "llm_call_progress",
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
      // The clock rides `elapsed_s` on the same record — the engine names WHO the wait belongs
      // to, this layer formats how long.
      detail: "30s",
      tone: "muted",
    });
  });

  it("drops a heartbeat with no detail (ordinary optimizer tick)", () => {
    expect(projectionToActivity(progressEnv(undefined))).toBeNull();
    expect(projectionToActivity(progressEnv(""))).toBeNull();
  });
});
