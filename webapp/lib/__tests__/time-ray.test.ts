import { describe, expect, it } from "vitest";
import { fmtGap } from "@/lib/format";
import { raySteps, rayHead, WEDGED_AFTER_S } from "@/lib/derivations";
import type { RayItem } from "@/lib/api/types";

// The ray's two load-bearing derivations. Both are pure, both decide something an operator
// reads as a fact about the run, and neither has any other witness — a wrong gap or a wrong
// head state renders perfectly and says the wrong thing.

const ROOT = "camp::cycle_root";
const INNER = "camp::cycle_root~inner::cycle_in";

const T0 = Date.parse("2026-01-01T00:00:00Z");
const at = (sec: number): string => new Date(T0 + sec * 1000).toISOString();

function item(over: Partial<RayItem> & { ts: string }): RayItem {
  return {
    path: [{ campaign_id: "camp", cycle_id: "cycle_root" }],
    offset: 0,
    kind: "phase",
    payload: { phase: "round", event: "display", round: 1, payload: {} },
    ...over,
  };
}

function round(sec: number, n: number, offset: number): RayItem {
  return item({
    ts: at(sec),
    offset,
    payload: { phase: "round", event: "display", round: n, payload: {} },
  });
}

function heartbeat(sec: number, offset: number): RayItem {
  return item({ ts: at(sec), offset, kind: "llm_call_progress", payload: { call_id: "c1" } });
}

function innerRound(sec: number, n: number, offset: number): RayItem {
  return item({
    ts: at(sec),
    offset,
    path: [
      { campaign_id: "camp", cycle_id: "cycle_root" },
      { campaign_id: "inner", cycle_id: "cycle_in" },
    ],
    payload: { phase: "round", event: "display", round: n, payload: {} },
  });
}

describe("raySteps", () => {
  it("drops bare heartbeats from the steps but keeps them as gap suppressors", () => {
    // THE COUPLING this test exists to hold. `measure_sample`'s QUERY_TIMEOUT is 120 s and
    // the heartbeat fires every 15 s, so a legitimate long query is 120 s of wall-clock with
    // heartbeats through it. If the heartbeats stopped feeding the silence clock, every
    // backend query would sprout a spurious "── 2m ──".
    const steps = raySteps(
      [round(0, 1, 0), heartbeat(60, 1), heartbeat(110, 2), round(120, 2, 3)],
      ROOT,
    );
    expect(steps).toHaveLength(2);
    expect(steps[1]?.gapBeforeS).toBe(10);
    expect(fmtGap(steps[1]!.gapBeforeS)).toBe("");
  });

  it("marks a real silence when nothing at all was recorded", () => {
    const steps = raySteps([round(0, 1, 0), round(600, 2, 1)], ROOT);
    expect(steps[1]?.gapBeforeS).toBe(600);
    expect(fmtGap(steps[1]!.gapBeforeS)).toBe("10m");
  });

  it("clusters consecutive inner-cycle steps and leaves the root's own alone", () => {
    const steps = raySteps(
      [round(0, 1, 0), innerRound(1, 1, 0), innerRound(2, 2, 1), innerRound(3, 3, 2), round(4, 2, 1)],
      ROOT,
    );
    expect(steps.map((s) => s.pathKey)).toEqual([ROOT, INNER, ROOT]);
    expect(steps[1]?.cluster).toBe(3);
    // The cluster's gap is the gap before the RUN began — folding the interior silences in
    // would invent a pause that never happened.
    expect(steps[1]?.gapBeforeS).toBe(1);
    // Consecutive ROOT steps never cluster: they are the story, not a digression.
    expect(steps[0]?.cluster).toBe(1);
    expect(steps[2]?.cluster).toBe(1);
  });

  it("carries the round and the candidate label a click needs", () => {
    const minted = item({
      ts: at(0),
      kind: "candidate_minted",
      payload: { round: 2, idx: 0, candidate_id: "x", label: "C2.1" },
    });
    const steps = raySteps([minted, round(1, 3, 1)], ROOT);
    expect(steps[0]?.candidateLabel).toBe("C2.1");
    expect(steps[0]?.round).toBe(2);
    expect(steps[1]?.candidateLabel).toBeNull();
    expect(steps[1]?.round).toBe(3);
  });
});

describe("rayHead", () => {
  const label = (items: RayItem[], phase: string, nowSec: number) =>
    rayHead(raySteps(items, ROOT), items, phase, "Finished", T0 + nowSec * 1000, ROOT);

  it("reads wedged when the server still says running and nothing has progressed", () => {
    // THE POINT OF THE WHOLE ARC. Every await that outlasts RUN_FRESH_S must heartbeat, so a
    // live cycle can never go stale — which means a WEDGED process reads `running` forever.
    // Freshness proves attachment, never progress.
    const items = [round(0, 1, 0), heartbeat(WEDGED_AFTER_S + 60, 1)];
    const head = label(items, "running", WEDGED_AFTER_S + 61);
    expect(head.state).toBe("wedged");
    expect(head.detail).toContain("no progress");
  });

  it("never reads wedged at the origin gate, however long it is held", () => {
    // The gate is the package's only UNBOUNDED await — it ends when a human decides — and it
    // heartbeats. So it emits proof-of-life and zero progress indefinitely and would trip any
    // threshold within minutes. It is not wedged: it is blocked on the operator, and it
    // already has a state that says exactly that.
    const items = [round(0, 1, 0), heartbeat(9999, 1)];
    expect(label(items, "gate", 10_000).state).toBe("gate");
  });

  it("reads running off the newest step while that step is recent", () => {
    const items = [round(0, 1, 0), round(30, 2, 1)];
    const head = label(items, "running", 40);
    expect(head.state).toBe("running");
    expect(head.label).toBe("Running");
  });

  it("reads waiting-on-a-child when the root opened a call and the newest event is below", () => {
    const start = item({
      ts: at(10),
      offset: 1,
      kind: "llm_call_start",
      payload: { call_id: "abc", node: "l1_generate", started_at_ms: 0 },
    });
    const head = label([round(0, 1, 0), start, innerRound(20, 1, 0)], "running", 30);
    expect(head.state).toBe("waiting");
    expect(head.target?.[head.target.length - 1]?.cycleId).toBe("cycle_in");
  });

  it("stops waiting once the matching llm_call lands", () => {
    const start = item({
      ts: at(10),
      offset: 1,
      kind: "llm_call_start",
      payload: { call_id: "abc", node: "l1_generate", started_at_ms: 0 },
    });
    const done = item({
      ts: at(15),
      offset: 2,
      kind: "llm_call",
      payload: { call_id: "abc", node: "l1_generate", payload_kind: "full", payload: {} },
    });
    expect(label([round(0, 1, 0), start, done, innerRound(20, 1, 0)], "running", 30).state).toBe(
      "running",
    );
  });

  it("defers to the server for every state the server owns", () => {
    const items = [round(0, 1, 0)];
    expect(label(items, "terminal", 1).state).toBe("finished");
    expect(label(items, "terminal", 1).label).toBe("Finished");
    expect(label(items, "paused", 1).state).toBe("paused");
    expect(label(items, "detached", 1).state).toBe("detached");
    expect(label(items, "checkin", 1).state).toBe("idle");
  });
});

describe("fmtGap", () => {
  it("renders nothing below six missed heartbeats", () => {
    expect(fmtGap(0)).toBe("");
    expect(fmtGap(89)).toBe("");
  });

  it("steps through minutes, hours, then days", () => {
    expect(fmtGap(90)).toBe("2m");
    expect(fmtGap(600)).toBe("10m");
    expect(fmtGap(3 * 3600)).toBe("3h");
    expect(fmtGap(14 * 3600)).toBe("14h");
    expect(fmtGap(3 * 86_400)).toBe("3d");
  });
});
