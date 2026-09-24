// Bracket-dendrogram geometry for the strip under the fitness bars. Pure numbers: x arrives and
// leaves as fractions of the chart's plot width.

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
  isWinner: boolean;
  // Display-only: a single-arm round advances without an election.
  isElected: boolean;
  // Keeps its bar slot but joins no round band: its descent is cross-cycle, which the Forest draws.
  isFork: boolean;
  // Spine index === bar category index — the alignment contract.
  i: number;
  xf: number;
  y: number;
}

export interface DendroStub {
  xf: number;
  y1: number;
  y2: number;
}

export interface DendroBracket {
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
  height: number;
}

// Kept minimal so the caller can content-stabilize it: a per-sample tick must not re-run packing.
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

  // react-chartjs-2 updates in an effect, so rows can lead the chart's centers by a frame;
  // refuse to draw rather than draw a wrong genealogy.
  if (rows.length === 0 || rows.length !== centers.length) {
    return { nodes, stubs, brackets, height: FLOOR_H };
  }

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

  // A round is a contiguous block of the spine (round asc, then idx asc).
  const bands = new Map<number, { first: number; last: number }>();
  rows.forEach((r, i) => {
    if (r.is_fork) return;
    const b = bands.get(r.round);
    if (b) b.last = i;
    else bands.set(r.round, { first: i, last: i });
  });

  // Greedy lowest-free-row is optimal because rounds arrive in left-edge order. Strict `<`: a
  // bracket merely touching the row's right edge would render as one merged beam.
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

  // The winner of the last ADVANCING round: a held round crowns nobody, so the next fans from here.
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
