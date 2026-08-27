// Cladogram geometry for the served lineage tree. Pure layout: walks the ONE tree `/tree`
// serves, assigns lanes, resolves SVG node + branch coordinates. No React.
//
// The tree alternates at every depth, so this is one recursion rather than a per-tier rule, and
// a course's columns start one right of the candidate it hangs off — the served shape answers
// it, with no client-side tree build. A collapsed lane spans one row; an expanded one spans the
// widest round's candidate count and pushes the lanes below it down.

import type { LineageDivergence, LineageNode } from "@/lib/api";
import { candidatesOf, nodeKeyOf, pathOf, wasElected } from "@/lib/derivations";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";

// Cladogram dimensions. Each round is one column; each course gets its own
// horizontal lane (one row collapsed, N rows expanded).
//
// The HORIZONTAL half is a density, because a tree's width is set by the text on it: a labelled
// node needs a column wide enough to print `C1.2 33%`, an unlabelled one needs room for a dot.
// So a surface that must fit two trees beside each other drops the text and the columns close up
// — never a scaled `viewBox`, which compresses until labels collide with nothing to say so.
// The VERTICAL half is not a density: rows stay the height of a click target either way.
export interface Density {
  colW: number;        // width per round-column
  leftPad: number;     // left margin before column 0
  rightPad: number;    // room past the rightmost node
  stub: number;        // horizontal stub before a collapsed round node
  candStub: number;    // horizontal stub before an expanded candidate node
  // Print the per-node text. Off, every label still rides the node's `<title>` and the surface
  // names what was clicked, so nothing is unreachable — only unprinted.
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

// Which side of a cut the run CONTINUES on, as the tree serves it (`FORK_DIRECTION`,
// derived server-side from the trigger — never re-derived here).
export type ForkDirection = NonNullable<LineageNode["fork_direction"]>;

// "↳" = this branch IS the line now and the PARENT is what was left behind. "≡" = the cut
// was taken but changed nothing measurable, so both sides carry on identically — a resume
// that corrected a round nothing downstream had read. An offshoot is unmarked: it already
// reads as one, hanging off a line that keeps running. All three are the same shape on disk
// and wore the same ⑂, so an operator could not tell a branch that superseded its parent
// from one exploring beside it, nor either from a cut that turned out to be a no-op.
export const DIRECTION_GLYPH: Record<ForkDirection, string> = {
  offshoot: "",
  supersede: "↳",
  equivalent: "≡",
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

// A point on the drawing, as an address rather than a position — what a surface hands in when it
// wants a searchpoint found. The two halves are what a placed node publishes, and both are
// needed: a candidate id repeats across `.inner/` sandboxes, and a course path holds many.
export interface CladogramAnchor {
  coursePathKey: string;
  candidateId: string;
}

// What a point CAME FROM, as a set of candidate addresses (`nodeKeyOf`) — its extent, defined by
// `webapp/CLAUDE.md` § Component conventions, including why it is never a round-COLUMN test.
// Deliberately OUT: a sibling seed of the same ancestor, a fork off the anchor, every later
// round. `null` where this tree does not hold the anchor at all.
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
  // Down to the anchor; on the way back up each course keeps its rounds through the one the
  // chain leaves it on.
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

// A lane is addressed by `nodeKeyOf`, never by `course.id`. Inner cycle ids REPEAT
// across sibling `.inner/` sandboxes — one id is on disk three times today — so a
// map keyed on the bare id silently drops one sibling's lane onto another's.
//
// `keep` is an extent (`extentKeys`): only those candidates are laid out, and a course the
// extent never reaches takes no lane row and no column rather than being drawn empty — so rows,
// width and columns all fall out of the smaller shape instead of being trimmed afterwards.
// `null` lays out the whole family, which is the identity of the cut.
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
    // A course the extent never reaches contributed nothing to the point — it takes no lane row
    // and no column, rather than being drawn empty. Its subtree goes with it: the extent keeps
    // the candidate a child hangs off whenever it keeps the child.
    if (keep !== null && cands.length === 0) return;
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
  // elected nobody (Forest draws "R{n}" for collapsed nodes). DISPLAY ONLY — it is this
  // candidate's renumbered position on the campaign's one timeline.
  candidateLabel: string;
  // The SERVED node this geometry was placed from. A drawing's node is a position; every
  // question about the searchpoint itself — its config, its scalars, its `course_label` join key
  // — is answered by the tree, so it rides along rather than being re-found by key at each
  // click. Copying the few fields a renderer reads is fine; re-deriving a searchpoint from a
  // placed dot is a second answer to what the tree already says.
  node: LineageNode;
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
  // Which side of the cut continues; null for a root or an inner run, neither of which
  // was cut from anything.
  forkDirection: ForkDirection | null;
  // The counterfactual, carried by the node itself rather than re-joined from a
  // parallel array by a hand-rolled `{cycle}::r{round}` key.
  divergence: LineageDivergence | null;
  divergent: boolean;
  // The branch that replaced this candidate — served on the left-behind side of a
  // supersede cut. Kept apart from `divergent`: that one is a counterfactual under a
  // lens the operator applied, this one is what the run actually did.
  retiredBy: string | null;
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
function bandLeftX(l: LaneLayout, d: Density): number {
  return d.leftPad + l.baseCol * d.colW;
}

// One placed node — named for what it BUILDS, never for what it looks up. Addressing lives
// in `lineage-candidates.ts`, and two different things must not wear one name.
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
    // The PLACED node, which is the one `candidateId` and `candKey` also name — the collapsed
    // band passes the winner's label for DISPLAY while standing on `stand`, so the two can differ
    // and only this one answers for the searchpoint.
    node: cand,
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
    forkDirection: l.course.fork_direction ?? null,
    divergence: cand.divergence,
    divergent: cand.divergent,
    retiredBy: cand.superseded_by,
  };
}

export function placeNodes(layouts: Map<string, LaneLayout>, d: Density): {
  nodes: RoundNodePos[];
  segs: BranchSeg[];
  // Per (lane, round) winner/summary node — the fork-anchor spine, keyed by the
  // lane's address so two sandboxes' identically-named cycles keep their own spines.
  spineByKeyRound: Map<string, RoundNodePos>;
} {
  const nodes: RoundNodePos[] = [];
  const segs: BranchSeg[] = [];
  const spineByKeyRound = new Map<string, RoundNodePos>();
  // Anchors a child course to its parent candidate at any depth — no per-cycle key scoping.
  // A candidate id is MINTED, but it is not unique on the timeline: a repair re-measures
  // rather than re-mints, so one id names both the corrected node and the measurement it
  // withdrew. The RETIRED one is skipped — the runs measured the individual and are served on
  // the live node, so a last-write-wins map would resolve correctly only by iteration order.
  const nodeByCandidate = new Map<string, RoundNodePos>();

  for (const [laneKey, l] of layouts) {
    const rounds = [...groupRounds(l.candidates).entries()].sort((a, b) => a[0] - b[0]);
    const lastRound = rounds.at(-1)?.[0] ?? 0;
    const colX = (round: number): number => d.leftPad + (l.baseCol + round) * d.colW;

    if (!l.expanded) {
      // Collapsed: one summary node per round on the band's single row.
      const y = bandCenterY(l);
      let prev: RoundNodePos | null = null;
      for (const [round, cands] of rounds) {
        const winner = pickWinner(cands);
        // The round's stand-in when it elected nobody: the first candidate carries
        // the position, but never the crown — `isWinner` rides the real fact. A RETIRED
        // one is passed over: a correction withdraws the crown from the tail it cut, so
        // the first candidate of such a round is exactly the one the run stopped being,
        // and a collapsed band that stands on it plots the abandoned line.
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
        if (!cand.superseded_by) nodeByCandidate.set(cand.id, node);
        // Parent winner → this child's stub start. The stub itself (and the label) is
        // drawn by the node group in lineage style, so emit only the slant here.
        if (parent) {
          segs.push({ x1: parent.x, y1: parent.y, x2: x - d.candStub, y2: y, variant: "chain" });
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
    const anchorX = anchorNode?.x ?? bandLeftX(parentLayout, d);
    const anchorY = anchorNode?.y ?? bandCenterY(parentLayout);

    const childBandY = bandCenterY(l);
    const minChildX = anchorX + d.colW;
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
    segs.push({ x1: anchorX, y1: anchorY, x2: childX - d.stub, y2: childBandY, variant: "fork" });
    segs.push({ x1: childX - d.stub, y1: childBandY, x2: childX, y2: childBandY, variant: "fork" });
  }

  return { nodes, segs, spineByKeyRound };
}
