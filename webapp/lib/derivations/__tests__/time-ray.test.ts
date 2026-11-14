import { describe, expect, it } from "vitest";
import { fmtGap } from "@/lib/format";
import { raySteps } from "@/lib/derivations";
import type { HierarchyStep, StepFacts } from "@/lib/api/types";
import { hierarchyStep } from "@/lib/test-fixtures";

const ROOT = "camp::cycle_root";
const INNER = "camp::cycle_root~inner::cycle_in";

const T0 = Date.parse("2026-01-01T00:00:00Z");
const at = (sec: number): string => new Date(T0 + sec * 1000).toISOString();

function item(
  over: { ts: string; offset?: number; gap?: number; label?: string } & Partial<StepFacts>,
  course = ROOT,
): HierarchyStep {
  return hierarchyStep(course, {
    offset: over.offset ?? 0,
    ts: over.ts,
    gap_before_s: over.gap ?? 0,
    activity: { key: "x", tone: "success", label: over.label ?? "Round 1", value: null, sub: null, detail: null },
    round: over.round ?? 1,
    arm: over.arm ?? null,
  });
}

function round(sec: number, n: number, offset: number, gap = 0): HierarchyStep {
  return item({ ts: at(sec), offset, gap, label: `Round ${n}`, round: n });
}

function innerRound(sec: number, n: number, offset: number, gap = 0): HierarchyStep {
  return item({ ts: at(sec), offset, gap, label: `Round ${n}`, round: n }, INNER);
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
    const minted = item({ ts: at(0), label: "C2.1", round: 2, arm: "c2a" });
    const steps = raySteps([minted, round(1, 3, 1)], ROOT);
    expect(steps[0]?.arm).toBe("c2a");
    expect(steps[0]?.round).toBe(2);
    expect(steps[1]?.arm).toBeNull();
    expect(steps[1]?.round).toBe(3);
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
