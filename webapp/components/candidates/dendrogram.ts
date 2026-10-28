import type { ArmElection } from "@/lib/api/types";
import type { CandidateBar } from "@/lib/types";

export const NODE_ROW_Y = 7;
export const NODE_R = 3;
export const FIRST_ROW_Y = 16;
export const ROW_H = 9;
export const BOTTOM_PAD = 4;

interface DendroNode {
  key: string;
  address: string;
  round: number;
  label: string;
  isWinner: boolean;
  crown: ArmElection["crown"];
  i: number;
  xf: number;
  y: number;
}

interface DendroStub {
  xf: number;
  y1: number;
  y2: number;
}

interface DendroBracket {
  round: number;
  x1f: number;
  x2f: number;
  y: number;
  parentKey: string;
}

interface Dendrogram {
  nodes: DendroNode[];
  stubs: DendroStub[];
  brackets: DendroBracket[];
  height: number;
}

// No live value here: the caller content-stabilizes it so a per-sample tick does not re-run packing.
export interface DendroRow {
  key: string;
  round: number;
  label: string;
  address: string;
  is_selected: boolean;
  crown: ArmElection["crown"];
}

export function roundOf(bar: CandidateBar): number {
  return bar.reading?.arm.round ?? 0;
}

export function dendroRow(bar: CandidateBar): DendroRow {
  return {
    key: bar.key,
    round: roundOf(bar),
    label: bar.label,
    address: bar.node.address,
    is_selected: bar.reading?.election.selected ?? false,
    crown: bar.reading?.election.crown ?? null,
  };
}

const FLOOR_H = NODE_ROW_Y + NODE_R + BOTTOM_PAD;

export function dendrogram(
  rows: readonly DendroRow[],
  centers: readonly number[],
): Dendrogram {
  const nodes: DendroNode[] = [];
  const stubs: DendroStub[] = [];
  const brackets: DendroBracket[] = [];

  // react-chartjs-2 updates in an effect, so rows can lead the chart's centers by a frame.
  if (rows.length === 0 || rows.length !== centers.length) {
    return { nodes, stubs, brackets, height: FLOOR_H };
  }

  rows.forEach((r, i) => {
    nodes.push({
      key: r.key,
      address: r.address,
      round: r.round,
      label: r.label,
      isWinner: r.is_selected,
      crown: r.crown,
      i,
      xf: centers[i]!,
      y: NODE_ROW_Y,
    });
  });

  const bands = new Map<number, { first: number; last: number }>();
  rows.forEach((r, i) => {
    const b = bands.get(r.round);
    if (b) b.last = i;
    else bands.set(r.round, { first: i, last: i });
  });

  // Strict `<`: a bracket touching the row's right edge would render as one merged beam.
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
