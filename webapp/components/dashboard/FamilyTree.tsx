"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchCampaignLineage,
  postCleanupEmpty,
  type CampaignLineageCycle,
  type CampaignLineageResponse,
  type SiblingKind,
} from "@/lib/api";
import { rootCycleId, shortFamilyTail, sessionIndexOf } from "@/lib/ids";
import { fmtPct0 } from "@/lib/format";
import { bumpRevalidation } from "@/lib/revalidate";

interface Props {
  // The campaign whose lineage to render. A campaign is a FOREST: it holds
  // N session roots, each with its own fork tree. One fetch returns every
  // cycle; we render one cladogram per session.
  campaignId: string | null;
  // The cycle currently in view — drives the selected-lane highlight.
  cycleId: string | null;
  // A unit is the pair (campaignId, cycleId); this tree is scoped to one
  // campaign, so it passes its own campaignId alongside each cycle_id.
  onSelectCycle: (campaignId: string, cycleId: string) => void;
}

// Cladogram dimensions. Each round across a session's family is one
// column; each cycle gets its own horizontal lane. Forks branch off
// their parent's lane at the parent-round where the fork was cut and
// then run their own rounds to the right.
const COL_W = 110;          // width per round-column
const LANE_H = 26;          // height per cycle-lane
const STUB = 14;            // horizontal stub before each round node
const LEFT_PAD = 16;
const HEADER_H = 18;        // column-header row at the top
const TOP_PAD = HEADER_H + 8; // first lane sits below the header row
const RIGHT_PAD = 80;       // room for the rightmost label
const NODE_R = 3.5;         // round-node circle radius

const KIND_GLYPH: Record<SiblingKind, string> = {
  root: "●",
  fork: "⑂",
  sweep: "~",
  diag: "Δ",
};

// Build immediate-parent map + per-parent child list (sorted by recency
// of fork) so the BFS lays out forks under their parent in a stable order.
interface CycleNode {
  cycle: CampaignLineageCycle;
  children: CycleNode[];
}

function buildTree(
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
interface LaneLayout {
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

function layout(tree: CycleNode): {
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
interface RoundNodePos {
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
}
interface BranchSeg {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  variant: "chain" | "fork";
}

function placeNodes(layouts: Map<string, LaneLayout>): {
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

export function FamilyTree({ campaignId, cycleId, onSelectCycle }: Props) {
  const [data, setData] = useState<CampaignLineageResponse | null>(null);
  const [tick, setTick] = useState(0);
  // The campaign's session roots — one cladogram per session. A campaign
  // is a forest: every `sibling_kind === "root"` cycle is a session.
  const rootCycleIds = useMemo(() => {
    const roots = (data?.cycles ?? [])
      .filter((c) => c.sibling_kind === "root")
      .map((c) => c.cycle_id);
    roots.sort((a, b) => sessionIndexOf(a) - sessionIndexOf(b));
    return roots;
  }, [data]);

  // Single batch-cleanup modal — campaign-wide. Empty-row stubs accumulate
  // because the fork-creation paths mint the cycle dir BEFORE the first
  // round runs; an interrupt between dir-mint and first-round leaves a
  // stub forever.
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const [cleanupError, setCleanupError] = useState<string | null>(null);
  const [cleaning, setCleaning] = useState(false);
  const [lastCleanupCount, setLastCleanupCount] = useState<number | null>(null);

  const confirmCleanup = useCallback(async () => {
    if (!campaignId || rootCycleIds.length === 0) return;
    setCleaning(true);
    setCleanupError(null);
    try {
      const r = await postCleanupEmpty(campaignId, rootCycleIds[0]);
      setLastCleanupCount(r.deleted_cycle_ids.length);
      setCleanupOpen(false);
      setTick((t) => t + 1);
      // Re-tick the workspace poll so the sidebar drops the deleted cycles
      // at once, not on its next interval.
      bumpRevalidation();
    } catch (err) {
      setCleanupError((err as Error).message);
    } finally {
      setCleaning(false);
    }
  }, [campaignId, rootCycleIds]);

  useEffect(() => {
    if (!campaignId) return;
    let cancelled = false;
    const ac = new AbortController();
    (async () => {
      try {
        const r = await fetchCampaignLineage(campaignId, ac.signal);
        if (!cancelled) setData(r);
      } catch {
        // Silent — sidebar covers navigation. Empty render below.
      }
    })();
    const onFocus = () => setTick((t) => t + 1);
    window.addEventListener("focus", onFocus);
    return () => {
      cancelled = true;
      ac.abort();
      window.removeEventListener("focus", onFocus);
    };
  }, [campaignId, tick]);

  // Campaign-wide stub count — every empty non-root cycle, across every
  // session. Same definition the server-side cleanup guard uses.
  const stubCount = useMemo(
    () =>
      (data?.cycles ?? []).filter(
        (c) => c.rounds.length === 0 && c.sibling_kind !== "root",
      ).length,
    [data],
  );

  // Per-session cladograms — only sessions that actually branched render a
  // tree (a lone root with no forks has nothing to draw).
  const forests = useMemo(() => {
    if (!data) return [];
    return rootCycleIds
      .map((rootId) => {
        const tree = buildTree(rootId, data.cycles);
        if (!tree || tree.children.length === 0) return null;
        return { rootId, tree };
      })
      .filter((f): f is { rootId: string; tree: CycleNode } => f !== null);
  }, [data, rootCycleIds]);

  if (!campaignId || forests.length === 0) return null;
  const totalDesc = forests.reduce(
    (n, f) => n + countDescendants(f.tree),
    0,
  );
  const multiSession = rootCycleIds.length > 1;

  return (
    <section className="family-cladogram" aria-label="Campaign lineage tree">
      <div className="family-cladogram-head">
        <span>Campaign lineage</span>
        <span className="family-cladogram-head-meta">
          <span className="badge">
            {totalDesc} {totalDesc === 1 ? "descendant" : "descendants"}
          </span>
          {stubCount > 0 && (
            <button
              type="button"
              className="family-cladogram-cleanup-btn"
              onClick={() => {
                setCleanupError(null);
                setCleanupOpen(true);
              }}
              title="Delete every empty-stub fork in this campaign from disk"
            >
              Clean up {stubCount} stub{stubCount === 1 ? "" : "s"}
            </button>
          )}
          {lastCleanupCount != null && stubCount === 0 && (
            <span className="family-cladogram-cleanup-done" title="Last cleanup result">
              cleaned {lastCleanupCount}
            </span>
          )}
        </span>
      </div>
      {forests.map((f) => (
        <Forest
          key={f.rootId}
          tree={f.tree}
          campaignId={campaignId}
          cycleId={cycleId}
          onSelectCycle={onSelectCycle}
          sessionLabel={
            multiSession ? `Session ${sessionIndexOf(f.rootId)}` : null
          }
        />
      ))}
      {cleanupOpen && (
        <CleanupConfirmModal
          stubCount={stubCount}
          cleaning={cleaning}
          error={cleanupError}
          onCancel={() => {
            setCleanupOpen(false);
            setCleanupError(null);
          }}
          onConfirm={confirmCleanup}
        />
      )}
    </section>
  );
}

function countDescendants(tree: CycleNode): number {
  let n = 0;
  for (const c of tree.children) n += 1 + countDescendants(c);
  return n;
}

// One session's cladogram — its own <svg> so cross-session lane math
// never has to be reconciled. The session header appears only when the
// campaign has more than one session.
function Forest({
  tree,
  campaignId,
  cycleId,
  onSelectCycle,
  sessionLabel,
}: {
  tree: CycleNode;
  campaignId: string;
  cycleId: string | null;
  onSelectCycle: (campaignId: string, cycleId: string) => void;
  sessionLabel: string | null;
}) {
  const { laneByCycle, totalLanes, maxCol } = layout(tree);
  const { nodes, segs } = placeNodes(laneByCycle);
  const height = TOP_PAD + totalLanes * LANE_H + 8;
  const width = LEFT_PAD + (maxCol + 1) * COL_W + RIGHT_PAD;

  // Round-number header — one label per column across the whole family.
  const headerCols: number[] = [];
  for (let c = 1; LEFT_PAD + c * COL_W <= width - RIGHT_PAD + COL_W / 2; c += 1) {
    headerCols.push(c);
  }

  return (
    <div className="family-cladogram-forest">
      {sessionLabel && (
        <div className="family-cladogram-session-head">{sessionLabel}</div>
      )}
      <div className="family-cladogram-scroll">
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          xmlns="http://www.w3.org/2000/svg"
          className="family-cladogram-svg"
          aria-label="Session lineage cladogram"
          shapeRendering="crispEdges"
        >
          {headerCols.map((c) => {
            const x = LEFT_PAD + c * COL_W;
            return (
              <g key={`hdr-${c}`} className="family-cladogram-header-col">
                <line
                  x1={x}
                  y1={HEADER_H}
                  x2={x}
                  y2={height - 4}
                  className="family-cladogram-gridline"
                />
                <text
                  x={x}
                  y={HEADER_H - 4}
                  className="family-cladogram-headerlabel"
                  textAnchor="middle"
                >
                  R{c}
                </text>
              </g>
            );
          })}

          {/* Origin label, anchored at the trunk's left tip. */}
          {(() => {
            const rootLayout = [...laneByCycle.values()].find(
              (l) => l.cycle.sibling_kind === "root",
            );
            if (!rootLayout) return null;
            const y = TOP_PAD + rootLayout.lane * LANE_H;
            return (
              <text
                x={LEFT_PAD - 4}
                y={y + 3}
                className="family-cladogram-origin"
                textAnchor="end"
              >
                origin
              </text>
            );
          })()}

          {segs.map((s, i) => (
            <line
              key={`seg-${i}`}
              x1={s.x1}
              y1={s.y1}
              x2={s.x2}
              y2={s.y2}
              className={`family-cladogram-branch${
                s.variant === "fork" ? " fork" : ""
              }`}
            />
          ))}

          {/* Selected-lane highlight. */}
          {[...laneByCycle.values()].map((l) => {
            const selected = l.cycle.cycle_id === cycleId;
            if (!selected) return null;
            const y = TOP_PAD + l.lane * LANE_H;
            return (
              <rect
                key={`hl-${l.cycle.cycle_id}`}
                x={0}
                y={y - LANE_H / 2 + 2}
                width={width}
                height={LANE_H - 4}
                className="family-cladogram-lane-selected"
              />
            );
          })}

          {nodes.map((n) => {
            const cycleSelected = n.cycleId === cycleId;
            const layoutEntry = laneByCycle.get(n.cycleId);
            const rowLabelText = (() => {
              if (!n.isLastInLane) return null;
              if (!layoutEntry) return null;
              const cyc = layoutEntry.cycle;
              return cyc.sibling_kind === "root"
                ? cyc.dataset_name || cyc.cycle_id
                : shortFamilyTail(cyc.cycle_id);
            })();
            return (
              <g
                key={`n-${n.cycleId}-${n.round}`}
                className={`family-cladogram-node${cycleSelected ? " selected" : ""}`}
                onClick={() => onSelectCycle(campaignId, n.cycleId)}
                style={{ cursor: "pointer" }}
              >
                <circle
                  cx={n.x}
                  cy={n.y}
                  r={NODE_R}
                  className={`family-cladogram-dot kind-${n.sibling_kind}`}
                />
                <text
                  x={n.x}
                  y={n.y - 6}
                  className="family-cladogram-roundlabel"
                  textAnchor="middle"
                >
                  R{n.round} {fmtPct0(n.accuracy)}
                </text>
                {rowLabelText && (
                  <text
                    x={n.x + 8}
                    y={n.y + 3}
                    className="family-cladogram-cyclelabel"
                  >
                    <tspan className="family-cladogram-glyph">
                      {KIND_GLYPH[n.sibling_kind]}
                    </tspan>
                    <tspan dx="4">{rowLabelText}</tspan>
                  </text>
                )}
                <rect
                  x={n.x - COL_W / 2}
                  y={n.y - LANE_H / 2 + 2}
                  width={COL_W}
                  height={LANE_H - 4}
                  fill="transparent"
                />
                <title>
                  {n.cycleId} · R{n.round} · {fmtPct0(n.accuracy)}
                  {n.label ? `\n${n.label}` : ""}
                </title>
              </g>
            );
          })}

          {/* Per-lane interactive overlay — every lane (including
              empty-rounds stubs) gets a full-width click target. */}
          {[...laneByCycle.values()].map((l) => {
            const y = TOP_PAD + l.lane * LANE_H;
            const cyc = l.cycle;
            const isEmpty = cyc.rounds.length === 0;
            return (
              <rect
                key={`lanehit-${cyc.cycle_id}`}
                x={0}
                y={y - LANE_H / 2 + 2}
                width={width}
                height={LANE_H - 4}
                className="family-cladogram-lane-hit"
                onClick={() => onSelectCycle(campaignId, cyc.cycle_id)}
                style={{ cursor: "pointer" }}
              >
                <title>
                  {cyc.cycle_id}
                  {`\n${cyc.sibling_kind}`}
                  {cyc.status ? ` · ${cyc.status}` : ""}
                  {cyc.best_accuracy != null
                    ? ` · best ${fmtPct0(cyc.best_accuracy)}`
                    : ""}
                  {isEmpty
                    ? "\nNo post-divergence rounds — use Clean up in the header to prune"
                    : `\n${cyc.rounds.length} round(s)`}
                </title>
              </rect>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

function CleanupConfirmModal({
  stubCount,
  cleaning,
  error,
  onCancel,
  onConfirm,
}: {
  stubCount: number;
  cleaning: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Confirm stub cleanup"
      onClick={onCancel}
    >
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <h2 className="modal-title">
          Clean up {stubCount} empty stub{stubCount === 1 ? "" : "s"}?
        </h2>
        <p className="family-tree-modal-body">
          Removes every fork / sweep / diag dir in this campaign that has{" "}
          <code>n_rounds = 0</code> and no descendants. Units that ran real
          work, the active unit, session roots, and units with children are
          skipped.
        </p>
        {error && <p className="family-tree-modal-error">{error}</p>}
        <div className="modal-actions">
          <button type="button" onClick={onCancel} disabled={cleaning}>
            Cancel
          </button>
          <button
            type="button"
            className="modal-primary"
            onClick={onConfirm}
            disabled={cleaning}
          >
            {cleaning ? "Cleaning…" : "Delete stubs"}
          </button>
        </div>
      </div>
    </div>
  );
}
