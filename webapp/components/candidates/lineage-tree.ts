import type {
  ArmElection,
  CourseKind,
  CourseSpawn,
  HierarchyCandidate,
  LineageDivergence,
  RoundFacts,
  SubtreeCampaign,
  SubtreeCourse,
} from "@/lib/api/types";
import { candidatesOf, descentOf, tagValue, type Descent } from "@/lib/derivations";
import { type CyclePath, pathOfAddress } from "@/lib/ids";
import { fmtUnit } from "@/lib/format";
import type { CandidateBar } from "@/lib/types";

// The lineage tree, laid out two ways: PAIRED under the candidates bar chart, x-aligned on the bars
// whose order it is (`DendrogramStrip`), and FREED as the stand-alone forest of lanes (`Forest`).
// Both draw one derivation, `descentLinks`: what each drawn individual descends from, among the drawn.

// `chain`: a main to what descends from it. `donor`: a declared donor to the child it gave loci.
// `gene`: a source to the child whose locus, which no declared parent explains, resembles it.
type DescentVariant = "chain" | "donor" | "gene";

interface DescentLink<N> {
  from: N;
  variant: DescentVariant;
  // What a donor or gene link says on hover.
  note: string | null;
}

function descentLinks<N extends { label: string }>(
  descent: Descent,
  nodeAt: (address: string) => N | undefined,
): DescentLink<N>[] {
  const { parent, donor, donated, genes } = descent;
  const from = parent === null ? undefined : nodeAt(parent);
  const links: DescentLink<N>[] = from ? [{ from, variant: "chain", note: null }] : [];
  const giver = donor === null ? undefined : nodeAt(donor);
  if (giver && giver !== from) {
    const gave = donated.length > 0 ? donated.join(", ") : "no locus verbatim";
    links.push({ from: giver, variant: "donor", note: `donor ${giver.label} gave ${gave}` });
  }
  for (const gene of genes) {
    const source = nodeAt(gene.source);
    if (source && source !== from && source !== giver) {
      const alike = fmtUnit("rate", gene.similarity);
      const note = `${gene.locus} resembles ${source.label} (${alike} alike)`;
      links.push({ from: source, variant: "gene", note });
    }
  }
  return links;
}

// ---- PAIRED: one node per bar, on its category center (a fraction of the plot's width).

export const NODE_ROW_Y = 7;
const PAIRED_NODE_R = 3;
export const FIRST_ROW_Y = 16;
export const ROW_H = 9;
export const BOTTOM_PAD = 4;
const FLOOR_H = NODE_ROW_Y + PAIRED_NODE_R + BOTTOM_PAD;

// No live value here: the caller content-stabilizes it so a per-sample tick does not re-run packing.
export interface PairedRow {
  key: string;
  round: number;
  label: string;
  address: string;
  is_selected: boolean;
  crown: ArmElection["crown"];
  descent: Descent;
}

export function barRound(bar: CandidateBar): number {
  return bar.reading?.arm.round ?? 0;
}

export function pairedRow(bar: CandidateBar): PairedRow {
  return {
    key: bar.key,
    round: barRound(bar),
    label: bar.label,
    address: bar.node.address,
    is_selected: bar.reading?.election.selected ?? false,
    crown: bar.reading?.election.crown ?? null,
    descent: descentOf(bar.node),
  };
}

interface PairedNode extends PairedRow {
  xf: number;
  y: number;
}

interface PairedStub {
  xf: number;
  y1: number;
  y2: number;
  variant: DescentVariant;
}

interface PairedBeam {
  key: string;
  x1f: number;
  x2f: number;
  y: number;
  variant: DescentVariant;
}

interface PairedLayout {
  nodes: PairedNode[];
  stubs: PairedStub[];
  beams: PairedBeam[];
  height: number;
}

function paired(rows: readonly PairedRow[], centers: readonly number[]): PairedLayout {
  const nodes: PairedNode[] = [];
  const stubs: PairedStub[] = [];
  const beams: PairedBeam[] = [];

  // react-chartjs-2 updates in an effect, so rows can lead the chart's centers by a frame.
  if (rows.length === 0 || rows.length !== centers.length) {
    return { nodes, stubs, beams, height: FLOOR_H };
  }
  rows.forEach((r, i) => nodes.push({ ...r, xf: centers[i]!, y: NODE_ROW_Y }));

  // A repair re-measures without re-minting, so an address may repeat: the first bar draws it.
  const byAddress = new Map<string, PairedNode>();
  for (const n of nodes) if (!byAddress.has(n.address)) byAddress.set(n.address, n);

  // Strict `<`: a beam touching the row's right edge would render as one merged beam.
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
  const families = new Map<string, { from: PairedNode; kids: PairedNode[]; variant: DescentVariant }>();
  for (const kid of nodes) {
    for (const { from, variant } of descentLinks(kid.descent, (a) => byAddress.get(a))) {
      const key = variant === "chain" ? `c|${kid.round}|${from.key}` : `${variant}|${kid.key}|${from.key}`;
      const family = families.get(key);
      if (family) family.kids.push(kid);
      else families.set(key, { from, kids: [kid], variant });
    }
  }
  for (const [key, { from, kids, variant }] of families) {
    const xs = [from.xf, ...kids.map((k) => k.xf)];
    const x1f = Math.min(...xs);
    const x2f = Math.max(...xs);
    const y = FIRST_ROW_Y + place(x1f, x2f) * ROW_H;
    beams.push({ key, x1f, x2f, y, variant });
    stubs.push({ xf: from.xf, y1: NODE_ROW_Y, y2: y, variant });
    for (const k of kids) stubs.push({ xf: k.xf, y1: y, y2: NODE_ROW_Y, variant });
  }

  const depth = rowRight.length;
  return {
    nodes,
    stubs,
    beams,
    height: depth === 0 ? FLOOR_H : FIRST_ROW_Y + (depth - 1) * ROW_H + BOTTOM_PAD,
  };
}

// ---- FREED: one lane per course, one column per round, in pixels.

export interface Density {
  colW: number;
  leftPad: number;
  rightPad: number;
  stub: number;
  candStub: number;
  labels: boolean;
}

export const ROOMY: Density = {
  colW: 110,
  leftPad: 48,
  rightPad: 80,
  stub: 14,
  candStub: 22,
  labels: true,
};
export const DENSE: Density = {
  colW: 34,
  leftPad: 18,
  rightPad: 24,
  stub: 7,
  candStub: 10,
  labels: false,
};

export const forestWidth = (d: Density, maxCol: number): number =>
  d.leftPad + (maxCol + 1) * d.colW + d.rightPad;

// The labelled drawing where it fits the box, else the label-free one; DENSE until the box is measured.
export const densityFor = (maxCol: number, boxW: number | null): Density =>
  boxW !== null && forestWidth(ROOMY, maxCol) <= boxW ? ROOMY : DENSE;

export const LANE_H = 26;
export const HEADER_H = 18;
export const TOP_PAD = HEADER_H + 8;
export const NODE_R = 3.5;

export const KIND_GLYPH: Record<CourseKind, string> = {
  root: "●",
  fork: "⑂",
  inner: "◇",
};

export const TRIGGER_GLYPH: Record<CourseSpawn["trigger"], string> = {
  operator_steered: "✎",
  operator_rewind: "",
  optimizer_rebase: "",
  scoring_divergence: "",
};

export const DIRECTION_GLYPH: Record<CourseSpawn["direction"], string> = {
  offshoot: "",
  supersede: "↳",
  equivalent: "≡",
};

const candidateRound = (c: HierarchyCandidate): number => c.facts.reading.arm.round;
const retiredBy = (c: HierarchyCandidate): string | null => tagValue(c, "superseded");

function groupRounds(cands: readonly HierarchyCandidate[]): Map<number, HierarchyCandidate[]> {
  const byRound = new Map<number, HierarchyCandidate[]>();
  for (const c of cands) {
    const r = candidateRound(c);
    const arr = byRound.get(r) ?? [];
    arr.push(c);
    byRound.set(r, arr);
  }
  return byRound;
}

function pickWinner(cands: readonly HierarchyCandidate[]): HierarchyCandidate | null {
  return cands.find((c) => c.facts.reading.election.selected) ?? null;
}

const innerCourses = (cand: HierarchyCandidate): SubtreeCourse[] =>
  cand.children.flatMap((c) => c.children);

export function expandedLaneSpan(cands: readonly HierarchyCandidate[]): number {
  let max = 1;
  for (const n of groupRounds(cands).values()) {
    if (n.length > max) max = n.length;
  }
  return max;
}

export interface LaneLayout {
  course: SubtreeCourse;
  // The course's address, which is its `CyclePath` on the wire.
  coursePathKey: string;
  candidates: HierarchyCandidate[];
  electsOn: ReadonlyMap<number, RoundFacts["elects_on"]>;
  expanded: boolean;
  // In LANE_H units.
  laneOffset: number;
  laneSpan: number;
  baseCol: number;
  // The candidate address the lane hangs from: a fork's spawn candidate, an inner run's spawner.
  anchor: string | null;
  parentKey: string | null;
}

export interface CladogramAnchor {
  coursePathKey: string;
  // The individual id.
  candidateId: string;
}

// The campaigns hung inside a course: off its candidates, and off the course where none claims one.
const campaignsIn = (course: SubtreeCourse): SubtreeCampaign[] => [
  ...candidatesOf(course).flatMap((c) => c.children),
  ...course.children.filter((c) => c.level === "campaign"),
];

function pointAt(campaign: SubtreeCampaign, anchor: CladogramAnchor): HierarchyCandidate | null {
  for (const course of campaign.children) {
    if (course.address === anchor.coursePathKey) {
      const own = candidatesOf(course).find((c) => c.facts.individual === anchor.candidateId);
      if (own) return own;
    }
    for (const inner of campaignsIn(course)) {
      const found = pointAt(inner, anchor);
      if (found) return found;
    }
  }
  return null;
}

// The chain that PRODUCED the anchor is its served `main_line`, across forks and inner hosts; the
// seed runs that measured it stay whole.
export function extentKeys(
  root: SubtreeCampaign,
  anchor: CladogramAnchor,
): ReadonlySet<string> | null {
  const point = pointAt(root, anchor);
  if (point === null) return null;
  const keys = new Set([point.address, ...point.facts.main_line.flatMap((s) => s.candidates)]);
  const addMeasurements = (cand: HierarchyCandidate): void => {
    for (const course of innerCourses(cand)) {
      if (course.facts.mint !== "inner") continue;
      for (const c of candidatesOf(course)) {
        keys.add(c.address);
        addMeasurements(c);
      }
    }
  };
  addMeasurements(point);
  return keys;
}

// A lane stands while it, or a run hung inside it, holds a kept candidate.
const holdsKept = (course: SubtreeCourse, keep: ReadonlySet<string>): boolean =>
  candidatesOf(course).some((c) => keep.has(c.address)) ||
  campaignsIn(course).some((inner) => inner.children.some((c) => holdsKept(c, keep)));

// Lanes key on the course address, never a cycle id: inner cycle ids repeat across `.inner/` sandboxes.
function lanes(
  root: SubtreeCampaign,
  expanded: ReadonlySet<string>,
  keep: ReadonlySet<string> | null,
): {
  laneByKey: Map<string, LaneLayout>;
  totalLaneRows: number;
  maxCol: number;
} {
  const laneByKey = new Map<string, LaneLayout>();
  let nextRow = 0;
  let maxCol = 0;
  const visitCourse = (
    course: SubtreeCourse,
    baseCol: number,
    anchor: string | null,
    parentKey: string | null,
  ): void => {
    const key = course.address;
    if (keep !== null && !holdsKept(course, keep)) return;
    const cands = candidatesOf(course).filter((c) => keep === null || keep.has(c.address));
    const isExpanded = expanded.has(key);
    const laneSpan = isExpanded ? expandedLaneSpan(cands) : 1;
    const laneOffset = nextRow;
    nextRow += laneSpan;
    const rightmost = cands.length > 0 ? baseCol + Math.max(...cands.map(candidateRound)) : baseCol;
    if (rightmost > maxCol) maxCol = rightmost;
    const electsOn = new Map<number, RoundFacts["elects_on"]>();
    for (const rnd of course.children) {
      if (rnd.level === "round") electsOn.set(rnd.facts.round, rnd.facts.elects_on);
    }
    laneByKey.set(key, {
      course,
      coursePathKey: key,
      candidates: cands,
      electsOn,
      expanded: isExpanded,
      laneOffset,
      laneSpan,
      baseCol,
      anchor,
      parentKey,
    });
    for (const cand of cands) {
      for (const inner of cand.children) {
        visitCampaign(inner, baseCol + candidateRound(cand) + 1, cand.address, key);
      }
    }
    // An inner campaign no candidate holds hangs from the course itself, its caveat saying why.
    for (const inner of course.children) {
      if (inner.level === "campaign") visitCampaign(inner, baseCol, null, key);
    }
  };
  // A fork shares its parent's round axis and hangs at its spawn candidate.
  const visitCampaign = (
    campaign: SubtreeCampaign,
    baseCol: number,
    anchor: string | null,
    parentKey: string | null,
  ): void => {
    for (const course of campaign.children) {
      const spawn = course.facts.spawn;
      if (spawn) visitCourse(course, baseCol, course.facts.from_candidate, spawn.parent);
      else visitCourse(course, baseCol, anchor, parentKey);
    }
  };
  visitCampaign(root, 0, null, null);
  return { laneByKey, totalLaneRows: nextRow, maxCol };
}

export interface RoundNodePos {
  courseKey: string;
  coursePath: CyclePath;
  coursePathKey: string;
  round: number;
  col: number;
  x: number;
  y: number;
  // The candidate's label; a collapsed round's, its winner's or none.
  label: string;
  node: HierarchyCandidate;
  // The candidate's address.
  candKey: string;
  // Its individual id.
  candidateId: string;
  isWinner: boolean;
  crown: ArmElection["crown"];
  isExpanded: boolean;
  isLastInLane: boolean;
  courseKind: CourseKind;
  spawn: CourseSpawn | null;
  divergence: LineageDivergence | null;
  divergent: boolean;
  retiredBy: string | null;
  // The column the candidate's round elected on.
  electsOn: RoundFacts["elects_on"];
}

interface BranchSeg {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  // `fork`: a lane to the candidate or course it hangs from; else a descent link.
  variant: DescentVariant | "fork";
  note: string | null;
}

function bandCenterY(l: LaneLayout): number {
  return TOP_PAD + (l.laneOffset + (l.laneSpan - 1) / 2) * LANE_H;
}

function bandLeftX(l: LaneLayout, d: Density): number {
  return d.leftPad + l.baseCol * d.colW;
}

function placedNode(
  laneKey: string,
  l: LaneLayout,
  cand: HierarchyCandidate,
  x: number,
  y: number,
  isExpanded: boolean,
  label: string,
): RoundNodePos {
  const { round } = cand.facts.reading.arm;
  const { selected, crown } = cand.facts.reading.election;
  return {
    courseKey: laneKey,
    coursePath: pathOfAddress(l.course.address),
    coursePathKey: l.coursePathKey,
    round,
    col: l.baseCol + round,
    x,
    y,
    label,
    node: cand,
    candKey: cand.address,
    candidateId: cand.facts.individual,
    isWinner: selected,
    crown,
    isExpanded,
    isLastInLane: false,
    courseKind: l.course.facts.mint,
    spawn: l.course.facts.spawn,
    divergence: cand.facts.divergence,
    divergent: cand.facts.divergent,
    retiredBy: retiredBy(cand),
    electsOn: l.electsOn.get(round) ?? null,
  };
}

interface FreedLayout {
  laneByKey: Map<string, LaneLayout>;
  totalLaneRows: number;
  maxCol: number;
  d: Density;
  nodes: RoundNodePos[];
  segs: BranchSeg[];
  spineByKeyRound: Map<string, RoundNodePos>;
}

function freed(
  root: SubtreeCampaign,
  expanded: ReadonlySet<string>,
  keep: ReadonlySet<string> | null,
  boxW: number | null,
): FreedLayout {
  const { laneByKey, totalLaneRows, maxCol } = lanes(root, expanded, keep);
  const d = densityFor(maxCol, boxW);
  const nodes: RoundNodePos[] = [];
  const segs: BranchSeg[] = [];
  const spineByKeyRound = new Map<string, RoundNodePos>();
  const nodeByCandidate = new Map<string, RoundNodePos>();
  const seg = (x1: number, y1: number, x2: number, y2: number, variant: BranchSeg["variant"]) =>
    segs.push({ x1, y1, x2, y2, variant, note: null });

  for (const [laneKey, l] of laneByKey) {
    const rounds = [...groupRounds(l.candidates).entries()].sort((a, b) => a[0] - b[0]);
    const lastRound = rounds.at(-1)?.[0] ?? 0;
    const colX = (round: number): number => d.leftPad + (l.baseCol + round) * d.colW;

    if (!l.expanded) {
      const y = bandCenterY(l);
      let prev: RoundNodePos | null = null;
      for (const [round, cands] of rounds) {
        const winner = pickWinner(cands);
        const stand = winner ?? cands.find((c) => !retiredBy(c)) ?? cands[0];
        if (!stand) continue;
        const node = placedNode(laneKey, l, stand, colX(round), y, false, winner?.label ?? "");
        nodes.push(node);
        spineByKeyRound.set(`${laneKey}::r${round}`, node);
        // A collapsed round is drawn as one node, so every candidate of it is drawn there.
        for (const c of cands) nodeByCandidate.set(c.address, node);
        if (round === lastRound) node.isLastInLane = true;
        if (prev) {
          seg(prev.x, prev.y, node.x - d.stub, node.y, "chain");
          seg(node.x - d.stub, node.y, node.x, node.y, "chain");
        }
        prev = node;
      }
      continue;
    }

    let lastWinnerNode: RoundNodePos | null = null;

    for (const [round, cands] of rounds) {
      if (cands.length === 0) continue;
      const topRow = (l.laneSpan - cands.length) / 2;
      const x = colX(round);
      const roundNodes: RoundNodePos[] = [];
      cands.forEach((cand, i) => {
        const y = TOP_PAD + (l.laneOffset + topRow + i) * LANE_H;
        const node = placedNode(laneKey, l, cand, x, y, true, cand.label);
        nodes.push(node);
        roundNodes.push(node);
        nodeByCandidate.set(cand.address, node);
      });
      const winner = pickWinner(cands);
      const winnerNode = winner ? roundNodes.find((n) => n.candKey === winner.address) : undefined;
      if (winnerNode) {
        spineByKeyRound.set(`${laneKey}::r${round}`, winnerNode);
        if (round === lastRound) winnerNode.isLastInLane = true;
        lastWinnerNode = winnerNode;
      } else if (lastWinnerNode) {
        spineByKeyRound.set(`${laneKey}::r${round}`, lastWinnerNode);
        if (round === lastRound) lastWinnerNode.isLastInLane = true;
      }
    }
  }

  // Every node is placed before any edge, so a parent in a later lane is drawn from too. The stub
  // itself is drawn by the node group, so each edge is only the slant to it.
  for (const n of nodes) {
    if (!n.isExpanded) continue;
    for (const { from, variant, note } of descentLinks(descentOf(n.node), (a) => nodeByCandidate.get(a))) {
      segs.push({ x1: from.x, y1: from.y, x2: n.x - d.candStub, y2: n.y, variant, note });
    }
  }

  for (const [laneKey, l] of laneByKey) {
    if (!l.parentKey) continue;
    const parentLayout = laneByKey.get(l.parentKey);
    if (!parentLayout) continue;
    const anchorNode = l.anchor ? nodeByCandidate.get(l.anchor) : undefined;
    const anchorX = anchorNode?.x ?? bandLeftX(parentLayout, d);
    const anchorY = anchorNode?.y ?? bandCenterY(parentLayout);

    const childBandY = bandCenterY(l);
    // A fork shares its parent's round axis; an inner run starts a column right of its spawner.
    const minChildX = l.course.facts.spawn ? anchorX : anchorX + d.colW;
    const firstRound = l.candidates.length > 0
      ? Math.min(...l.candidates.map(candidateRound))
      : null;
    let childX = minChildX;
    if (firstRound != null) {
      const firstNode = spineByKeyRound.get(`${laneKey}::r${firstRound}`);
      if (firstNode) childX = firstNode.x;
    }
    if (childX < minChildX) childX = minChildX;
    seg(anchorX, anchorY, childX - d.stub, childBandY, "fork");
    seg(childX - d.stub, childBandY, childX, childBandY, "fork");
  }

  return { laneByKey, totalLaneRows, maxCol, d, nodes, segs, spineByKeyRound };
}

// ---- The one entry: a placement names the layout, and the layout answers in its own shape.

export type TreePlacement =
  | { paired: readonly PairedRow[]; centers: readonly number[] }
  | {
      freed: SubtreeCampaign;
      expanded: ReadonlySet<string>;
      keep: ReadonlySet<string> | null;
      boxW: number | null;
    };

type LayoutOf<P extends TreePlacement> = P extends { paired: readonly PairedRow[] }
  ? PairedLayout
  : FreedLayout;

export function layout<P extends TreePlacement>(p: P): LayoutOf<P> {
  const out = "paired" in p ? paired(p.paired, p.centers) : freed(p.freed, p.expanded, p.keep, p.boxW);
  return out as LayoutOf<P>;
}
