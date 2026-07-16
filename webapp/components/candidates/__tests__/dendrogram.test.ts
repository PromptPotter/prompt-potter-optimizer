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

// Build a spine the way `roundCandidates` does: round asc, idx asc, one entry per
// candidate. `winners[round]` = the index that won that round, or null for a HELD
// round (nobody advanced — the round crowns no winner).
function spine(
  rounds: { round: number; n: number; winner: number | null }[],
): DendroRow[] {
  const rows: DendroRow[] = [];
  for (const r of rounds) {
    for (let i = 0; i < r.n; i++) {
      rows.push({
        key: `R${r.round}.${i}`,
        round: r.round,
        label: r.round === 0 ? "C0" : `C${r.round}.${i + 1}`,
        candidate_id: `r${r.round}_${i}`,
        is_winner: r.winner === i,
        is_fork: false,
      });
    }
  }
  return rows;
}

// Evenly-spaced category centers, as chart.js's `offset:true` category scale
// produces them: center i = (i + 0.5) / n of the plot width.
function centers(n: number): number[] {
  return Array.from({ length: n }, (_, i) => (i + 0.5) / n);
}

const depthRows = (ys: number[]) => new Set(ys).size;

describe("dendrogram", () => {
  it("packs into exactly two depth rows while every round advances", () => {
    // C0 → R1(2) → R2(2) → R3(2) → R4(2) → R5(2). Bracket r overlaps bracket r+1
    // but never bracket r+2, so greedy packing alternates two rows forever.
    const rows = spine([
      { round: 0, n: 1, winner: 0 },
      { round: 1, n: 2, winner: 1 },
      { round: 2, n: 2, winner: 0 },
      { round: 3, n: 2, winner: 1 },
      { round: 4, n: 2, winner: 0 },
      { round: 5, n: 2, winner: 1 },
    ]);
    const g = dendrogram(rows, centers(rows.length));

    expect(g.brackets).toHaveLength(5); // one per round that has a parent
    expect(depthRows(g.brackets.map((b) => b.y))).toBe(2);
    expect(g.height).toBe(FIRST_ROW_Y + ROW_H + BOTTOM_PAD);
  });

  it("hangs a held round's successors off the EARLIER winner, and grows a third row", () => {
    // R2 is held: nobody advanced. R3 must fan from R1's winner, NOT from any R2
    // candidate — crowning an eliminated candidate is the misdraw this rule
    // exists to prevent. R2's and R3's brackets then share a left edge and nest,
    // which is what forces a third depth row.
    const rows = spine([
      { round: 0, n: 1, winner: 0 },
      { round: 1, n: 2, winner: 0 },
      { round: 2, n: 2, winner: null }, // held
      { round: 3, n: 2, winner: 1 },
    ]);
    const g = dendrogram(rows, centers(rows.length));

    const r1Winner = rows.findIndex((r) => r.round === 1 && r.is_winner);
    const b3 = g.brackets.find((b) => b.round === 3);
    const b2 = g.brackets.find((b) => b.round === 2);

    expect(b3?.parentKey).toBe(rows[r1Winner]!.key);
    expect(b2?.parentKey).toBe(rows[r1Winner]!.key);
    expect(b3?.x1f).toBe(b2?.x1f); // same parent ⇒ same left edge ⇒ they nest
    expect(depthRows(g.brackets.map((b) => b.y))).toBe(3);
    expect(g.height).toBe(FIRST_ROW_Y + 2 * ROW_H + BOTTOM_PAD);
  });

  it("keeps growing depth across consecutive held rounds", () => {
    const rows = spine([
      { round: 0, n: 1, winner: 0 },
      { round: 1, n: 2, winner: 0 },
      { round: 2, n: 2, winner: null },
      { round: 3, n: 2, winner: null },
      { round: 4, n: 2, winner: null },
    ]);
    const g = dendrogram(rows, centers(rows.length));

    // R2, R3, R4 all fan from R1's winner: three nested brackets, three rows.
    const r1Winner = rows.find((r) => r.round === 1 && r.is_winner)!;
    for (const b of g.brackets.filter((x) => x.round >= 2)) {
      expect(b.parentKey).toBe(r1Winner.key);
    }
    expect(depthRows(g.brackets.map((b) => b.y))).toBe(4);
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
    // One frame where React has N+1 rows but the chart still has N. Drawing here
    // would misattribute parentage; a blank fixed-height band is correct.
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

  it("runs every edge strictly left-to-right", () => {
    const rows = spine([
      { round: 0, n: 1, winner: 0 },
      { round: 1, n: 2, winner: 1 },
      { round: 2, n: 3, winner: 0 },
      { round: 3, n: 2, winner: 1 },
    ]);
    const g = dendrogram(rows, centers(rows.length));
    const byKey = new Map(g.nodes.map((n) => [n.key, n]));

    for (const b of g.brackets) {
      expect(b.x1f).toBeLessThan(b.x2f);
      const parent = byKey.get(b.parentKey)!;
      // Every child of this bracket sits right of its parent on the spine.
      for (const kid of g.nodes.filter((n) => n.round === b.round)) {
        expect(kid.i).toBeGreaterThan(parent.i);
      }
    }
  });

  it("mints no bracket for the origin, and lets a winner-less last round hand nothing forward", () => {
    // Round 0 has no parent (there is nothing before the origin). Round 2 is the
    // in-flight round: no winner elected yet, so it receives a bracket from R1's
    // winner but never becomes a parent itself.
    const rows = spine([
      { round: 0, n: 1, winner: 0 },
      { round: 1, n: 2, winner: 0 },
      { round: 2, n: 2, winner: null },
    ]);
    const g = dendrogram(rows, centers(rows.length));

    expect(g.brackets.find((b) => b.round === 0)).toBeUndefined();
    expect(g.brackets.map((b) => b.round)).toEqual([1, 2]);
    const r1Winner = rows.find((r) => r.round === 1 && r.is_winner)!;
    expect(g.brackets.find((b) => b.round === 2)?.parentKey).toBe(r1Winner.key);
  });

  it("keeps a fork bar's slot but leaves it out of the round packing", () => {
    // A fork trails the candidate spine as a sibling COURSE: it gets a node on
    // its own bar center, joins no band (its descent is cross-cycle), and its
    // stamped round must not stretch that round's bracket to reach it.
    const rows = [
      ...spine([
        { round: 0, n: 1, winner: 0 },
        { round: 1, n: 2, winner: 0 },
      ]),
      {
        key: "fork|cy_ab",
        round: 1,
        label: "f·ab",
        candidate_id: "cy_ab",
        is_winner: false,
        is_fork: true,
      },
    ];
    const g = dendrogram(rows, centers(rows.length));

    expect(g.nodes).toHaveLength(4);
    expect(g.nodes[3]!.isFork).toBe(true);
    expect(g.nodes[3]!.xf).toBe(centers(4)[3]);
    const r1 = g.brackets.find((b) => b.round === 1)!;
    // The bracket ends at the last CANDIDATE of round 1, not at the fork bar.
    expect(r1.x2f).toBe(centers(4)[2]);
    expect(g.stubs.every((s) => s.xf !== centers(4)[3])).toBe(true);
  });
});
