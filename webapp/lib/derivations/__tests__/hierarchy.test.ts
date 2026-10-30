import { describe, expect, it } from "vitest";
import { dockPriority, groupByRoot, runningCourses } from "@/lib/derivations";
import type { HierarchyCampaign } from "@/lib/api";
import { subtreeCampaign, subtreeCourse, hierarchyState } from "@/lib/test-fixtures";

function rootedAt(address: string, rootCycle: string): HierarchyCampaign {
  const c = subtreeCampaign({ address });
  return { ...c, facts: { ...c.facts, root: `${address}::${rootCycle}` } };
}

describe("groupByRoot", () => {
  it("groups consecutive campaigns sharing a decoded root cycle, keeping served order", () => {
    const groups = groupByRoot([
      rootedAt("camp_a", "cycle_x"),
      rootedAt("camp_b", "cycle_x"),
      rootedAt("camp_c", "cycle_y"),
    ]);
    expect(groups.map((g) => [g.rootCycleId, g.campaigns.map((c) => c.address)])).toEqual([
      ["cycle_x", ["camp_a", "camp_b"]],
      ["cycle_y", ["camp_c"]],
    ]);
  });
});

describe("runningCourses", () => {
  it("keeps only attached courses, ordered by the served dock priority", () => {
    const attached = (run_phase: "running" | "paused" | "queued") =>
      hierarchyState({
        status: { run_phase, stop_reason: null, mark: run_phase },
        attached: true,
      });
    const campaign = {
      ...subtreeCampaign({ address: "camp_a" }),
      children: [
        subtreeCourse({ address: "camp_a::cycle_1", state: attached("paused") }),
        subtreeCourse({ address: "camp_a::cycle_2" }),
        subtreeCourse({ address: "camp_a::cycle_3", state: attached("running") }),
        subtreeCourse({ address: "camp_a::cycle_4", state: attached("queued") }),
      ],
    };
    const running = runningCourses([campaign]);
    expect(running.map((c) => c.address).sort()).toEqual([
      "camp_a::cycle_1",
      "camp_a::cycle_3",
      "camp_a::cycle_4",
    ]);
    const priorities = running.map((c) => dockPriority(c.state.status.run_phase));
    expect(priorities).toEqual([...priorities].sort((a, b) => a - b));
  });
});
