import { describe, expect, it } from "vitest";
import { fmtGap } from "@/lib/format";
import { raySteps, rayHead } from "@/lib/derivations";
import type { ActivityItem, HierarchyStep, ProducerReading } from "@/lib/api/types";
import type { RunPhase, RunStatus } from "@/lib/api/types.generated";
import { hierarchyStep, producerReading as producer } from "@/lib/test-fixtures";

const ROOT = "camp::cycle_root";
const INNER = "camp::cycle_root~inner::cycle_in";

const T0 = Date.parse("2026-01-01T00:00:00Z");
const at = (sec: number): string => new Date(T0 + sec * 1000).toISOString();

function read(over: Partial<ActivityItem>): ActivityItem {
  return {
    id: "x",
    kind: "round",
    icon: "★",
    label: "Round 1",
    detail: null,
    tone: "good",
    round: 1,
    arm: null,
    ...over,
  };
}

function item(
  over: { ts: string; offset?: number; gap?: number; activity?: ActivityItem },
  course = ROOT,
): HierarchyStep {
  return hierarchyStep(course, {
    offset: over.offset ?? 0,
    ts: over.ts,
    gap_before_s: over.gap ?? 0,
    activity: over.activity ?? read({}),
  });
}

function round(sec: number, n: number, offset: number, gap = 0): HierarchyStep {
  return item({ ts: at(sec), offset, gap, activity: read({ label: `Round ${n}`, round: n }) });
}

function innerRound(sec: number, n: number, offset: number, gap = 0): HierarchyStep {
  return item(
    { ts: at(sec), offset, gap, activity: read({ label: `Round ${n}`, round: n }) },
    INNER,
  );
}

describe("raySteps", () => {
  it("reads each step's served gap, never a sum of its own", () => {
    // The server measures a gap from the record before it, heartbeats included, and serves no heartbeat.
    const steps = raySteps([round(0, 1, 0), round(120, 2, 3, 10)], ROOT);
    expect(steps).toHaveLength(2);
    expect(steps[1]?.gapBeforeS).toBe(10);
    expect(fmtGap(steps[1]!.gapBeforeS)).toBe("");
  });

  it("marks a real silence when nothing at all was recorded", () => {
    const steps = raySteps([round(0, 1, 0), round(600, 2, 1, 600)], ROOT);
    expect(steps[1]?.gapBeforeS).toBe(600);
    expect(fmtGap(steps[1]!.gapBeforeS)).toBe("10m");
  });

  it("clusters consecutive inner-cycle steps and leaves the root's own alone", () => {
    const steps = raySteps(
      [
        round(0, 1, 0),
        innerRound(1, 1, 0, 1),
        innerRound(2, 2, 1, 1),
        innerRound(3, 3, 2, 1),
        round(4, 2, 1, 1),
      ],
      ROOT,
    );
    expect(steps.map((s) => s.pathKey)).toEqual([ROOT, INNER, ROOT]);
    expect(steps[1]?.cluster).toBe(3);
    // The cluster's gap is the one before the run began; interior silences are no pause.
    expect(steps[1]?.gapBeforeS).toBe(1);
    // Consecutive ROOT steps never cluster: they are the story, not a digression.
    expect(steps[0]?.cluster).toBe(1);
    expect(steps[2]?.cluster).toBe(1);
  });

  it("carries the round and the arm key a click needs", () => {
    const minted = item({
      ts: at(0),
      activity: read({ kind: "candidate", label: "C2.1", round: 2, arm: "c2a" }),
    });
    const steps = raySteps([minted, round(1, 3, 1)], ROOT);
    expect(steps[0]?.arm).toBe("c2a");
    expect(steps[0]?.round).toBe(2);
    expect(steps[1]?.arm).toBeNull();
    expect(steps[1]?.round).toBe(3);
  });
});

describe("rayHead", () => {
  // A served status as a response holds it: the word is the server's, so the fixture's is opaque.
  const RUNNING: RunStatus = { label: "served running", mark: "running" };

  const head = (
    items: HierarchyStep[],
    phase: RunPhase,
    reading: ProducerReading,
    status: RunStatus = RUNNING,
    waitingOn: string | null = null,
  ) => {
    const out = rayHead(
      raySteps(items, ROOT),
      items,
      { run_phase: phase, status, producer: reading },
      ROOT,
      waitingOn,
    );
    if (out === null) throw new Error("a served run has a head");
    return out;
  };

  it("states no head before the first dashboard lands", () => {
    const items = [round(0, 1, 0)];
    expect(rayHead(raySteps(items, ROOT), items, null, ROOT, null)).toBeNull();
  });

  it("names the newest step only while the served producer is stepping", () => {
    const items = [round(0, 1, 0), round(30, 2, 1)];
    const stepping = head(items, "running", producer({ state: "live", stepping: true }));
    expect([stepping.wedged, stepping.detail]).toEqual([false, "Round 2"]);
    const quiet = head(items, "running", producer({ state: "idle" }));
    expect([quiet.wedged, quiet.detail]).toEqual([false, "no recent step"]);
  });

  it("reads wedged off the served reading, in the server's own sentence", () => {
    const stalled = "no progress for 6m";
    const read = head([round(0, 1, 0)], "running", producer({ state: "wedged", label: "Wedged", stalled }));
    expect([read.wedged, read.label, read.detail]).toEqual([true, "Wedged", stalled]);
  });

  it("heads an open cell with the producer's served word and its served clock", () => {
    const measuring = producer({
      state: "idle",
      label: "served measuring",
      silent_for_s: 400,
      open_for_s: 261,
    });
    const read = head([round(0, 1, 0)], "running", measuring, RUNNING, "C1.1 · sample 12");
    expect(read.label).toBe("served measuring");
    expect(read.detail).toBe("C1.1 · sample 12 · open 4m");
  });

  it("reads waiting-on-a-child when the root opened a call and the newest event is below", () => {
    const start = item({ ts: at(10), offset: 1, activity: read({ kind: "running" }) });
    const waiting = producer({ state: "live", label_on_child: "served waiting" });
    const got = head([round(0, 1, 0), start, innerRound(20, 1, 0)], "running", waiting);
    expect([got.label, got.tone]).toEqual(["served waiting", "quiet"]);
    expect(got.target?.[got.target.length - 1]?.cycleId).toBe("cycle_in");
  });

  it("stops waiting once the call's completion lands", () => {
    const start = item({ ts: at(10), offset: 1, activity: read({ kind: "running" }) });
    const done = item({ ts: at(15), offset: 2, activity: read({ kind: "done" }) });
    const items = [round(0, 1, 0), start, done, innerRound(20, 1, 0)];
    const live = producer({ state: "live", label: "Running" });
    expect(head(items, "running", live).label).toBe("Running");
  });

  it("defers to the server for every state the server owns", () => {
    const items = [round(0, 1, 0)];
    expect(
      head(items, "terminal", producer(), { label: "served ending", mark: "failed" }),
    ).toMatchObject({ label: "served ending", tone: "danger" });
    expect(
      head(items, "terminal", producer(), { label: "served ending", mark: "success" }).tone,
    ).toBe("success");
    expect(
      head(items, "gate", producer({ state: "held", attached: true }), {
        label: "served gate",
        mark: "gate",
      }),
    ).toMatchObject({ label: "served gate", tone: "attention" });
    expect(head(items, "paused", producer()).detail).toBe("resumable");
    const attached = producer({ state: "live", attached: true });
    expect(head(items, "paused", attached).detail).toBe("stopping at its next checkpoint");
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
