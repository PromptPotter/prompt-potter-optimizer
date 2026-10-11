import type { ArmElection, ArmNode, CourseNode, LineageDivergence } from "@/lib/api";
import { nodeKeyOf, pathOf } from "@/lib/derivations";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";

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

export const LANE_H = 26;
export const HEADER_H = 18;
export const TOP_PAD = HEADER_H + 8;
export const NODE_R = 3.5;

export type CourseKind = CourseNode["course_kind"];

export const KIND_GLYPH: Record<CourseKind, string> = {
  root: "●",
  fork: "⑂",
  diag: "Δ",
  inner: "◇",
};

export const TRIGGER_GLYPH: Record<string, string> = {
  operator_steered: "✎",
};

export type ForkDirection = NonNullable<CourseNode["fork_direction"]>;

export const DIRECTION_GLYPH: Record<ForkDirection, string> = {
  offshoot: "",
  supersede: "↳",
  equivalent: "≡",
};

const roundOf = (c: ArmNode): number => c.reading.arm.round;

function groupRounds(cands: readonly ArmNode[]): Map<number, ArmNode[]> {
  const byRound = new Map<number, ArmNode[]>();
  for (const c of cands) {
    const r = roundOf(c);
    const arr = byRound.get(r) ?? [];
    arr.push(c);
    byRound.set(r, arr);
  }
  return byRound;
}

function pickWinner(cands: readonly ArmNode[]): ArmNode | null {
  return cands.find((c) => c.reading.election.selected) ?? null;
}

export function expandedLaneSpan(cands: readonly ArmNode[]): number {
  let max = 1;
  for (const n of groupRounds(cands).values()) {
    if (n.length > max) max = n.length;
  }
  return max;
}

export interface LaneLayout {
  course: CourseNode;
  coursePathKey: string;
  candidates: ArmNode[];
  expanded: boolean;
  // In LANE_H units.
  laneOffset: number;
  laneSpan: number;
  baseCol: number;
  anchorCandidateId: string | null;
  parentKey: string | null;
}

export interface CladogramAnchor {
  coursePathKey: string;
  candidateId: string;
}

export function extentKeys(
  root: CourseNode,
  anchor: CladogramAnchor,
): ReadonlySet<string> | null {
  const keys = new Set<string>();
  const addMeasurements = (cand: ArmNode): void => {
    for (const child of cand.children) {
      if (child.course_kind !== "inner") continue;
      for (const c of child.children) {
        keys.add(nodeKeyOf(c));
        addMeasurements(c);
      }
    }
  };
  const walk = (course: CourseNode): boolean => {
    const cands = course.children;
    const keepThrough = (round: number): void => {
      for (const c of cands) if (roundOf(c) <= round) keys.add(nodeKeyOf(c));
    };
    const own =
      encodeCyclePath(pathOf(course)) === anchor.coursePathKey
        ? cands.find((c) => c.id === anchor.candidateId)
        : undefined;
    if (own) {
      keepThrough(roundOf(own));
      addMeasurements(own);
      return true;
    }
    for (const cand of cands) {
      if (cand.children.some(walk)) {
        keepThrough(roundOf(cand));
        return true;
      }
    }
    return false;
  };
  return walk(root) ? keys : null;
}

// Lanes key on `nodeKeyOf`, never `course.id`: inner cycle ids repeat across `.inner/` sandboxes.
export function layout(
  root: CourseNode,
  expanded: ReadonlySet<string>,
  keep: ReadonlySet<string> | null = null,
): {
  laneByKey: Map<string, LaneLayout>;
  totalLaneRows: number;
  maxCol: number;
} {
  const laneByKey = new Map<string, LaneLayout>();
  let nextRow = 0;
  let maxCol = 0;
  const visit = (
    course: CourseNode,
    baseCol: number,
    anchorCandidateId: string | null,
    parentKey: string | null,
  ): void => {
    const key = nodeKeyOf(course);
    const cands = course.children.filter((c) => keep === null || keep.has(nodeKeyOf(c)));
    if (keep !== null && cands.length === 0) return;
    const isExpanded = expanded.has(key);
    const laneSpan = isExpanded ? expandedLaneSpan(cands) : 1;
    const laneOffset = nextRow;
    nextRow += laneSpan;
    const rightmost =
      cands.length > 0 ? baseCol + Math.max(...cands.map(roundOf)) : baseCol;
    if (rightmost > maxCol) maxCol = rightmost;
    laneByKey.set(key, {
      course,
      coursePathKey: encodeCyclePath(pathOf(course)),
      candidates: cands,
      expanded: isExpanded,
      laneOffset,
      laneSpan,
      baseCol,
      anchorCandidateId,
      parentKey,
    });
    for (const cand of cands) {
      for (const child of cand.children) visit(child, baseCol + roundOf(cand) + 1, cand.id, key);
    }
  };
  visit(root, 0, null, null);
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
  candidateLabel: string;
  node: ArmNode;
  candKey: string;
  candidateId: string;
  isWinner: boolean;
  crown: ArmElection["crown"];
  isExpanded: boolean;
  isLastInLane: boolean;
  courseKind: CourseKind;
  trigger: string;
  forkDirection: ForkDirection | null;
  divergence: LineageDivergence | null;
  divergent: boolean;
  retiredBy: string | null;
}
interface BranchSeg {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  variant: "chain" | "fork";
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
  cand: ArmNode,
  x: number,
  y: number,
  isExpanded: boolean,
  label: string,
): RoundNodePos {
  const { round } = cand.reading.arm;
  const { selected, crown } = cand.reading.election;
  return {
    courseKey: laneKey,
    coursePath: pathOf(l.course),
    coursePathKey: l.coursePathKey,
    round,
    col: l.baseCol + round,
    x,
    y,
    candidateLabel: label,
    node: cand,
    candKey: nodeKeyOf(cand),
    candidateId: cand.id,
    isWinner: selected,
    crown,
    isExpanded,
    isLastInLane: false,
    courseKind: l.course.course_kind,
    trigger: l.course.trigger,
    forkDirection: l.course.fork_direction,
    divergence: cand.divergence,
    divergent: cand.divergent,
    retiredBy: cand.superseded_by,
  };
}

export function placeNodes(layouts: Map<string, LaneLayout>, d: Density): {
  nodes: RoundNodePos[];
  segs: BranchSeg[];
  spineByKeyRound: Map<string, RoundNodePos>;
} {
  const nodes: RoundNodePos[] = [];
  const segs: BranchSeg[] = [];
  const spineByKeyRound = new Map<string, RoundNodePos>();
  const nodeByCandidate = new Map<string, RoundNodePos>();

  for (const [laneKey, l] of layouts) {
    const rounds = [...groupRounds(l.candidates).entries()].sort((a, b) => a[0] - b[0]);
    const lastRound = rounds.at(-1)?.[0] ?? 0;
    const colX = (round: number): number => d.leftPad + (l.baseCol + round) * d.colW;

    if (!l.expanded) {
      const y = bandCenterY(l);
      let prev: RoundNodePos | null = null;
      for (const [round, cands] of rounds) {
        const winner = pickWinner(cands);
        const stand = winner ?? cands.find((c) => !c.superseded_by) ?? cands[0];
        if (!stand) continue;
        const node = placedNode(laneKey, l, stand, colX(round), y, false, winner?.label ?? "");
        nodes.push(node);
        spineByKeyRound.set(`${laneKey}::r${round}`, node);
        if (winner) nodeByCandidate.set(winner.id, node);
        if (round === lastRound) node.isLastInLane = true;
        if (prev) {
          segs.push({ x1: prev.x, y1: prev.y, x2: node.x - d.stub, y2: node.y, variant: "chain" });
          segs.push({ x1: node.x - d.stub, y1: node.y, x2: node.x, y2: node.y, variant: "chain" });
        }
        prev = node;
      }
      continue;
    }

    let parent: { x: number; y: number } | null = null;
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
        if (!cand.superseded_by) nodeByCandidate.set(cand.id, node);
        // The stub itself is drawn by the node group, so emit only the slant.
        if (parent) {
          segs.push({ x1: parent.x, y1: parent.y, x2: x - d.candStub, y2: y, variant: "chain" });
        }
      });
      const winner = pickWinner(cands);
      const winnerNode = winner ? roundNodes.find((n) => n.candidateId === winner.id) : undefined;
      if (winnerNode) {
        spineByKeyRound.set(`${laneKey}::r${round}`, winnerNode);
        if (round === lastRound) winnerNode.isLastInLane = true;
        parent = { x: winnerNode.x, y: winnerNode.y };
        lastWinnerNode = winnerNode;
      } else if (lastWinnerNode) {
        spineByKeyRound.set(`${laneKey}::r${round}`, lastWinnerNode);
        if (round === lastRound) lastWinnerNode.isLastInLane = true;
      }
    }
  }

  for (const [laneKey, l] of layouts) {
    if (!l.parentKey) continue;
    const parentLayout = layouts.get(l.parentKey);
    if (!parentLayout) continue;
    const anchorNode = l.anchorCandidateId
      ? nodeByCandidate.get(l.anchorCandidateId)
      : undefined;
    const anchorX = anchorNode?.x ?? bandLeftX(parentLayout, d);
    const anchorY = anchorNode?.y ?? bandCenterY(parentLayout);

    const childBandY = bandCenterY(l);
    const minChildX = anchorX + d.colW;
    const firstRound = l.candidates.length > 0
      ? Math.min(...l.candidates.map(roundOf))
      : null;
    let childX = minChildX;
    if (firstRound != null) {
      const firstNode = spineByKeyRound.get(`${laneKey}::r${firstRound}`);
      if (firstNode) childX = firstNode.x;
    }
    if (childX < minChildX) childX = minChildX;
    segs.push({ x1: anchorX, y1: anchorY, x2: childX - d.stub, y2: childBandY, variant: "fork" });
    segs.push({ x1: childX - d.stub, y1: childBandY, x2: childX, y2: childBandY, variant: "fork" });
  }

  return { nodes, segs, spineByKeyRound };
}
