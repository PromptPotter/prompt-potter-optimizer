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

// Built the way `roundCandidates` does. `winners[round]` null = a HELD round.
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

// As chart.js's `offset:true` category scale produces them.
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
    // R2 is held, so R3 fans from R1's winner; the brackets share a left edge and nest.
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
    // React has N+1 rows but the chart still has N.
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
    // Round 2 is in flight: it receives a bracket but never becomes a parent.
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
    // A fork's stamped round must not stretch that round's bracket to reach it.
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
