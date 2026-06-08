// Cladogram geometry for the campaign lineage tree. Pure layout: builds the
// per-session parent/child tree, assigns lanes, and resolves SVG node + branch
// coordinates. No React — FamilyTree and Forest own the rendering.
//
// One tree, two depths per cycle lane:
//   collapsed — one summary node per round (the round winner), the compact
//               campaign/fork view. laneSpan = 1 row.
//   expanded  — the full intra-cycle candidate cladogram for that cycle:
//               one node per candidate per round, winner→children branches.
//               laneSpan = the widest round's candidate count, so an expanded
//               lane pushes the lanes below it down by that many rows.
// Forks anchor to the parent's WINNER node (the "spine") at the fork round, so
// a fork stem stays attached even when the parent lane fans out.

import { type CampaignLineageCycle, type SiblingKind } from "@/lib/api";
import { rootCycleId } from "@/lib/ids";

// Cladogram dimensions. Each round across a session's family is one
// column; each cycle gets its own horizontal lane (one row collapsed,
// N rows expanded). Forks branch off their parent's lane at the
// parent-round where the fork was cut and then run their own rounds.
export const COL_W = 110;          // width per round-column
export const LANE_H = 26;          // height per lane-row
export const STUB = 14;            // horizontal stub before a collapsed round node
export const CAND_STUB = 22;       // horizontal stub before an expanded candidate node
// Left margin before column 0 — room for round 1's left-anchored stub + label.
export const LEFT_PAD = 48;
export const HEADER_H = 18;        // column-header row at the top
export const TOP_PAD = HEADER_H + 8; // first lane sits below the header row
export const RIGHT_PAD = 80;       // room for the rightmost label
export const NODE_R = 3.5;         // round-node circle radius

export const KIND_GLYPH: Record<SiblingKind, string> = {
  root: "●",
  fork: "⑂",
  sweep: "~",
  diag: "Δ",
};

// Operator-fork provenance mark, appended after the kind glyph. "✎" = the
// operator steered the searchpoint (operator_steered). Everything else
// (auto/divergence/sweep) is unmarked.
export const TRIGGER_GLYPH: Record<string, string> = {
  operator_steered: "✎",
};

// Normalized per-cycle detail the geometry consumes. Built by FamilyTree from
// the lineage snapshot for every cycle, then OVERRIDDEN for the in-view active
// cycle with live dashboard.json candidates (source-by-cycle-role — never a
// per-field merge of the two). Labels are already short ("C1.2") here.
export interface LaneCandidate {
  candidateId: string;
  label: string;
  accuracy: number | null;
  isWinner: boolean;
}
export interface LaneRound {
  round: number;
  candidates: LaneCandidate[];
}
export interface CycleDetail {
  rounds: LaneRound[];
}
export type DetailByCycle = Map<string, CycleDetail>;

const EMPTY_DETAIL: CycleDetail = { rounds: [] };

// The round's elected winner (or first candidate as a fallback) — the node a
// fork anchors to and the parent of the next round's fan.
function pickWinner(candidates: LaneCandidate[]): LaneCandidate | null {
  if (candidates.length === 0) return null;
  return candidates.find((c) => c.isWinner) ?? candidates[0];
}

// Rows an expanded lane occupies — the widest round's candidate count, floored
// at 1 so a single-candidate lane still has a row.
export function expandedLaneSpan(detail: CycleDetail): number {
  let max = 1;
  for (const r of detail.rounds) {
    if (r.candidates.length > max) max = r.candidates.length;
  }
  return max;
}

// Build immediate-parent map + per-parent child list (sorted by recency
// of fork) so the BFS lays out forks under their parent in a stable order.
export interface CycleNode {
  cycle: CampaignLineageCycle;
  children: CycleNode[];
}

export function buildTree(
  rootId: string,
  cycles: CampaignLineageCycle[],
): CycleNode | null {
  // A campaign is a forest of N session roots — restrict to THIS session's
  // own cycles so an orphan never attaches across session boundaries.
  const own = cycles.filter((c) => rootCycleId(c.cycle_id) === rootId);
  const byId = new Map<string, CampaignLineageCycle>();
  for (const c of own) byId.set(c.cycle_id, c);
  const root = byId.get(rootId);
  if (!root) return null;
  const childrenOf = new Map<string, CampaignLineageCycle[]>();
  for (const c of own) {
    if (c.cycle_id === rootId) continue;
    const pid =
      c.immediate_parent_cycle_id && byId.has(c.immediate_parent_cycle_id)
        ? c.immediate_parent_cycle_id
        : rootId;
    const arr = childrenOf.get(pid) ?? [];
    arr.push(c);
    childrenOf.set(pid, arr);
  }
  // Sort children by fork_from_round (smaller round first — earlier
  // fork sits visually closer to the parent's first rounds), then by
  // cycle_id for stable ordering when round info is missing.
  for (const arr of childrenOf.values()) {
    arr.sort((a, b) => {
      const ra = a.fork_from_round ?? Number.MAX_SAFE_INTEGER;
      const rb = b.fork_from_round ?? Number.MAX_SAFE_INTEGER;
      if (ra !== rb) return ra - rb;
      return a.cycle_id.localeCompare(b.cycle_id);
    });
  }
  const visit = (cycle: CampaignLineageCycle): CycleNode => ({
    cycle,
    children: (childrenOf.get(cycle.cycle_id) ?? []).map(visit),
  });
  return visit(root);
}

// Layout pass: assign each cycle a lane band (a starting row `laneOffset` and a
// height `laneSpan` in LANE_H units) via DFS so a parent and its child subtree
// stay visually grouped. An expanded cycle reserves `laneSpan` rows; every lane
// after it shifts down. Each cycle uses the server-computed
// `round_column_offset` for x-positions, so the client doesn't need to know
// HITL-vs-divergence semantics.
export interface LaneLayout {
  cycle: CampaignLineageCycle;
  detail: CycleDetail;
  expanded: boolean;
  // Starting row (in LANE_H units) of this cycle's band, and its height.
  laneOffset: number;
  laneSpan: number;
  parentCycleId: string | null;
  forkFromRound: number | null;
  forkFromCandidateId: string | null;
}

export function layout(
  tree: CycleNode,
  detailByCycle: DetailByCycle,
  expanded: ReadonlySet<string>,
): {
  laneByCycle: Map<string, LaneLayout>;
  totalLaneRows: number;
  maxCol: number;
} {
  const laneByCycle = new Map<string, LaneLayout>();
  let nextRow = 0;
  let maxCol = 0;
  const visit = (node: CycleNode): void => {
    const cycle = node.cycle;
    const detail = detailByCycle.get(cycle.cycle_id) ?? EMPTY_DETAIL;
    const isExpanded = expanded.has(cycle.cycle_id);
    const laneSpan = isExpanded ? expandedLaneSpan(detail) : 1;
    const laneOffset = nextRow;
    nextRow += laneSpan;
    // Rightmost column: when the cycle has rounds, take the highest round +
    // server's column offset. When it doesn't, extend the lane to one column
    // past the cut point so the empty-fork stub sits RIGHT of the parent
    // anchor (not left of it).
    let rightmostCol: number;
    if (detail.rounds.length > 0) {
      rightmostCol =
        cycle.round_column_offset + Math.max(...detail.rounds.map((r) => r.round));
    } else {
      rightmostCol = Math.max(1, (cycle.fork_from_round ?? 0) + 1);
    }
    if (rightmostCol > maxCol) maxCol = rightmostCol;
    laneByCycle.set(cycle.cycle_id, {
      cycle,
      detail,
      expanded: isExpanded,
      laneOffset,
      laneSpan,
      parentCycleId: cycle.immediate_parent_cycle_id,
      forkFromRound: cycle.fork_from_round,
      forkFromCandidateId: cycle.fork_from_candidate_id,
    });
    for (const child of node.children) visit(child);
  };
  visit(tree);
  return { laneByCycle, totalLaneRows: nextRow, maxCol };
}

// Compute SVG (x, y) for every node (collapsed = one summary node per round;
// expanded = one node per candidate per round) and the branch segments between
// them.
export interface RoundNodePos {
  cycleId: string;
  round: number;
  col: number;
  x: number;
  y: number;
  accuracy: number | null;
  // Short candidate label ("C1.2") for expanded nodes; "" for collapsed summary
  // nodes (Forest draws "R{n}" for those).
  candidateLabel: string;
  // Stable id for selection routing. Origin uses ORIGIN_CANDIDATE_ID.
  candidateId: string;
  isWinner: boolean;
  isExpanded: boolean;
  // Carries the lane (cycle) label — the winner of the cycle's last round.
  isLastInLane: boolean;
  sibling_kind: SiblingKind;
  // Fork creation trigger — drives the operator_steered provenance glyph.
  trigger: string;
}
export interface BranchSeg {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  variant: "chain" | "fork";
}

// Band-center y for a lane (the row the collapsed node sits on, and the y the
// origin / fork stems anchor to).
function bandCenterY(l: LaneLayout): number {
  return TOP_PAD + (l.laneOffset + (l.laneSpan - 1) / 2) * LANE_H;
}

// Left x of a lane's column band — what the band-left fork fallbacks anchor to.
function bandLeftX(l: LaneLayout): number {
  return LEFT_PAD + l.cycle.round_column_offset * COL_W;
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
  // Parent-cycle candidate lookup for forks that carry from_candidate_id.
  const nodeByCandidate = new Map<string, RoundNodePos>();

  for (const l of layouts.values()) {
    const offset = l.cycle.round_column_offset;
    const rounds = [...l.detail.rounds].sort((a, b) => a.round - b.round);
    const lastRound = rounds.length > 0 ? rounds[rounds.length - 1].round : 0;
    const colX = (round: number): number => LEFT_PAD + (offset + round) * COL_W;

    if (!l.expanded) {
      // Collapsed: one summary node per round on the band's single row.
      const y = bandCenterY(l);
      let prev: RoundNodePos | null = null;
      for (const r of rounds) {
        const winner = pickWinner(r.candidates);
        const node: RoundNodePos = {
          cycleId: l.cycle.cycle_id,
          round: r.round,
          col: offset + r.round,
          x: colX(r.round),
          y,
          accuracy: winner?.accuracy ?? null,
          candidateLabel: winner?.label ?? "",
          candidateId: winner?.candidateId ?? "",
          isWinner: true,
          isExpanded: false,
          isLastInLane: r.round === lastRound,
          sibling_kind: l.cycle.sibling_kind,
          trigger: l.cycle.trigger,
        };
        nodes.push(node);
        spineByCycleRound.set(`${l.cycle.cycle_id}::r${r.round}`, node);
        if (node.candidateId) {
          nodeByCandidate.set(`${l.cycle.cycle_id}::c${node.candidateId}`, node);
        }
        if (prev) {
          segs.push({ x1: prev.x, y1: prev.y, x2: node.x - STUB, y2: node.y, variant: "chain" });
          segs.push({ x1: node.x - STUB, y1: node.y, x2: node.x, y2: node.y, variant: "chain" });
        }
        prev = node;
      }
      continue;
    }

    // Expanded: the full intra-cycle candidate cladogram. Round 1 has no
    // parent node (the origin trunk was removed) — its candidates draw only
    // their own stub; each later round chains from the prior round's winner.
    let parent: { x: number; y: number } | null = null;

    for (const r of rounds) {
      const n = r.candidates.length;
      if (n === 0) continue;
      const topRow = (l.laneSpan - n) / 2;
      const x = colX(r.round);
      const roundNodes: RoundNodePos[] = [];
      r.candidates.forEach((cand, i) => {
        const y = TOP_PAD + (l.laneOffset + topRow + i) * LANE_H;
        const node: RoundNodePos = {
          cycleId: l.cycle.cycle_id,
          round: r.round,
          col: offset + r.round,
          x,
          y,
          accuracy: cand.accuracy,
          candidateLabel: cand.label,
          candidateId: cand.candidateId,
          isWinner: cand.isWinner,
          isExpanded: true,
          isLastInLane: false,
          sibling_kind: l.cycle.sibling_kind,
          trigger: l.cycle.trigger,
        };
        nodes.push(node);
        roundNodes.push(node);
        if (node.candidateId) {
          nodeByCandidate.set(`${l.cycle.cycle_id}::c${node.candidateId}`, node);
        }
        // Parent winner → this child's stub start. The stub itself (and the
        // label) is drawn by the node group in lineage style, so emit only the
        // slant here. Round 1 has no parent (no origin) — it draws just its stub.
        if (parent) {
          segs.push({ x1: parent.x, y1: parent.y, x2: x - CAND_STUB, y2: y, variant: "chain" });
        }
      });
      const winner = pickWinner(r.candidates);
      const winnerNode =
        roundNodes.find((nn) => nn.candidateId === winner?.candidateId) ?? roundNodes[0];
      spineByCycleRound.set(`${l.cycle.cycle_id}::r${r.round}`, winnerNode);
      if (r.round === lastRound) winnerNode.isLastInLane = true;
      parent = { x: winnerNode.x, y: winnerNode.y };
    }
  }

  // Fork stems — parent spine node (or specific from-candidate) → child lane.
  // Anchor selection respects fork_from_round semantics:
  //   fr === 0           → parent's band center (cut before any round)
  //   from_candidate_id  → that exact candidate node
  //   parent has R{fr}   → parent's winner spine at that round
  //   parent ran less    → parent's last spine node
  //   parent has no rounds yet → parent's band center
  // Child stub never goes LEFT of its anchor: clamp childX to (anchorX + COL_W).
  for (const l of layouts.values()) {
    if (!l.parentCycleId) continue;
    const parentLayout = layouts.get(l.parentCycleId);
    if (!parentLayout) continue;
    const parentBandY = bandCenterY(parentLayout);
    const fr = l.forkFromRound;
    let anchorX: number;
    let anchorY: number;
    const fromCand = l.forkFromCandidateId
      ? nodeByCandidate.get(`${l.parentCycleId}::c${l.forkFromCandidateId}`)
      : undefined;
    if (fromCand) {
      anchorX = fromCand.x;
      anchorY = fromCand.y;
    } else if (fr === 0) {
      anchorX = bandLeftX(parentLayout);
      anchorY = parentBandY;
    } else {
      const exact = fr != null ? spineByCycleRound.get(`${l.parentCycleId}::r${fr}`) : undefined;
      if (exact) {
        anchorX = exact.x;
        anchorY = exact.y;
      } else {
        // Parent's last spine node, else its band left edge.
        let last: RoundNodePos | undefined;
        for (const r of parentLayout.detail.rounds) {
          const node = spineByCycleRound.get(`${l.parentCycleId}::r${r.round}`);
          if (node) last = node;
        }
        if (last) {
          anchorX = last.x;
          anchorY = last.y;
        } else {
          anchorX = LEFT_PAD + parentLayout.cycle.round_column_offset * COL_W;
          anchorY = parentBandY;
        }
      }
    }
    const childBandY = bandCenterY(l);
    const minChildX = anchorX + COL_W;
    // Anchor the child stem at its first node (min round); empty forks at the
    // clamped minimum so the stem always slants rightward.
    let childX = minChildX;
    const firstRound = l.detail.rounds.length > 0
      ? Math.min(...l.detail.rounds.map((r) => r.round))
      : null;
    if (firstRound != null) {
      const firstNode = spineByCycleRound.get(`${l.cycle.cycle_id}::r${firstRound}`);
      if (firstNode) childX = firstNode.x;
    }
    if (childX < minChildX) childX = minChildX;
    segs.push({ x1: anchorX, y1: anchorY, x2: childX - STUB, y2: childBandY, variant: "fork" });
    segs.push({ x1: childX - STUB, y1: childBandY, x2: childX, y2: childBandY, variant: "fork" });
  }

  return { nodes, segs, spineByCycleRound };
}

export function countDescendants(tree: CycleNode): number {
  let n = 0;
  for (const c of tree.children) n += 1 + countDescendants(c);
  return n;
}
