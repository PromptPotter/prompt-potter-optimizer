// Cladogram geometry for the served lineage tree. Pure layout: walks the ONE tree
// `/tree` serves, assigns lanes, and resolves SVG node + branch coordinates. No
// React — CandidatesCard and Forest own the rendering.
//
// The tree alternates `course → candidate → (course | sample)` at every depth, so
// this is one recursion rather than a per-tier rule: a fork and an L4 inner run are
// the same edge (a course hanging off a candidate), and L5+ needs nothing new.
//
// Two depths per course lane:
//   collapsed — one summary node per round (the round winner), the compact
//               course view. laneSpan = 1 row.
//   expanded  — the full intra-course candidate cladogram: one node per candidate
//               per round, winner→children branches. laneSpan = the widest round's
//               candidate count, so an expanded lane pushes the lanes below it down.
//
// **There is no `round_column_offset` and no client-side tree build.** A course
// hangs off a candidate, so its columns start one right of THAT candidate — the
// tree's own shape answers it. The server used to compute the offset precisely so
// the client wouldn't need fork-lane semantics; now nobody needs them.

import type { LineageDivergence, LineageNode } from "@/lib/api";

// Cladogram dimensions. Each round is one column; each course gets its own
// horizontal lane (one row collapsed, N rows expanded).
export const COL_W = 110;          // width per round-column
export const LANE_H = 26;          // height per lane-row
const STUB = 14;            // horizontal stub before a collapsed round node
export const CAND_STUB = 22;       // horizontal stub before an expanded candidate node
// Left margin before column 0 — room for round 1's left-anchored stub + label.
export const LEFT_PAD = 48;
export const HEADER_H = 18;        // column-header row at the top
export const TOP_PAD = HEADER_H + 8; // first lane sits below the header row
export const RIGHT_PAD = 80;       // room for the rightmost label
export const NODE_R = 3.5;         // round-node circle radius

// A course's kind, as the tree serves it. `inner` is an L4 seed run — a course
// filed under a candidate rather than cut beside one.
export type CourseKind = NonNullable<LineageNode["course_kind"]>;

export const KIND_GLYPH: Record<CourseKind, string> = {
  root: "●",
  fork: "⑂",
  sweep: "~",
  diag: "Δ",
  inner: "◇",
};

// Operator-fork provenance mark, appended after the kind glyph. "✎" = the
// operator steered the searchpoint (operator_steered). Everything else
// (auto/divergence/sweep) is unmarked.
export const TRIGGER_GLYPH: Record<string, string> = {
  operator_steered: "✎",
};

// One course's candidate children, in served (round) order.
export function candidatesOf(course: LineageNode): LineageNode[] {
  return course.children.filter((c) => c.kind === "candidate");
}

// The courses hanging off a candidate — a fork cut from it, an L4 inner run filed under
// it. The tree makes those the same edge, so callers take one list and never branch on
// which kind of thing produced it.
export function childCourses(candidate: LineageNode | undefined): LineageNode[] {
  return (candidate?.children ?? []).filter((c) => c.kind === "course");
}

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

// The round's elected winner, or null when it crowned nobody. NO first-candidate
// fallback: a held round has no winner, and a round that never CLOSED has no
// election at all — crowning either one's lone candidate is the exact misdraw this
// removes.
function pickWinner(cands: readonly LineageNode[]): LineageNode | null {
  return cands.find((c) => c.is_winner) ?? null;
}

// Rows an expanded lane occupies — the widest round's candidate count, floored at 1
// so a single-candidate lane still has a row.
export function expandedLaneSpan(cands: readonly LineageNode[]): number {
  let max = 1;
  for (const n of groupRounds(cands).values()) {
    if (n.length > max) max = n.length;
  }
  return max;
}

// Layout pass: assign each course a lane band (a starting row `laneOffset` and a
// height `laneSpan` in LANE_H units) via DFS so a parent and its child subtree stay
// visually grouped. An expanded course reserves `laneSpan` rows; every lane after it
// shifts down.
export interface LaneLayout {
  course: LineageNode;
  candidates: LineageNode[];
  expanded: boolean;
  // Starting row (in LANE_H units) of this course's band, and its height.
  laneOffset: number;
  laneSpan: number;
  // Column of this course's round 0 — one right of the candidate it hangs off.
  baseCol: number;
  // The candidate this course descends from, and the course that candidate sits on.
  // Both null only at the tree's root.
  anchorCandidateId: string | null;
  parentCycleId: string | null;
}

export function layout(
  root: LineageNode,
  expanded: ReadonlySet<string>,
): {
  laneByCycle: Map<string, LaneLayout>;
  totalLaneRows: number;
  maxCol: number;
} {
  const laneByCycle = new Map<string, LaneLayout>();
  let nextRow = 0;
  let maxCol = 0;
  const visit = (
    course: LineageNode,
    baseCol: number,
    anchorCandidateId: string | null,
    parentCycleId: string | null,
  ): void => {
    const cands = candidatesOf(course);
    const isExpanded = expanded.has(course.id);
    const laneSpan = isExpanded ? expandedLaneSpan(cands) : 1;
    const laneOffset = nextRow;
    nextRow += laneSpan;
    // Rightmost column: the course's own last round. An empty course still occupies
    // its base column, so its stub sits RIGHT of the anchor rather than left of it.
    const rightmost =
      cands.length > 0 ? baseCol + Math.max(...cands.map((c) => c.round ?? 0)) : baseCol;
    if (rightmost > maxCol) maxCol = rightmost;
    laneByCycle.set(course.id, {
      course,
      candidates: cands,
      expanded: isExpanded,
      laneOffset,
      laneSpan,
      baseCol,
      anchorCandidateId,
      parentCycleId,
    });
    // Depth-first per child so a course's whole subtree stays contiguous. The child's
    // columns start one right of the candidate it hangs off — that IS the offset.
    for (const cand of cands) {
      for (const child of cand.children) {
        if (child.kind === "course") {
          visit(child, baseCol + (cand.round ?? 0) + 1, cand.id, course.id);
        }
      }
    }
  };
  visit(root, 0, null, null);
  return { laneByCycle, totalLaneRows: nextRow, maxCol };
}

// Compute SVG (x, y) for every node (collapsed = one summary node per round;
// expanded = one node per candidate per round) and the branch segments between them.
export interface RoundNodePos {
  cycleId: string;
  round: number;
  col: number;
  x: number;
  y: number;
  // Short candidate label ("C1.2") for expanded nodes; "" for a collapsed round that
  // elected nobody (Forest draws "R{n}" for collapsed nodes).
  candidateLabel: string;
  // Stable id for selection routing — the MINTED candidate id, never a position.
  candidateId: string;
  isWinner: boolean;
  // Did the round CLOSE? With `isWinner` false it separates a HELD round (it ran,
  // nothing beat the incumbent) from one that never finished (no election to
  // report). Collapsing those into one false is how a never-closed round's lone
  // candidate got promoted to a fake winner.
  roundClosed: boolean;
  isExpanded: boolean;
  // Carries the lane (course) label — the last node of the course's last round.
  isLastInLane: boolean;
  courseKind: CourseKind;
  // Fork creation trigger — drives the operator_steered provenance glyph.
  trigger: string;
  // The counterfactual, carried by the node itself rather than re-joined from a
  // parallel array by a hand-rolled `{cycle}::r{round}` key.
  divergence: LineageDivergence | null;
  divergent: boolean;
}
interface BranchSeg {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  variant: "chain" | "fork";
}

// Band-center y for a lane (the row the collapsed node sits on, and the y the
// fork stems fall back to).
function bandCenterY(l: LaneLayout): number {
  return TOP_PAD + (l.laneOffset + (l.laneSpan - 1) / 2) * LANE_H;
}

// Left x of a lane's column band — what the band-left fork fallback anchors to.
function bandLeftX(l: LaneLayout): number {
  return LEFT_PAD + l.baseCol * COL_W;
}

function nodeAt(
  l: LaneLayout,
  cand: LineageNode,
  x: number,
  y: number,
  isExpanded: boolean,
  label: string,
): RoundNodePos {
  return {
    cycleId: l.course.id,
    round: cand.round ?? 0,
    col: l.baseCol + (cand.round ?? 0),
    x,
    y,
    candidateLabel: label,
    candidateId: cand.id,
    isWinner: cand.is_winner,
    roundClosed: cand.round_closed,
    isExpanded,
    isLastInLane: false,
    courseKind: l.course.course_kind ?? "root",
    trigger: l.course.trigger,
    divergence: cand.divergence,
    divergent: cand.divergent,
  };
}

export function placeNodes(layouts: Map<string, LaneLayout>): {
  nodes: RoundNodePos[];
  segs: BranchSeg[];
  // Per (cycle, round) winner/summary node — the fork-anchor spine.
  spineByCycleRound: Map<string, RoundNodePos>;
} {
  const nodes: RoundNodePos[] = [];
  const segs: BranchSeg[] = [];
  const spineByCycleRound = new Map<string, RoundNodePos>();
  // Candidate ids are MINTED and globally unique, so one flat map anchors a child
  // course to its parent candidate at any depth — no per-cycle key scoping.
  const nodeByCandidate = new Map<string, RoundNodePos>();

  for (const l of layouts.values()) {
    const rounds = [...groupRounds(l.candidates).entries()].sort((a, b) => a[0] - b[0]);
    const lastRound = rounds.at(-1)?.[0] ?? 0;
    const colX = (round: number): number => LEFT_PAD + (l.baseCol + round) * COL_W;

    if (!l.expanded) {
      // Collapsed: one summary node per round on the band's single row.
      const y = bandCenterY(l);
      let prev: RoundNodePos | null = null;
      for (const [round, cands] of rounds) {
        const winner = pickWinner(cands);
        // The round's stand-in when it elected nobody: the first candidate carries
        // the position, but never the crown — `isWinner` rides the real fact.
        const stand = winner ?? cands[0];
        if (!stand) continue;
        const node = nodeAt(l, stand, colX(round), y, false, winner?.label ?? "");
        nodes.push(node);
        spineByCycleRound.set(`${l.course.id}::r${round}`, node);
        if (winner) nodeByCandidate.set(winner.id, node);
        if (round === lastRound) node.isLastInLane = true;
        if (prev) {
          segs.push({ x1: prev.x, y1: prev.y, x2: node.x - STUB, y2: node.y, variant: "chain" });
          segs.push({ x1: node.x - STUB, y1: node.y, x2: node.x, y2: node.y, variant: "chain" });
        }
        prev = node;
      }
      continue;
    }

    // Expanded: the full intra-course candidate cladogram. The first round has no
    // parent node — its candidates draw only their own stub; each later round chains
    // from the last WINNING round's winner. A held round advances nothing: its
    // candidates still fan from the retained incumbent, and the incumbent stays the
    // parent of the following round — a held round never becomes a parent.
    let parent: { x: number; y: number } | null = null;
    let lastWinnerNode: RoundNodePos | null = null;

    for (const [round, cands] of rounds) {
      if (cands.length === 0) continue;
      const topRow = (l.laneSpan - cands.length) / 2;
      const x = colX(round);
      const roundNodes: RoundNodePos[] = [];
      cands.forEach((cand, i) => {
        const y = TOP_PAD + (l.laneOffset + topRow + i) * LANE_H;
        const node = nodeAt(l, cand, x, y, true, cand.label);
        nodes.push(node);
        roundNodes.push(node);
        nodeByCandidate.set(cand.id, node);
        // Parent winner → this child's stub start. The stub itself (and the label) is
        // drawn by the node group in lineage style, so emit only the slant here.
        if (parent) {
          segs.push({ x1: parent.x, y1: parent.y, x2: x - CAND_STUB, y2: y, variant: "chain" });
        }
      });
      const winner = pickWinner(cands);
      const winnerNode = winner ? roundNodes.find((n) => n.candidateId === winner.id) : undefined;
      if (winnerNode) {
        // Advancing round: this winner becomes the spine node and the parent of the
        // next round's fan.
        spineByCycleRound.set(`${l.course.id}::r${round}`, winnerNode);
        if (round === lastRound) winnerNode.isLastInLane = true;
        parent = { x: winnerNode.x, y: winnerNode.y };
        lastWinnerNode = winnerNode;
      } else if (lastWinnerNode) {
        // Held (or never-closed) round: no new winner. Leave `parent` on the last real
        // winner so the next round fans from it — this round contributes no spine node.
        spineByCycleRound.set(`${l.course.id}::r${round}`, lastWinnerNode);
        if (round === lastRound) lastWinnerNode.isLastInLane = true;
      }
    }
  }

  // Course stems — the candidate a course hangs off → that course's lane. The tree
  // names the anchor outright, so there is no `fork_from_round` semantics to
  // re-derive: resolve the candidate's node, else fall back to the parent lane's
  // band. Child stub never goes LEFT of its anchor: clamp childX to (anchorX + COL_W).
  for (const l of layouts.values()) {
    if (!l.parentCycleId) continue;
    const parentLayout = layouts.get(l.parentCycleId);
    if (!parentLayout) continue;
    const anchorNode = l.anchorCandidateId
      ? nodeByCandidate.get(l.anchorCandidateId)
      : undefined;
    // A collapsed parent draws only winners, so a course cut from an eliminated
    // candidate has no node to hang on — anchor it to the parent's band instead.
    const anchorX = anchorNode?.x ?? bandLeftX(parentLayout);
    const anchorY = anchorNode?.y ?? bandCenterY(parentLayout);

    const childBandY = bandCenterY(l);
    const minChildX = anchorX + COL_W;
    // Anchor the child stem at its first node; an empty course at the clamped
    // minimum so the stem always slants rightward.
    const firstRound = l.candidates.length > 0
      ? Math.min(...l.candidates.map((c) => c.round ?? 0))
      : null;
    let childX = minChildX;
    if (firstRound != null) {
      const firstNode = spineByCycleRound.get(`${l.course.id}::r${firstRound}`);
      if (firstNode) childX = firstNode.x;
    }
    if (childX < minChildX) childX = minChildX;
    segs.push({ x1: anchorX, y1: anchorY, x2: childX - STUB, y2: childBandY, variant: "fork" });
    segs.push({ x1: childX - STUB, y1: childBandY, x2: childX, y2: childBandY, variant: "fork" });
  }

  return { nodes, segs, spineByCycleRound };
}

// Courses below this one in the tree — the "+N more" count on a collapsed root.
export function countDescendants(root: LineageNode): number {
  let n = 0;
  const visit = (node: LineageNode): void => {
    for (const child of node.children) {
      if (child.kind === "course") n += 1;
      visit(child);
    }
  };
  visit(root);
  return n;
}
