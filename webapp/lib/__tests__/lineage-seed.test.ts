import { describe, expect, it } from "vitest";
import { subtreeRead } from "@/lib/api/reads";
import { treeMaskOf } from "@/lib/lineage";
import { lensOf, NO_DIALS, seededMask, type MaskState } from "@/lib/scoring-mask";
import { dash } from "@/lib/test-fixtures";

const OPEN: MaskState = { open: true, mask: NO_DIALS, seededForCycle: null };

const lensOfRead = (id: string) => new URL(id.split("\x1f")[1]!, "http://x").searchParams.get("lens");

// The campaign tree the viewed cycle asks for under `state`, keyed off the mask alone.
function treeFor(state: MaskState, cycleId: string) {
  return subtreeRead("a", treeMaskOf(state, cycleId, lensOf(state.mask, []), null), null, null);
}

describe("the viewed tree under an open mask panel", () => {
  it("holds a new cycle's first tree on the campaign's last one until that cycle's own seed lands", () => {
    const c1 = seededMask(OPEN, "c1", dash({ composite_fitness_formula: "fitness", composite_fitness_weights: null }));
    expect(lensOfRead(treeFor(c1, "c1").id)).toBe("score:fitness");

    // Switched before c2's dashboard lands: held on the campaign's keep, asking nothing.
    const held = treeFor(c1, "c2");
    expect(held.held).toBe(true);
    expect(held.keep).toBe(treeFor(c1, "c1").keep);

    const c2 = seededMask(c1, "c2", dash({ composite_fitness_formula: "fitness - cost", composite_fitness_weights: null }));
    expect(lensOfRead(treeFor(c2, "c2").id)).toBe("score:fitness - cost");
  });

  it("seeds once per cycle, so a later landing never overwrites the operator's edit", () => {
    const c1 = seededMask(OPEN, "c1", dash({ composite_fitness_formula: "fitness", composite_fitness_weights: null }));
    const edited: MaskState = { ...c1, mask: { kind: "expression", lens: "score:cost" } };
    expect(seededMask(edited, "c1", dash({ composite_fitness_formula: "fitness", composite_fitness_weights: null }))).toBe(edited);
  });

  it("asks a cycle's tree with no lens when its dashboard serves no formula", () => {
    const seeded = seededMask(OPEN, "c4", dash({ composite_fitness_formula: null, composite_fitness_weights: null }));
    const read = treeFor(seeded, "c4");
    expect(read.held).toBeUndefined();
    expect(lensOfRead(read.id)).toBeNull();
  });

  it("never holds a tree while the mask panel is closed", () => {
    expect(treeMaskOf({ ...OPEN, open: false }, "c9", null, null)).toBeNull();
  });
});
