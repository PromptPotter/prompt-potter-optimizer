import { describe, expect, it } from "vitest";
import { cycleOf, flowOrder, layoutGrid } from "../pipeline-layout";
import type { PipelineViewEdge, PipelineViewNode } from "@/components/workflow";

const node = (
  id: string,
  tier = 0,
  rank = 0,
  kind = "llm",
): PipelineViewNode => ({ id, label: id, kind, tier, rank });

const edge = (from: string, to: string, kind = "forward"): PipelineViewEdge => ({
  from,
  to,
  kind,
});

// The optimizer's shape: a three-step round that repeats, a check-in that runs once ahead
// of it, and two escalations reached only by leaving the round.
const OPT_NODES = [
  node("checkin", 0, 0),
  node("l1_generate", 0, 1),
  node("l1_score", 0, 2, "measurement"),
  node("l1_critique", 0, 3),
  node("l2_context", 1, 1),
  node("l3_plan", 2, 1),
];
const OPT_EDGES = [
  edge("checkin", "l1_generate"),
  edge("l1_generate", "l1_score"),
  edge("l1_score", "l1_critique"),
  edge("l1_critique", "l1_generate", "loop"),
  edge("l1_critique", "l2_context", "escalate"),
  edge("l2_context", "l1_generate", "directive"),
  edge("l1_critique", "l3_plan", "escalate"),
  edge("l3_plan", "l2_context", "directive"),
];

const OPTS = { cell: 132, rowH: 72, padTop: 16, padBottom: 38 };

describe("cycleOf", () => {
  it("names the repeating steps in flow order, off the loop edge", () => {
    expect(cycleOf(OPT_NODES, OPT_EDGES)).toEqual([
      "l1_generate",
      "l1_score",
      "l1_critique",
    ]);
  });

  it("is empty for a chain — every dataset, which must keep the rail", () => {
    const nodes = [node("a", 0, 0), node("b", 0, 1), node("c", 0, 2)];
    expect(cycleOf(nodes, [edge("a", "b"), edge("b", "c")])).toEqual([]);
  });

  it("is empty when the loop's ends are not joined by forward steps", () => {
    const nodes = [node("a"), node("b")];
    expect(cycleOf(nodes, [edge("b", "a", "loop")])).toEqual([]);
  });
});

describe("flowOrder", () => {
  it("runs preamble, then the round, then each escalation by depth", () => {
    const ids = flowOrder(OPT_NODES, cycleOf(OPT_NODES, OPT_EDGES)).map((n) => n.id);
    expect(ids).toEqual([
      "checkin",
      "l1_generate",
      "l1_score",
      "l1_critique",
      "l2_context",
      "l3_plan",
    ]);
  });
});

describe("layoutGrid", () => {
  const out = layoutGrid(OPT_NODES, cycleOf(OPT_NODES, OPT_EDGES), OPTS);
  const at = (id: string) => out.pos.get(id)!;

  it("places every node exactly once", () => {
    expect([...out.pos.keys()].sort()).toEqual(OPT_NODES.map((n) => n.id).sort());
  });

  // The arrangement, spelled out: across the top, fold, back along the bottom.
  it("lays the optimizer out as two rows of three, serpentine", () => {
    expect(out.cols).toBe(3);
    expect(out.rows).toBe(2);
    const cell = (id: string) => [at(id).row, at(id).col];
    expect(cell("checkin")).toEqual([0, 0]);
    expect(cell("l1_generate")).toEqual([0, 1]);
    expect(cell("l1_score")).toEqual([0, 2]);
    expect(cell("l1_critique")).toEqual([1, 2]);
    expect(cell("l2_context")).toEqual([1, 1]);
    expect(cell("l3_plan")).toEqual([1, 0]);
  });

  it("puts the fold under the step it follows — no edge crosses the diagram", () => {
    // `l1_score → l1_critique` is the fold: same column, one row down.
    expect(at("l1_score").col).toBe(at("l1_critique").col);
    expect(at("l1_critique").row).toBe(at("l1_score").row + 1);
    // and the directive back up sits in its own column, one row apart.
    expect(at("l2_context").col).toBe(at("l1_generate").col);
  });

  it("recedes what runs once, and only that", () => {
    expect(at("checkin").muted).toBe(true);
    for (const id of ["l1_generate", "l1_score", "l1_critique", "l2_context", "l3_plan"]) {
      expect(at(id).muted, id).toBe(false);
    }
  });

  it("keeps every node — and the text under it — inside the canvas", () => {
    for (const [id, p] of out.pos) {
      expect(p.x, `${id}.x`).toBeGreaterThanOrEqual(0);
      expect(p.x, `${id}.x`).toBeLessThanOrEqual(out.width);
      expect(p.y, `${id}.y`).toBeGreaterThanOrEqual(0);
      // Every row labels BELOW, so the last row needs its full allowance under it.
      expect(p.y + OPTS.padBottom, `${id}.y`).toBeLessThanOrEqual(out.height);
    }
  });

  it("clears a row's labels before the next row's dots", () => {
    expect(OPTS.rowH).toBeGreaterThan(OPTS.padBottom);
  });

  it("folds an odd count with the last row one short", () => {
    const five = OPT_NODES.filter((n) => n.id !== "l3_plan");
    const odd = layoutGrid(five, cycleOf(five, OPT_EDGES), OPTS);
    expect(odd.cols).toBe(3);
    expect(odd.rows).toBe(2);
    expect([...odd.pos.values()].filter((p) => p.row === 1)).toHaveLength(2);
  });
});
