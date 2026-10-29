import type { ArmElection } from "@/lib/api/types";
import { descentOf } from "@/lib/derivations";
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
  parent: string | null;
  donor: string | null;
  genes: string[];
  i: number;
  xf: number;
  y: number;
}

// `chain`: a main to what descends from it. `donor`: a declared donor to the child it gave loci.
// `gene`: a source to the child whose locus, which no declared parent explains, resembles it.
type DendroVariant = "chain" | "donor" | "gene";

interface DendroStub {
  xf: number;
  y1: number;
  y2: number;
  variant: DendroVariant;
}

interface DendroBracket {
  key: string;
  x1f: number;
  x2f: number;
  y: number;
  variant: DendroVariant;
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
  parent: string | null;
  donor: string | null;
  genes: string[];
}

export function roundOf(bar: CandidateBar): number {
  return bar.reading?.arm.round ?? 0;
}

export function dendroRow(bar: CandidateBar): DendroRow {
  const { parent, donor, genes } = descentOf(bar.node);
  return {
    key: bar.key,
    round: roundOf(bar),
    label: bar.label,
    address: bar.node.address,
    is_selected: bar.reading?.election.selected ?? false,
    crown: bar.reading?.election.crown ?? null,
    parent,
    donor,
    genes: genes.map((g) => g.source),
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
      parent: r.parent,
      donor: r.donor,
      genes: r.genes,
      i,
      xf: centers[i]!,
      y: NODE_ROW_Y,
    });
  });

  // A repair re-measures without re-minting, so an address may repeat: the first bar draws it.
  const byAddress = new Map<string, DendroNode>();
  for (const n of nodes) if (!byAddress.has(n.address)) byAddress.set(n.address, n);

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

  // One beam per main and round, over the kids served as descending from it; one per donor or gene link.
  const families = new Map<string, { from: DendroNode; kids: DendroNode[]; variant: DendroVariant }>();
  const join = (key: string, from: DendroNode, kid: DendroNode, variant: DendroVariant): void => {
    const family = families.get(key);
    if (family) family.kids.push(kid);
    else families.set(key, { from, kids: [kid], variant });
  };
  for (const kid of nodes) {
    const from = kid.parent === null ? undefined : byAddress.get(kid.parent);
    if (from) join(`c|${kid.round}|${from.key}`, from, kid, "chain");
    const donor = kid.donor === null ? undefined : byAddress.get(kid.donor);
    if (donor && donor !== from) join(`d|${kid.key}|${donor.key}`, donor, kid, "donor");
    for (const address of kid.genes) {
      const source = byAddress.get(address);
      if (source && source !== from && source !== donor) {
        join(`g|${kid.key}|${source.key}`, source, kid, "gene");
      }
    }
  }
  for (const [key, { from, kids, variant }] of families) {
    const xs = [from.xf, ...kids.map((k) => k.xf)];
    const x1f = Math.min(...xs);
    const x2f = Math.max(...xs);
    const y = FIRST_ROW_Y + place(x1f, x2f) * ROW_H;
    brackets.push({ key, x1f, x2f, y, variant });
    stubs.push({ xf: from.xf, y1: NODE_ROW_Y, y2: y, variant });
    for (const k of kids) stubs.push({ xf: k.xf, y1: y, y2: NODE_ROW_Y, variant });
  }

  const depth = rowRight.length;
  return {
    nodes,
    stubs,
    brackets,
    height: depth === 0 ? FLOOR_H : FIRST_ROW_Y + (depth - 1) * ROW_H + BOTTOM_PAD,
  };
}
