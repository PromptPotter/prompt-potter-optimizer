// Cladogram geometry for the one tree `/tree` serves: lanes, node and branch coordinates. No React.

import type { LineageDivergence, LineageNode } from "@/lib/api";
import { candidatesOf, nodeKeyOf, pathOf, wasElected } from "@/lib/derivations";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";

// Horizontal only: a surface that must fit narrower drops the text and closes the columns — never
// a scaled `viewBox`. Rows stay click-target height either way.
export interface Density {
  colW: number;        // width per round-column
  leftPad: number;     // left margin before column 0
  rightPad: number;    // room past the rightmost node
  stub: number;        // horizontal stub before a collapsed round node
  candStub: number;    // horizontal stub before an expanded candidate node
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

export const LANE_H = 26;          // height per lane-row
export const HEADER_H = 18;        // column-header row at the top
export const TOP_PAD = HEADER_H + 8; // first lane sits below the header row
export const NODE_R = 3.5;         // round-node circle radius

// `inner` is an L4 seed run — a course filed under a candidate rather than cut beside one.
export type CourseKind = NonNullable<LineageNode["course_kind"]>;

export const KIND_GLYPH: Record<CourseKind, string> = {
  root: "●",
  fork: "⑂",
  diag: "Δ",
  inner: "◇",
};

export const TRIGGER_GLYPH: Record<string, string> = {
  operator_steered: "✎",
};

// Served (`FORK_DIRECTION`, derived server-side from the trigger) — never re-derived here.
export type ForkDirection = NonNullable<LineageNode["fork_direction"]>;

// "↳" = this branch IS the line now; "≡" = the cut changed nothing measurable. Offshoot is unmarked.
export const DIRECTION_GLYPH: Record<ForkDirection, string> = {
  offshoot: "",
  supersede: "↳",
  equivalent: "≡",
};

function groupRounds(cands: readonly LineageNode[]): Map<number, LineageNode[]> {
  const byRound = new Map<number, LineageNode[]>();
  for (const c of cands) {
    const r = c.round ?? 0;
    const arr = byRound.get(r) ?? [];
    arr.push(c);
    byRound.set(r, arr);
  }
  return byRound;
}

// NO first-candidate fallback: a held or never-closed round crowned nobody.
function pickWinner(cands: readonly LineageNode[]): LineageNode | null {
  return cands.find((c) => c.is_winner) ?? null;
}

export function expandedLaneSpan(cands: readonly LineageNode[]): number {
  let max = 1;
  for (const n of groupRounds(cands).values()) {
    if (n.length > max) max = n.length;
  }
  return max;
}

export interface LaneLayout {
  course: LineageNode;
  coursePathKey: string;
  candidates: LineageNode[];
  expanded: boolean;
  // In LANE_H units.
  laneOffset: number;
  laneSpan: number;
  baseCol: number;
  // Both null only at the tree's root.
  anchorCandidateId: string | null;
  parentKey: string | null;
}

// Both halves needed: a candidate id repeats across `.inner/` sandboxes, a course path holds many.
export interface CladogramAnchor {
  coursePathKey: string;
  candidateId: string;
}

// A point's extent (`webapp/CLAUDE.md` § Component conventions) as `nodeKeyOf` addresses.
// `null` where this tree does not hold the anchor.
export function extentKeys(
  root: LineageNode,
  anchor: CladogramAnchor,
): ReadonlySet<string> | null {
  const keys = new Set<string>();
  const addMeasurements = (cand: LineageNode): void => {
    for (const child of cand.children) {
      if (child.kind !== "course" || child.course_kind !== "inner") continue;
      for (const c of candidatesOf(child)) {
        keys.add(nodeKeyOf(c));
        addMeasurements(c);
      }
    }
  };
  const walk = (course: LineageNode): boolean => {
    const cands = candidatesOf(course);
    const keepThrough = (round: number): void => {
      for (const c of cands) if ((c.round ?? 0) <= round) keys.add(nodeKeyOf(c));
    };
    const own =
      encodeCyclePath(pathOf(course)) === anchor.coursePathKey
        ? cands.find((c) => c.id === anchor.candidateId)
        : undefined;
    if (own) {
      keepThrough(own.round ?? 0);
      addMeasurements(own);
      return true;
    }
    for (const cand of cands) {
      for (const child of cand.children) {
        if (child.kind === "course" && walk(child)) {
          keepThrough(cand.round ?? 0);
          return true;
        }
      }
    }
    return false;
  };
  return walk(root) ? keys : null;
}

// Lanes key on `nodeKeyOf`, never `course.id`: inner cycle ids repeat across `.inner/` sandboxes.
// `keep` (`extentKeys`) restricts the layout; `null` lays out the whole family.
export function layout(
  root: LineageNode,
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
    course: LineageNode,
    baseCol: number,
    anchorCandidateId: string | null,
    parentKey: string | null,
  ): void => {
    const key = nodeKeyOf(course);
    const cands = candidatesOf(course).filter((c) => keep === null || keep.has(nodeKeyOf(c)));
    if (keep !== null && cands.length === 0) return;
    const isExpanded = expanded.has(key);
    const laneSpan = isExpanded ? expandedLaneSpan(cands) : 1;
    const laneOffset = nextRow;
    nextRow += laneSpan;
    const rightmost =
      cands.length > 0 ? baseCol + Math.max(...cands.map((c) => c.round ?? 0)) : baseCol;
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
      for (const child of cand.children) {
        if (child.kind === "course") {
          visit(child, baseCol + (cand.round ?? 0) + 1, cand.id, key);
        }
      }
    }
  };
  visit(root, 0, null, null);
  return { laneByKey, totalLaneRows: nextRow, maxCol };
}

export interface RoundNodePos {
  courseKey: string;
  // Selection and navigation ride this, never a bare cycle id.
  coursePath: CyclePath;
  coursePathKey: string;
  round: number;
  col: number;
  x: number;
  y: number;
  // "" for a collapsed round that elected nobody. Display only — a renumbered timeline position.
  candidateLabel: string;
  // The served node this was placed from — ask it about the searchpoint, never the placed dot.
  node: LineageNode;
  candKey: string;
  candidateId: string;
  isWinner: boolean;
  // Display-only: a single-arm round advances without an election.
  isElected: boolean;
  isExpanded: boolean;
  isLastInLane: boolean;
  courseKind: CourseKind;
  trigger: string;
  forkDirection: ForkDirection | null;
  divergence: LineageDivergence | null;
  divergent: boolean;
  // What the run actually did, unlike `divergent` (a counterfactual under an applied lens).
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
  cand: LineageNode,
  x: number,
  y: number,
  isExpanded: boolean,
  label: string,
  roundSize: number,
): RoundNodePos {
  return {
    courseKey: laneKey,
    coursePath: pathOf(l.course),
    coursePathKey: l.coursePathKey,
    round: cand.round ?? 0,
    col: l.baseCol + (cand.round ?? 0),
    x,
    y,
    candidateLabel: label,
    // A collapsed band passes the winner's label while standing on `stand`; this names the latter.
    node: cand,
    // Not lane key + id: a fork-contributed candidate carries the fork's path.
    candKey: nodeKeyOf(cand),
    candidateId: cand.id,
    isWinner: cand.is_winner,
    isElected: wasElected(cand.is_winner, roundSize),
    isExpanded,
    isLastInLane: false,
    courseKind: l.course.course_kind ?? "root",
    trigger: l.course.trigger,
    forkDirection: l.course.fork_direction ?? null,
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
  // A repair re-measures without re-minting, so one id names two nodes; the RETIRED one is skipped.
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
        // A retired candidate is passed over, or the band plots the abandoned line.
        const stand = winner ?? cands.find((c) => !c.superseded_by) ?? cands[0];
        if (!stand) continue;
        const node = placedNode(
          laneKey,
          l,
          stand,
          colX(round),
          y,
          false,
          winner?.label ?? "",
          cands.length,
        );
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

    // Each round fans from the last WINNING round's winner; a held round never becomes a parent.
    let parent: { x: number; y: number } | null = null;
    let lastWinnerNode: RoundNodePos | null = null;

    for (const [round, cands] of rounds) {
      if (cands.length === 0) continue;
      const topRow = (l.laneSpan - cands.length) / 2;
      const x = colX(round);
      const roundNodes: RoundNodePos[] = [];
      cands.forEach((cand, i) => {
        const y = TOP_PAD + (l.laneOffset + topRow + i) * LANE_H;
        const node = placedNode(laneKey, l, cand, x, y, true, cand.label, cands.length);
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
      ? Math.min(...l.candidates.map((c) => c.round ?? 0))
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
