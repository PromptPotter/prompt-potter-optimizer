import { describe, expect, it } from "vitest";
import type { LineageNode } from "@/lib/api";
import { candidatesAtPath, innerPanelIndex, panelCellKey } from "@/lib/derivations";
import type { CyclePath } from "@/lib/ids";

// The served tree dissolves a fork: its attempts carry the FORK's `path` and a `label`
// renumbered onto the parent's timeline; `course_label` is what the fork itself minted.

const ROOT: CyclePath = [{ campaignId: "demo__aaaaaa", cycleId: "cycle_root" }];
const FORK: CyclePath = [{ campaignId: "demo__aaaaaa", cycleId: "cycle_root_fork_beef" }];

const hops = (path: CyclePath) =>
  path.map((h) => ({ campaign_id: h.campaignId, cycle_id: h.cycleId }));

function node(over: Partial<LineageNode> & Pick<LineageNode, "kind" | "id" | "label">): LineageNode {
  return {
    parent_id: null,
    course_label: over.label,
    path: [],
    children: [],
    round: null,
    accuracy: null,
    composite_fitness: null,
    state: "",
    is_winner: false,
    theta: null,
    theta_se: null,
    evaluators: {},
    mean_fitness_ci_lo: null,
    mean_fitness_ci_hi: null,
    scored_samples: null,
    expected_samples: null,
    lens_value: null,
    sample_set_accuracy: null,
    sample_set_n: null,
    divergence: null,
    divergent: false,
    course_kind: null,
    run_phase: "terminal",
    dataset_name: "",
    trigger: null,
    steered_by: null,
    task: null,
    best_accuracy: null,
    origin_accuracy: null,
    hearts: null,
    lives_cap: null,
    ...over,
  } as LineageNode;
}

const innerRun = (id: string, task: string) =>
  node({
    kind: "course",
    id,
    label: id,
    task,
    path: hops([...ROOT, { campaignId: "inner__1", cycleId: id }]),
  });

// Root minted C0 + C1.1; its fork's own C1.1 is renumbered C1.2 on the timeline.
function tree(): LineageNode {
  return node({
    kind: "course",
    id: "cycle_root",
    label: "cycle_root",
    path: hops(ROOT),
    children: [
      node({
        kind: "candidate",
        id: "root-c0",
        label: "C0",
        round: 0,
        path: hops(ROOT),
        children: [innerRun("inner_a", "justlogic-d234/seed-0")],
      }),
      node({
        kind: "candidate",
        id: "root-c1",
        label: "C1.1",
        round: 1,
        path: hops(ROOT),
        children: [innerRun("inner_b", "justlogic-d234/seed-0")],
      }),
      node({
        kind: "candidate",
        id: "fork-c1",
        label: "C1.2",
        course_label: "C1.1",
        round: 1,
        course_kind: "fork",
        path: hops(FORK),
        children: [innerRun("inner_c", "justlogic-d234/seed-0")],
      }),
    ],
  });
}

describe("candidatesAtPath", () => {
  it("addresses a fork's attempts, which no course lookup can reach", () => {
    expect(candidatesAtPath(tree(), FORK).map((c) => c.id)).toEqual(["fork-c1"]);
  });

  it("gives a course its OWN candidates, without the ones a fork contributed to it", () => {
    // The root's own `dashboard.json` never minted the fork's attempt.
    expect(candidatesAtPath(tree(), ROOT).map((c) => c.id)).toEqual(["root-c0", "root-c1"]);
  });

  it("does not match a different campaign that reuses a cycle id", () => {
    // Inner ids repeat across sibling sandboxes, so the address is the whole path.
    const other: CyclePath = [{ campaignId: "other__bbbbbb", cycleId: "cycle_root" }];
    expect(candidatesAtPath(tree(), other)).toEqual([]);
  });
});

describe("innerPanelIndex", () => {
  it("keys a fork's cells on the label the FORK minted, not the renumbered one", () => {
    // Panel rows come from the leaf's per-cycle `dashboard.json` (C1.1) while the timeline
    // says C1.2: keying on `label` silently resolves nothing.
    const panel = innerPanelIndex(tree(), FORK);
    expect(panel.get(panelCellKey("C1.1", "justlogic-d234/seed-0"))?.id).toBe("inner_c");
    expect(panel.get(panelCellKey("C1.2", "justlogic-d234/seed-0"))).toBeUndefined();
  });

  it("resolves an ordinary course's cells, where both labels agree", () => {
    const panel = innerPanelIndex(tree(), ROOT);
    expect(panel.get(panelCellKey("C0", "justlogic-d234/seed-0"))?.id).toBe("inner_a");
    expect(panel.get(panelCellKey("C1.1", "justlogic-d234/seed-0"))?.id).toBe("inner_b");
    expect([...panel.values()].map((r) => r.id)).not.toContain("inner_c");
  });

  it("drops a run with no task rather than guessing its cell", () => {
    const t = tree();
    const cand = t.children[0]!;
    const run = cand.children[0]!;
    const noTask = { ...t, children: [{ ...cand, children: [{ ...run, task: null }] }] };
    expect(innerPanelIndex(noTask, ROOT).size).toBe(0);
  });

  it("is empty rather than throwing when the tree or the address is missing", () => {
    expect(innerPanelIndex(null, ROOT).size).toBe(0);
    expect(innerPanelIndex(tree(), null).size).toBe(0);
  });
});
