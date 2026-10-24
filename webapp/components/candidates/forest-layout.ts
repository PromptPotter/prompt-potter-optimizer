import type {
  ArmElection,
  CourseKind,
  CourseSpawn,
  HierarchyCampaign,
  HierarchyCandidate,
  HierarchyCourse,
  LineageDivergence,
  RoundFacts,
} from "@/lib/api";
import { childNodes, coursesOf } from "@/lib/derivations";
import { tagValue } from "@/lib/hierarchy";
import type { CyclePath } from "@/lib/ids";
import { pathOfAddress } from "@/lib/ids";

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

export const KIND_GLYPH: Record<CourseKind, string> = {
  root: "●",
  fork: "⑂",
  diag: "Δ",
  inner: "◇",
};

export const TRIGGER_GLYPH: Record<CourseSpawn["trigger"], string> = {
  operator_steered: "✎",
  operator_diag: "",
  operator_rewind: "",
  optimizer_rebase: "",
  scoring_divergence: "",
};

export const DIRECTION_GLYPH: Record<CourseSpawn["direction"], string> = {
  offshoot: "",
  supersede: "↳",
  equivalent: "≡",
};

const roundOf = (c: HierarchyCandidate): number => c.facts.reading.arm.round;
const retiredBy = (c: HierarchyCandidate): string | null => tagValue(c, "superseded");

function groupRounds(cands: readonly HierarchyCandidate[]): Map<number, HierarchyCandidate[]> {
  const byRound = new Map<number, HierarchyCandidate[]>();
  for (const c of cands) {
    const r = roundOf(c);
    const arr = byRound.get(r) ?? [];
    arr.push(c);
    byRound.set(r, arr);
  }
  return byRound;
}

function pickWinner(cands: readonly HierarchyCandidate[]): HierarchyCandidate | null {
  return cands.find((c) => c.facts.reading.election.selected) ?? null;
}

const candidatesOf = (course: HierarchyCourse): HierarchyCandidate[] =>
  childNodes(course) as HierarchyCandidate[];

const innerCourses = (cand: HierarchyCandidate): HierarchyCourse[] =>
  childNodes(cand) as HierarchyCourse[];

export function expandedLaneSpan(cands: readonly HierarchyCandidate[]): number {
  let max = 1;
  for (const n of groupRounds(cands).values()) {
    if (n.length > max) max = n.length;
  }
  return max;
}

export interface LaneLayout {
  course: HierarchyCourse;
  // The course's address, which is its `CyclePath` on the wire.
  coursePathKey: string;
  candidates: HierarchyCandidate[];
  // Each round's reference — the candidate its arms were measured against.
  references: ReadonlyMap<number, string>;
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

// The chain that PRODUCED the anchor: its course to its round, each course above cut where the next
// hangs off (a fork at its spawn candidate, an inner campaign at its spawner), the seed runs whole.
export function extentKeys(
  root: HierarchyCampaign,
  anchor: CladogramAnchor,
): ReadonlySet<string> | null {
  const keys = new Set<string>();
  const keepThrough = (course: HierarchyCourse, round: number): void => {
    for (const c of candidatesOf(course)) if (roundOf(c) <= round) keys.add(c.address);
  };
  const addMeasurements = (cand: HierarchyCandidate): void => {
    for (const course of innerCourses(cand)) {
      if (course.facts.mint !== "inner") continue;
      for (const c of candidatesOf(course)) {
        keys.add(c.address);
        addMeasurements(c);
      }
    }
  };
  const walkCampaign = (campaign: HierarchyCampaign): boolean => {
    const courses = coursesOf(campaign);
    const byAddress = new Map(courses.map((c) => [c.address, c]));
    const cutUpward = (course: HierarchyCourse): void => {
      const spawn = course.facts.spawn;
      const parent = spawn ? byAddress.get(spawn.parent) : undefined;
      if (!spawn || !parent) return;
      const from = candidatesOf(parent).find((c) => c.address === spawn.from_candidate);
      if (from) keepThrough(parent, roundOf(from));
      else if (spawn.at_round !== null) keepThrough(parent, spawn.at_round);
      cutUpward(parent);
    };
    for (const course of courses) {
      const cands = candidatesOf(course);
      const own =
        course.address === anchor.coursePathKey
          ? cands.find((c) => c.facts.individual === anchor.candidateId)
          : undefined;
      if (own) {
        keepThrough(course, roundOf(own));
        addMeasurements(own);
        cutUpward(course);
        return true;
      }
      for (const cand of cands) {
        const holds = cand.children.some((c) => c.level === "campaign" && walkCampaign(c));
        if (holds) {
          keepThrough(course, roundOf(cand));
          cutUpward(course);
          return true;
        }
      }
    }
    return false;
  };
  return walkCampaign(root) ? keys : null;
}

// Lanes key on the course address, never a cycle id: inner cycle ids repeat across `.inner/` sandboxes.
export function layout(
  root: HierarchyCampaign,
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
  const visitCourse = (
    course: HierarchyCourse,
    baseCol: number,
    anchor: string | null,
    parentKey: string | null,
  ): void => {
    const key = course.address;
    const cands = candidatesOf(course).filter((c) => keep === null || keep.has(c.address));
    if (keep !== null && cands.length === 0) return;
    const isExpanded = expanded.has(key);
    const laneSpan = isExpanded ? expandedLaneSpan(cands) : 1;
    const laneOffset = nextRow;
    nextRow += laneSpan;
    const rightmost = cands.length > 0 ? baseCol + Math.max(...cands.map(roundOf)) : baseCol;
    if (rightmost > maxCol) maxCol = rightmost;
    const references = new Map<number, string>();
    const electsOn = new Map<number, RoundFacts["elects_on"]>();
    for (const rnd of course.children) {
      if (rnd.level !== "round") continue;
      electsOn.set(rnd.facts.round, rnd.facts.elects_on);
      if (rnd.facts.reference) references.set(rnd.facts.round, rnd.facts.reference);
    }
    laneByKey.set(key, {
      course,
      coursePathKey: key,
      candidates: cands,
      references,
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
        if (inner.level === "campaign") {
          visitCampaign(inner, baseCol + roundOf(cand) + 1, cand.address, key);
        }
      }
    }
  };
  // A fork shares its parent's round axis and hangs at its spawn candidate.
  const visitCampaign = (
    campaign: HierarchyCampaign,
    baseCol: number,
    anchor: string | null,
    parentKey: string | null,
  ): void => {
    for (const course of coursesOf(campaign)) {
      const spawn = course.facts.spawn;
      if (spawn) visitCourse(course, baseCol, spawn.from_candidate, spawn.parent);
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
  candidateLabel: string;
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
    candidateLabel: label,
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
        const stand = winner ?? cands.find((c) => !retiredBy(c)) ?? cands[0];
        if (!stand) continue;
        const node = placedNode(laneKey, l, stand, colX(round), y, false, winner?.label ?? "");
        nodes.push(node);
        spineByKeyRound.set(`${laneKey}::r${round}`, node);
        if (winner) nodeByCandidate.set(winner.address, node);
        if (round === lastRound) node.isLastInLane = true;
        if (prev) {
          segs.push({ x1: prev.x, y1: prev.y, x2: node.x - d.stub, y2: node.y, variant: "chain" });
          segs.push({ x1: node.x - d.stub, y1: node.y, x2: node.x, y2: node.y, variant: "chain" });
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
      // Chained from the round's reference where it is drawn, else the last winner.
      const reference = l.references.get(round);
      const parent = (reference ? nodeByCandidate.get(reference) : undefined) ?? lastWinnerNode;
      cands.forEach((cand, i) => {
        const y = TOP_PAD + (l.laneOffset + topRow + i) * LANE_H;
        const node = placedNode(laneKey, l, cand, x, y, true, cand.label);
        nodes.push(node);
        roundNodes.push(node);
        if (!retiredBy(cand)) nodeByCandidate.set(cand.address, node);
        // The stub itself is drawn by the node group, so emit only the slant.
        if (parent) {
          segs.push({ x1: parent.x, y1: parent.y, x2: x - d.candStub, y2: y, variant: "chain" });
        }
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

  for (const [laneKey, l] of layouts) {
    if (!l.parentKey) continue;
    const parentLayout = layouts.get(l.parentKey);
    if (!parentLayout) continue;
    const anchorNode = l.anchor ? nodeByCandidate.get(l.anchor) : undefined;
    const anchorX = anchorNode?.x ?? bandLeftX(parentLayout, d);
    const anchorY = anchorNode?.y ?? bandCenterY(parentLayout);

    const childBandY = bandCenterY(l);
    // A fork shares its parent's round axis; an inner run starts a column right of its spawner.
    const minChildX = l.course.facts.spawn ? anchorX : anchorX + d.colW;
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
