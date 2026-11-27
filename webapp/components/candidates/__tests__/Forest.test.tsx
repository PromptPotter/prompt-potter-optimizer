import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CycleSpawn, HierarchyCandidate, SubtreeCycle } from "@/lib/api/types";
import { candidateSubject } from "@/lib/api/reads";
import { encodeCyclePath } from "@/lib/ids";
import {
  armReading,
  cycleTags,
  hierarchyCandidate,
  ownLevel,
  roundsOf,
  subtreeCampaign,
  subtreeCycle,
} from "@/lib/test-fixtures";
import { Forest, type CladogramCtx } from "../Forest";

const ROOT = encodeCyclePath([{ campaignId: "camp", cycleId: "root" }]);
const FORK = encodeCyclePath([{ campaignId: "camp", cycleId: "fork" }]);
// One arm key re-proposed on two branches: r2 of the root and r3 of the fork.
const DUP = "c8c1b433";

function cand(cycle: string, round: number, key: string, divergence: string | null = null) {
  const c = hierarchyCandidate(cycle, {
    id: key,
    reading: armReading({
      arm: { round, key, label: `C${round}.${key}` },
      own: ownLevel(0.5),
      election: { held: true, selected: false },
    }),
  });
  c.facts.divergence = divergence === null ? null : { alternative: divergence };
  return c;
}

function cycle(address: string, cands: HierarchyCandidate[], spawn: CycleSpawn | null) {
  const mint = spawn ? "fork" : "root";
  return subtreeCycle(
    { address, tags: cycleTags(mint, "ds"), children: roundsOf(address, cands) },
    { mint, spawn },
  );
}

const rootDup = cand(ROOT, 2, DUP);
const forkDup = cand(FORK, 3, DUP);
// The root's r2 diverges under the lens, naming the root's own `DUP` as its alternative.
const rootCycle = cycle(ROOT, [cand(ROOT, 1, "a1"), cand(ROOT, 2, "a2", rootDup.address), rootDup], null);
const forkCycle: SubtreeCycle = cycle(FORK, [cand(FORK, 2, "b2"), forkDup], {
  parent: ROOT,
  cut_round: 2,
  trigger: "operator_rewind",
  direction: "supersede",
  issued_by: "",
});
const tree = subtreeCampaign({ address: "camp", children: [rootCycle, forkCycle] });

function lit(picked: string | null) {
  const ctx: CladogramCtx = {
    viewedKey: ROOT,
    channels: [],
    clip: null,
    isPicked: (n) => n.candKey === picked,
    onPickCandidate: () => {},
  };
  const { container } = render(
    <Forest
      tree={tree}
      valueByKey={new Map()}
      metric="fitness"
      expanded={new Set([ROOT, FORK])}
      onLaneActivate={() => {}}
      ctx={ctx}
    />,
  );
  const labels = (cls: string) =>
    [...container.querySelectorAll(`.lineage-node.${cls}`)].map((n) => n.getAttribute("aria-label"));
  return { selected: labels("selected"), alt: labels("mask-alt") };
}

describe("one arm key re-proposed on two branches", () => {
  // jsdom lays nothing out: the box is never measured, so the tree draws at its default density.
  beforeEach(() => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
  });

  it("keys two subjects, one per branch", () => {
    expect(candidateSubject(rootDup.address)).toBe(`candidate:camp/root/r2/${DUP}`);
    expect(candidateSubject(forkDup.address)).toBe(`candidate:camp/fork/r3/${DUP}`);
  });

  it("lights only the picked one, and only the one the divergence names", () => {
    const { selected, alt } = lit(forkDup.address);
    expect(selected).toHaveLength(1);
    expect(selected[0]).toMatch(/^Round 3 candidate /);
    expect(alt).toHaveLength(1);
    expect(alt[0]).toMatch(/^Round 2 candidate /);
  });
});
