import { describe, expect, it } from "vitest";
import {
  BOTTOM_PAD,
  FIRST_ROW_Y,
  NODE_R,
  NODE_ROW_Y,
  ROW_H,
  dendrogram,
  type DendroRow,
} from "../dendrogram";

// Each candidate is served as descending from the newest crown before it, as a mutation of the line.
function spine(
  rounds: { round: number; n: number; winner: number | null }[],
): DendroRow[] {
  const rows: DendroRow[] = [];
  let crown: string | null = null;
  for (const r of rounds) {
    const from = crown;
    for (let i = 0; i < r.n; i++) {
      const address = `r${r.round}_${i}`;
      rows.push({
        key: `R${r.round}.${i}`,
        round: r.round,
        label: r.round === 0 ? "C0" : `C${r.round}.${i + 1}`,
        address,
        is_selected: r.winner === i,
        crown: r.winner !== i ? null : r.n > 1 ? "elected" : "uncontested",
        parent: from,
        donor: null,
        genes: [],
      });
      if (r.winner === i) crown = address;
    }
  }
  return rows;
}

// As chart.js's `offset:true` category scale produces them.
function centers(n: number): number[] {
  return Array.from({ length: n }, (_, i) => (i + 0.5) / n);
}

const depthRows = (ys: number[]) => new Set(ys).size;

describe("dendrogram", () => {
  it("packs into exactly two depth rows while every round advances", () => {
    // Bracket r overlaps bracket r+1 but never r+2, so greedy packing alternates two rows.
    const rows = spine([
      { round: 0, n: 1, winner: 0 },
      { round: 1, n: 2, winner: 1 },
      { round: 2, n: 2, winner: 0 },
      { round: 3, n: 2, winner: 1 },
      { round: 4, n: 2, winner: 0 },
      { round: 5, n: 2, winner: 1 },
    ]);
    const g = dendrogram(rows, centers(rows.length));

    expect(g.brackets).toHaveLength(5);
    expect(depthRows(g.brackets.map((b) => b.y))).toBe(2);
    expect(g.height).toBe(FIRST_ROW_Y + ROW_H + BOTTOM_PAD);
  });

  it("hangs each candidate from the parent it is served as descending from, one beam per parent", () => {
    const rows = spine([
      { round: 0, n: 1, winner: 0 },
      { round: 1, n: 2, winner: 1 },
    ]);
    // A loser of round 1 is the measured parent of a crossover child in round 2.
    rows.push({ ...rows[2]!, key: "R2.0", round: 2, address: "r2_0", parent: "r1_0", genes: [] });
    rows.push({ ...rows[2]!, key: "R2.1", round: 2, address: "r2_1", parent: "r1_1", genes: [] });
    const g = dendrogram(rows, centers(rows.length));
    const chains = g.brackets.filter((b) => b.variant === "chain");

    expect(chains).toHaveLength(3);
    const xOf = (address: string) => g.nodes.find((n) => n.address === address)!.xf;
    for (const kid of ["r2_0", "r2_1"]) {
      const parent = rows.find((r) => r.address === kid)!.parent!;
      expect(
        chains.some((b) => b.x1f === Math.min(xOf(parent), xOf(kid)) && b.x2f === Math.max(xOf(parent), xOf(kid))),
      ).toBe(true);
    }
  });

  it("links a gene source to its child, dotted, never twice over the parent edge", () => {
    const rows = spine([
      { round: 0, n: 1, winner: 0 },
      { round: 1, n: 2, winner: 1 },
      { round: 2, n: 1, winner: 0 },
    ]);
    const child = rows.find((r) => r.address === "r2_0")!;
    child.genes = ["r1_0", "r1_1"];
    const g = dendrogram(rows, centers(rows.length));
    const genes = g.brackets.filter((b) => b.variant === "gene");

    expect(genes).toHaveLength(1);
    const xOf = (address: string) => g.nodes.find((n) => n.address === address)!.xf;
    expect([genes[0]!.x1f, genes[0]!.x2f]).toEqual([xOf("r1_0"), xOf("r2_0")]);
  });

  it("links the served donor from any earlier round, apart from the main and any gene guess", () => {
    const rows = spine([
      { round: 0, n: 1, winner: 0 },
      { round: 1, n: 2, winner: 1 },
      { round: 2, n: 1, winner: 0 },
      { round: 3, n: 1, winner: 0 },
    ]);
    const child = rows.find((r) => r.address === "r3_0")!;
    child.donor = "r0_0";
    child.genes = ["r0_0", "r1_0"];
    const g = dendrogram(rows, centers(rows.length));
    const xOf = (address: string) => g.nodes.find((n) => n.address === address)!.xf;
    const span = (variant: string) =>
      g.brackets.filter((b) => b.variant === variant).map((b) => [b.x1f, b.x2f]);

    expect(span("donor")).toEqual([[xOf("r0_0"), xOf("r3_0")]]);
    expect(span("gene")).toEqual([[xOf("r1_0"), xOf("r3_0")]]);
  });

  it("anchors every node on its own bar's category center", () => {
    const rows = spine([
      { round: 0, n: 1, winner: 0 },
      { round: 1, n: 3, winner: 2 },
    ]);
    const cs = centers(rows.length);
    const g = dendrogram(rows, cs);

    g.nodes.forEach((n, i) => {
      expect(n.i).toBe(i);
      expect(n.xf).toBe(cs[i]);
    });
  });

  it("refuses to draw when the spine and the bar categories disagree", () => {
    const rows = spine([
      { round: 0, n: 1, winner: 0 },
      { round: 1, n: 2, winner: 0 },
    ]);
    const g = dendrogram(rows, centers(rows.length - 1));

    expect(g.nodes).toEqual([]);
    expect(g.brackets).toEqual([]);
    expect(g.stubs).toEqual([]);
    expect(g.height).toBe(NODE_ROW_Y + NODE_R + BOTTOM_PAD);
  });

  it("draws no edge from a parent the strip does not hold", () => {
    const rows = spine([{ round: 1, n: 2, winner: 0 }]).map((r) => ({ ...r, parent: "elsewhere" }));
    const g = dendrogram(rows, centers(rows.length));

    expect(g.brackets).toEqual([]);
  });
});
