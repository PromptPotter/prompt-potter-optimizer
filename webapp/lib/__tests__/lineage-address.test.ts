import { describe, expect, it } from "vitest";
import type { LineageNode } from "@/lib/api";
import { candidatesAtPath, innerPanelIndex, panelCellKey } from "@/lib/derivations";
import type { CyclePath } from "@/lib/ids";

// The served tree collapses a fork onto its parent's timeline: the fork is NOT a node, and
// its contributed attempts carry the FORK's `path` while wearing a `label` renumbered onto
// the parent's sequence. `course_label` is the label the fork itself minted.
//
// Both facts below are what a per-cycle projection joins against, and both fail SILENTLY
// when wrong — a mis-addressed lookup yields an empty map, and the L4 panel renders cells
// that say "no run recorded" for runs that are sitting right there on disk.

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

// A campaign whose root minted C0 + C1.1, and whose fork contributed one attempt that the
// timeline renumbered to C1.2 (the fork had minted it as its own C1.1). Each candidate was
// measured by an inner L4 run.
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
      // The fork's contribution: renumbered `label`, private `course_label`, fork `path`.
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
    // A fork has no course node — the server dissolved it — so its contributed attempts,
    // each carrying the fork's own path, are its only trace in the tree.
    expect(candidatesAtPath(tree(), FORK).map((c) => c.id)).toEqual(["fork-c1"]);
  });

  it("gives a course its OWN candidates, without the ones a fork contributed to it", () => {
    // The fork's attempt renders on the root's timeline, but it belongs to the fork: the
    // root's own `dashboard.json` never minted it and has no row for it.
    expect(candidatesAtPath(tree(), ROOT).map((c) => c.id)).toEqual(["root-c0", "root-c1"]);
  });

  it("does not match a different campaign that reuses a cycle id", () => {
    // Inner ids repeat across sibling sandboxes, which is why the address is the whole
    // path and never a bare cycle_id.
    const other: CyclePath = [{ campaignId: "other__bbbbbb", cycleId: "cycle_root" }];
    expect(candidatesAtPath(tree(), other)).toEqual([]);
  });
});

describe("innerPanelIndex", () => {
  it("keys a fork's cells on the label the FORK minted, not the renumbered one", () => {
    // This is the whole reason `course_label` is served. The panel rows come from the
    // leaf's own `dashboard.json`, which is per-cycle and says `C1.1`; the campaign
    // timeline says `C1.2`. Keying on `label` looks right and silently resolves nothing.
    const panel = innerPanelIndex(tree(), FORK);
    expect(panel.get(panelCellKey("C1.1", "justlogic-d234/seed-0"))?.id).toBe("inner_c");
    expect(panel.get(panelCellKey("C1.2", "justlogic-d234/seed-0"))).toBeUndefined();
  });

  it("resolves an ordinary course's cells, where both labels agree", () => {
    const panel = innerPanelIndex(tree(), ROOT);
    expect(panel.get(panelCellKey("C0", "justlogic-d234/seed-0"))?.id).toBe("inner_a");
    expect(panel.get(panelCellKey("C1.1", "justlogic-d234/seed-0"))?.id).toBe("inner_b");
    // The fork's run belongs to the fork's panel, not this one.
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
