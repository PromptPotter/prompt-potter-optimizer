// Bracket-dendrogram geometry for the strip under the fitness bars, drawing the intra-cycle
// genealogy of the SAME flat candidate spine the bars plot.
//
// The genealogy is spine-and-fan — every candidate of round r descends from the winner of the
// last ADVANCING round before r — so in flat (round asc, idx asc) order every parent sits
// strictly LEFT of every child and a bracket is a plain x-interval: INTERVAL PACKING, not tree
// layout. Two rows suffice while every round advances, but a HELD round crowns no winner and its
// brackets NEST, so packing is greedy lowest-free-row and the height falls out — nothing
// hardcodes "2".
//
// x arrives and leaves as FRACTIONS of the chart's plot width, so this module is pure numbers
// and unit-testable without a canvas.

import { roundSizes, wasElected } from "@/lib/derivations";

export const NODE_ROW_Y = 7; // candidate dot cy, px from the strip top
export const NODE_R = 3;
export const FIRST_ROW_Y = 16; // the first bracket beam
export const ROW_H = 9; // beam-to-beam
export const BOTTOM_PAD = 4;

export interface DendroNode {
  key: string;
  candidateId: string;
  round: number;
  label: string;
  // The served incumbency fact — who the round advanced. Structural: bracket
  // parentage rides this.
  isWinner: boolean;
  // Whether that crown was won over RIVALS (`wasElected`). Display-only: a
  // single-arm round (round 0, whose one arm is the origin) advances without an
  // election, so filling its dot and saying "elected" claims what never happened.
  isElected: boolean;
  // A fork bar — a sibling COURSE trailing the candidate spine. It keeps its bar
  // slot (the alignment contract is over ALL categories) but joins no round band:
  // its descent is cross-cycle, which the Forest draws, not this strip.
  isFork: boolean;
  // Spine index === bar category index. THE alignment contract.
  i: number;
  // Category-center fraction of the plot width, 0..1.
  xf: number;
  y: number;
}

export interface DendroStub {
  xf: number;
  y1: number;
  y2: number;
}

export interface DendroBracket {
  // The CHILD round this beam feeds.
  round: number;
  x1f: number;
  x2f: number;
  y: number;
  parentKey: string;
}

export interface Dendrogram {
  nodes: DendroNode[];
  stubs: DendroStub[];
  brackets: DendroBracket[];
  // Total strip height in px — grows only with the packed depth.
  height: number;
}

// The narrow structural projection this geometry needs. Keeping it minimal is
// what lets the caller content-stabilize it, so a per-sample value tick repaints
// node text without ever re-running the packing.
export interface DendroRow {
  key: string;
  round: number;
  label: string;
  candidate_id: string;
  is_winner: boolean;
  is_fork: boolean;
}

const FLOOR_H = NODE_ROW_Y + NODE_R + BOTTOM_PAD;

export function dendrogram(
  rows: readonly DendroRow[],
  centers: readonly number[],
): Dendrogram {
  const nodes: DendroNode[] = [];
  const stubs: DendroStub[] = [];
  const brackets: DendroBracket[] = [];

  // THE ALIGNMENT INVARIANT. This strip exists to be x-aligned with the bars; one
  // drawn against a stale center list is a LIE about which candidate descends
  // from which. React can render N+1 rows a frame before the chart knows about
  // them (react-chartjs-2 updates in an effect), so refuse to draw rather than
  // draw wrong — a blank band for one frame is the correct answer.
  if (rows.length === 0 || rows.length !== centers.length) {
    return { nodes, stubs, brackets, height: FLOOR_H };
  }

  // Arms per round, forks excluded — they trail the spine and join no band, so they
  // are not rivals. Feeds `isElected`: a crown is only an achievement against rivals.
  const sizes = roundSizes(rows.filter((r) => !r.is_fork));

  rows.forEach((r, i) => {
    nodes.push({
      key: r.key,
      candidateId: r.candidate_id,
      round: r.round,
      label: r.label,
      isWinner: r.is_winner,
      isElected: wasElected(r.is_winner, sizes.get(r.round) ?? 1),
      isFork: r.is_fork,
      i,
      xf: centers[i]!,
      y: NODE_ROW_Y,
    });
  });

  // A round is a CONTIGUOUS block in the spine (round asc, then idx asc). Fork
  // rows trail the candidate spine and join no band, so contiguity holds.
  const bands = new Map<number, { first: number; last: number }>();
  rows.forEach((r, i) => {
    if (r.is_fork) return;
    const b = bands.get(r.round);
    if (b) b.last = i;
    else bands.set(r.round, { first: i, last: i });
  });

  // Greedy packing. `rowRight[d]` = right edge of the last bracket placed on
  // depth row d. Rounds are walked in ascending order, which IS left-edge order
  // (the parent index never decreases), so "the lowest row that ends before this
  // one starts" is optimal. Strict `<`: a bracket whose left edge merely touches
  // the row's right edge would render as one merged beam, so it gets its own row.
  const rowRight: number[] = [];
  const place = (x1f: number, x2f: number): number => {
    for (let d = 0; d < rowRight.length; d++) {
      if (rowRight[d]! < x1f) {
        rowRight[d] = x2f;
        return d;
      }
    }
    rowRight.push(x2f);
    return rowRight.length - 1;
  };

  // The incumbent — the winner of the last ADVANCING round. A held round crowns
  // nobody and so never becomes a parent: the next round still fans from this
  // same node. `is_winner` already carries the held-round fact (a held round has
  // no winner), so the flat spine is the only input this needs.
  let parent: DendroNode | null = null;

  for (const round of [...bands.keys()].sort((a, b) => a - b)) {
    const band = bands.get(round)!;
    const kids = nodes.slice(band.first, band.last + 1);
    const lastKid = kids[kids.length - 1];
    if (!lastKid) continue;

    if (parent) {
      const d = place(parent.xf, lastKid.xf);
      const y = FIRST_ROW_Y + d * ROW_H;
      brackets.push({ round, x1f: parent.xf, x2f: lastKid.xf, y, parentKey: parent.key });
      stubs.push({ xf: parent.xf, y1: NODE_ROW_Y, y2: y });
      for (const k of kids) stubs.push({ xf: k.xf, y1: y, y2: NODE_ROW_Y });
    }

    const winner = kids.find((n) => n.isWinner);
    if (winner) parent = winner;
  }

  const depth = rowRight.length;
  return {
    nodes,
    stubs,
    brackets,
    height: depth === 0 ? FLOOR_H : FIRST_ROW_Y + (depth - 1) * ROW_H + BOTTOM_PAD,
  };
}
