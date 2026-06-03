// Cladogram geometry for the campaign lineage tree. Pure layout: builds the
// per-session parent/child tree, assigns lanes, and resolves SVG node + branch
// coordinates. No React — FamilyTree and Forest own the rendering.

import { type CampaignLineageCycle, type SiblingKind } from "@/lib/api";
import { rootCycleId } from "@/lib/ids";

// Cladogram dimensions. Each round across a session's family is one
// column; each cycle gets its own horizontal lane. Forks branch off
// their parent's lane at the parent-round where the fork was cut and
// then run their own rounds to the right.
export const COL_W = 110;          // width per round-column
export const LANE_H = 26;          // height per cycle-lane
export const STUB = 14;            // horizontal stub before each round node
export const LEFT_PAD = 16;
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

// Layout pass: assign one lane (vertical row) per cycle via DFS so a
// parent and its child subtree stay visually grouped. Each cycle uses
// the server-computed `round_column_offset` for its rounds' x-positions,
// so the client doesn't need to know HITL-vs-divergence semantics.
export interface LaneLayout {
  cycle: CampaignLineageCycle;
  lane: number;
  // Parent's lane info — needed when the fork's nominal attachment
  // round isn't present in the parent's rounds[] (e.g. fork_from_round
  // exceeds parent's last round, or parent has no rounds at all). The
  // stem-drawing code falls back to the parent's last-known node, then
  // to the parent's lane origin, so every fork gets a visible link.
  parentCycleId: string | null;
  forkFromRound: number | null;
  children: LaneLayout[];
}

export function layout(tree: CycleNode): {
  laneByCycle: Map<string, LaneLayout>;
  totalLanes: number;
  maxCol: number;
} {
  const laneByCycle = new Map<string, LaneLayout>();
  let nextLane = 0;
  let maxCol = 0;
  const visit = (node: CycleNode): void => {
    const cycle = node.cycle;
    const lane = nextLane++;
    // Rightmost column: when the fork has rounds, take the highest
    // round + server's column offset. When it doesn't, extend the lane
    // to one column past the cut point so the empty-fork stub has a
    // place to sit RIGHT of the parent anchor (not left of it).
    let rightmostCol: number;
    if (cycle.rounds.length > 0) {
      rightmostCol =
        cycle.round_column_offset +
        Math.max(...cycle.rounds.map((r) => r.round));
    } else {
      rightmostCol = Math.max(1, (cycle.fork_from_round ?? 0) + 1);
    }
    if (rightmostCol > maxCol) maxCol = rightmostCol;
    const me: LaneLayout = {
      cycle,
      lane,
      parentCycleId: cycle.immediate_parent_cycle_id,
      forkFromRound: cycle.fork_from_round,
      children: [],
    };
    laneByCycle.set(cycle.cycle_id, me);
    for (const child of node.children) visit(child);
  };
  visit(tree);
  return { laneByCycle, totalLanes: nextLane, maxCol };
}

// Compute SVG (x, y) for each round node and the branch segments
// between consecutive rounds (within a cycle) and from parent's
// branch-point round to a fork's first round.
export interface RoundNodePos {
  cycleId: string;
  round: number;
  col: number;
  lane: number;
  x: number;
  y: number;
  isWinner: boolean;
  accuracy: number | null;
  label: string;
  isLastInLane: boolean;
  sibling_kind: SiblingKind;
  // Fork creation trigger — drives the operator_steered / operator_endorse
  // provenance glyph beside the lane label.
  trigger: string;
}
export interface BranchSeg {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  variant: "chain" | "fork";
}

export function placeNodes(layouts: Map<string, LaneLayout>): {
  nodes: RoundNodePos[];
  segs: BranchSeg[];
  nodeByRowKey: Map<string, RoundNodePos>;
} {
  const nodes: RoundNodePos[] = [];
  const nodeByRowKey = new Map<string, RoundNodePos>();
  for (const l of layouts.values()) {
    const y = TOP_PAD + l.lane * LANE_H;
    const lastRound =
      l.cycle.rounds.length > 0
        ? Math.max(...l.cycle.rounds.map((r) => r.round))
        : 0;
    for (const round of l.cycle.rounds) {
      const col = l.cycle.round_column_offset + round.round;
      const x = LEFT_PAD + col * COL_W;
      const winnerCandidate = round.candidates.find((c) => c.is_winner);
      const accuracy = winnerCandidate?.accuracy ?? round.accuracy ?? null;
      const node: RoundNodePos = {
        cycleId: l.cycle.cycle_id,
        round: round.round,
        col,
        lane: l.lane,
        x,
        y,
        isWinner: true,
        accuracy,
        label: round.label || winnerCandidate?.label || "",
        isLastInLane: round.round === lastRound,
        sibling_kind: l.cycle.sibling_kind,
        trigger: l.cycle.trigger,
      };
      nodes.push(node);
      nodeByRowKey.set(`${l.cycle.cycle_id}::r${round.round}`, node);
    }
  }
  // Branch segments come in two flavours:
  //   chain: within a cycle, round N → round N+1 (within the same lane)
  //   fork: parent lane → child lane at the cut point
  const segs: BranchSeg[] = [];
  const byCycle = new Map<string, RoundNodePos[]>();
  for (const n of nodes) {
    const arr = byCycle.get(n.cycleId) ?? [];
    arr.push(n);
    byCycle.set(n.cycleId, arr);
  }
  for (const arr of byCycle.values()) {
    arr.sort((a, b) => a.round - b.round);
    for (let i = 0; i < arr.length - 1; i += 1) {
      const a = arr[i];
      const b = arr[i + 1];
      segs.push({ x1: a.x, y1: a.y, x2: b.x - STUB, y2: b.y, variant: "chain" });
      segs.push({ x1: b.x - STUB, y1: b.y, x2: b.x, y2: b.y, variant: "chain" });
    }
  }
  // Fork stems — always draw one, even when the parent's exact cut-point
  // node isn't present. Anchor selection respects fork_from_round
  // semantics:
  //   fr === 0           → parent's lane origin (cut before any round)
  //   parent has R{fr}   → exact match
  //   parent ran less    → parent's last round node
  //   parent has no rounds yet → parent's lane left edge
  // Child stub never goes LEFT of its anchor: we clamp childX to at least
  // (anchorX + COL_W) so the stem always slants RIGHTWARD.
  for (const l of layouts.values()) {
    if (!l.parentCycleId) continue;
    const parentLayout = layouts.get(l.parentCycleId);
    if (!parentLayout) continue;
    const parentLaneY = TOP_PAD + parentLayout.lane * LANE_H;
    const fr = l.forkFromRound;
    let anchorX: number;
    let anchorY: number;
    if (fr === 0) {
      anchorX = LEFT_PAD;
      anchorY = parentLaneY;
    } else {
      const exactKey = fr != null ? `${l.parentCycleId}::r${fr}` : null;
      const exactNode = exactKey ? nodeByRowKey.get(exactKey) : undefined;
      if (exactNode) {
        anchorX = exactNode.x;
        anchorY = exactNode.y;
      } else {
        const parentNodes = byCycle.get(l.parentCycleId) ?? [];
        if (parentNodes.length > 0) {
          const last = parentNodes[parentNodes.length - 1];
          anchorX = last.x;
          anchorY = last.y;
        } else {
          anchorX = LEFT_PAD;
          anchorY = parentLaneY;
        }
      }
    }
    const firstFork = byCycle.get(l.cycle.cycle_id)?.[0];
    const childY = TOP_PAD + l.lane * LANE_H;
    const minChildX = anchorX + COL_W; // never backward from anchor
    let childX = firstFork ? firstFork.x : minChildX;
    if (childX < minChildX) childX = minChildX;
    segs.push({
      x1: anchorX,
      y1: anchorY,
      x2: childX - STUB,
      y2: childY,
      variant: "fork",
    });
    segs.push({
      x1: childX - STUB,
      y1: childY,
      x2: childX,
      y2: childY,
      variant: "fork",
    });
  }
  return { nodes, segs, nodeByRowKey };
}

export function countDescendants(tree: CycleNode): number {
  let n = 0;
  for (const c of tree.children) n += 1 + countDescendants(c);
  return n;
}
