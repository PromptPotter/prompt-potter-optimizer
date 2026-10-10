import { describe, expect, it } from "vitest";
import { fmtGap } from "@/lib/format";
import { raySteps, rayHead } from "@/lib/derivations";
import type { ActivityItem, ProducerReading, RayItem } from "@/lib/api/types";
import type { RunPhase, RunStatus } from "@/lib/api/types.generated";
import { producerReading as producer } from "@/lib/test-fixtures";

type ProducerState = ProducerReading["state"];

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
    candidate: null,
    ...over,
  };
}

function item(over: Partial<RayItem> & { ts: string }): RayItem {
  return {
    path: [{ campaign_id: "camp", cycle_id: "cycle_root" }],
    offset: 0,
    gap_before_s: 0,
    activity: read({}),
    ...over,
  };
}

function round(sec: number, n: number, offset: number, gap = 0): RayItem {
  return item({
    ts: at(sec),
    offset,
    gap_before_s: gap,
    activity: read({ label: `Round ${n}`, round: n }),
  });
}

function heartbeat(sec: number, offset: number, gap = 0): RayItem {
  return item({ ts: at(sec), offset, gap_before_s: gap, activity: null });
}

function innerRound(sec: number, n: number, offset: number, gap = 0): RayItem {
  return item({
    ts: at(sec),
    offset,
    gap_before_s: gap,
    path: [
      { campaign_id: "camp", cycle_id: "cycle_root" },
      { campaign_id: "inner", cycle_id: "cycle_in" },
    ],
    activity: read({ label: `Round ${n}`, round: n }),
  });
}

describe("raySteps", () => {
  it("drops bare heartbeats from the steps and reads each step's served gap", () => {
    // The server measures a gap from the item before it, heartbeats included; a step never sums them.
    const steps = raySteps(
      [round(0, 1, 0), heartbeat(60, 1, 60), heartbeat(110, 2, 50), round(120, 2, 3, 10)],
      ROOT,
    );
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

  it("carries the round and the candidate label a click needs", () => {
    const minted = item({
      ts: at(0),
      activity: read({ kind: "candidate", label: "C2.1", round: 2, candidate: "C2.1" }),
    });
    const steps = raySteps([minted, round(1, 3, 1)], ROOT);
    expect(steps[0]?.candidateLabel).toBe("C2.1");
    expect(steps[0]?.round).toBe(2);
    expect(steps[1]?.candidateLabel).toBeNull();
    expect(steps[1]?.round).toBe(3);
  });
});

describe("rayHead", () => {
  // A served status as a response holds it: the word is the server's, so the fixture's is opaque.
  const RUNNING: RunStatus = { label: "served running", mark: "running" };

  const head = (
    items: RayItem[],
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

  const RUNNING_HEAD: Record<ProducerState, [boolean, string]> = {
    live: [false, "Round 2"],
    idle: [false, "no recent step"],
    held: [false, "no recent step"],
    silent: [false, "no recent step"],
    absent: [false, "no recent step"],
    claimed: [false, "no recent step"],
    wedged: [true, "no progress recorded"],
  };

  it.each(Object.entries(RUNNING_HEAD) as [ProducerState, [boolean, string]][])(
    "reads the head of a running cycle under a %s producer",
    (state, [wedged, detail]) => {
      const read = head([round(0, 1, 0), round(30, 2, 1)], "running", producer(state));
      expect([read.wedged, read.detail]).toEqual([wedged, detail]);
    },
  );

  it("reads wedged off the served reading, in the server's own sentence", () => {
    const stalled = "no progress for 6m";
    const read = head([round(0, 1, 0)], "running", producer("wedged", { stalled }));
    expect([read.wedged, read.label, read.detail]).toEqual([true, "Wedged", stalled]);
  });

  it("heads an open cell with the producer's served word and its served clock", () => {
    const measuring = producer("idle", { label: "served measuring", silent_for_s: 400, open_for_s: 261 });
    const read = head([round(0, 1, 0)], "running", measuring, RUNNING, "C1.1 · sample 12");
    expect(read.label).toBe("served measuring");
    expect(read.detail).toBe("C1.1 · sample 12 · open 4m");
  });

  it("reads waiting-on-a-child when the root opened a call and the newest event is below", () => {
    const start = item({ ts: at(10), offset: 1, activity: read({ kind: "running" }) });
    const waiting = producer("live", { label_on_child: "served waiting" });
    const got = head([round(0, 1, 0), start, innerRound(20, 1, 0)], "running", waiting);
    expect([got.label, got.tone]).toEqual(["served waiting", "quiet"]);
    expect(got.target?.[got.target.length - 1]?.cycleId).toBe("cycle_in");
  });

  it("stops waiting once the call's completion lands", () => {
    const start = item({ ts: at(10), offset: 1, activity: read({ kind: "running" }) });
    const done = item({ ts: at(15), offset: 2, activity: read({ kind: "done" }) });
    const items = [round(0, 1, 0), start, done, innerRound(20, 1, 0)];
    expect(head(items, "running", producer("live")).label).toBe("Running");
  });

  it("defers to the server for every state the server owns", () => {
    const items = [round(0, 1, 0)];
    expect(
      head(items, "terminal", producer("absent"), { label: "served ending", mark: "failed" }),
    ).toMatchObject({ label: "served ending", tone: "danger" });
    expect(
      head(items, "terminal", producer("absent"), { label: "served ending", mark: "success" }).tone,
    ).toBe("success");
    expect(
      head(items, "gate", producer("held"), { label: "served gate", mark: "gate" }),
    ).toMatchObject({ label: "served gate", tone: "attention" });
    expect(head(items, "paused", producer("absent")).detail).toBe("resumable");
    expect(head(items, "paused", producer("live")).detail).toBe("stopping at its next checkpoint");
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
