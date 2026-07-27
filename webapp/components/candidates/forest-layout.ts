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
// No client-side tree build: a course hangs off a candidate, so its columns start
// one right of THAT candidate — the served tree's own shape answers it.

import type { LineageDivergence, LineageNode } from "@/lib/api";
import { candidatesOf, nodeKeyOf, pathOf, wasElected } from "@/lib/derivations";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";

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
  // The course's encoded address, computed once here — render loops compare it against
  // the viewed key per lane/per node, and re-encoding there is what this field deletes.
  coursePathKey: string;
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
  parentKey: string | null;
}

// A lane is addressed by `nodeKeyOf`, never by `course.id`. Inner cycle ids REPEAT
// across sibling `.inner/` sandboxes — one id is on disk three times today — so a
// map keyed on the bare id silently drops one sibling's lane onto another's.
export function layout(
  root: LineageNode,
  expanded: ReadonlySet<string>,
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
    const cands = candidatesOf(course);
    const isExpanded = expanded.has(key);
    const laneSpan = isExpanded ? expandedLaneSpan(cands) : 1;
    const laneOffset = nextRow;
    nextRow += laneSpan;
    // Rightmost column: the course's own last round. An empty course still occupies
    // its base column, so its stub sits RIGHT of the anchor rather than left of it.
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
    // Depth-first per child so a course's whole subtree stays contiguous. The child's
    // columns start one right of the candidate it hangs off — that IS the offset.
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

// Compute SVG (x, y) for every node (collapsed = one summary node per round;
// expanded = one node per candidate per round) and the branch segments between them.
export interface RoundNodePos {
  // The lane this node sits on — `nodeKeyOf(course)`, the address. Every MAP is keyed
  // on this, never on `cycleId`: inner cycle ids repeat across sibling sandboxes.
  courseKey: string;
  // The course's ADDRESS, read off the node. Selection and navigation both ride this:
  // a bare cycle id cannot name a course (inner ids repeat across sibling sandboxes), and
  // publishing one here is what made a forest click pin a path that named the wrong run.
  // The display id is `pathLeaf(coursePath).cycleId` — derived, never a second field.
  coursePath: CyclePath;
  // `encodeCyclePath(coursePath)`, once — the selected/viewed comparisons run per node
  // per render.
  coursePathKey: string;
  round: number;
  col: number;
  x: number;
  y: number;
  // Short candidate label ("C1.2") for expanded nodes; "" for a collapsed round that
  // elected nobody (Forest draws "R{n}" for collapsed nodes).
  candidateLabel: string;
  // The candidate's address (`nodeKeyOf`) — what the value/θ overlays are keyed on.
  candKey: string;
  // Stable id for selection routing — the MINTED candidate id, never a position.
  candidateId: string;
  // The served incumbency fact — who the round advanced. Structural: the spine and
  // the next round's parent ride this.
  isWinner: boolean;
  // Whether that crown was won over RIVALS (`wasElected`). Display-only, and distinct
  // from `isWinner` on purpose: a single-arm round (round 0, whose one arm is the
  // origin) advances its candidate without an election, so drawing it as a victor —
  // bold label, filled dot, "elected on θ" copy — states something that never happened.
  isElected: boolean;
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

// One placed node. Named for what it builds — `nodeAt` in `lineage-candidates.ts` is
// THE address lookup, and two different things must not wear one name.
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
    // The candidate's OWN address. A fork-contributed candidate carries the fork's
    // path, not this lane's, so this is not the lane key plus an id.
    candKey: nodeKeyOf(cand),
    candidateId: cand.id,
    isWinner: cand.is_winner,
    isElected: wasElected(cand.is_winner, roundSize),
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
  // Per (lane, round) winner/summary node — the fork-anchor spine, keyed by the
  // lane's address so two sandboxes' identically-named cycles keep their own spines.
  spineByKeyRound: Map<string, RoundNodePos>;
} {
  const nodes: RoundNodePos[] = [];
  const segs: BranchSeg[] = [];
  const spineByKeyRound = new Map<string, RoundNodePos>();
  // Candidate ids are MINTED and globally unique, so one flat map anchors a child
  // course to its parent candidate at any depth — no per-cycle key scoping.
  const nodeByCandidate = new Map<string, RoundNodePos>();

  for (const [laneKey, l] of layouts) {
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
        const node = placedNode(laneKey, l, cand, x, y, true, cand.label, cands.length);
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
        spineByKeyRound.set(`${laneKey}::r${round}`, winnerNode);
        if (round === lastRound) winnerNode.isLastInLane = true;
        parent = { x: winnerNode.x, y: winnerNode.y };
        lastWinnerNode = winnerNode;
      } else if (lastWinnerNode) {
        // Held (or never-closed) round: no new winner. Leave `parent` on the last real
        // winner so the next round fans from it — this round contributes no spine node.
        spineByKeyRound.set(`${laneKey}::r${round}`, lastWinnerNode);
        if (round === lastRound) lastWinnerNode.isLastInLane = true;
      }
    }
  }

  // Course stems — the candidate a course hangs off → that course's lane. The tree
  // names the anchor outright, so there is no `fork_from_round` semantics to
  // re-derive: resolve the candidate's node, else fall back to the parent lane's
  // band. Child stub never goes LEFT of its anchor: clamp childX to (anchorX + COL_W).
  for (const [laneKey, l] of layouts) {
    if (!l.parentKey) continue;
    const parentLayout = layouts.get(l.parentKey);
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
      const firstNode = spineByKeyRound.get(`${laneKey}::r${firstRound}`);
      if (firstNode) childX = firstNode.x;
    }
    if (childX < minChildX) childX = minChildX;
    segs.push({ x1: anchorX, y1: anchorY, x2: childX - STUB, y2: childBandY, variant: "fork" });
    segs.push({ x1: childX - STUB, y1: childBandY, x2: childX, y2: childBandY, variant: "fork" });
  }

  return { nodes, segs, spineByKeyRound };
}
