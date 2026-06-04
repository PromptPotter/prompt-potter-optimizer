"use client";
import { memo, useMemo } from "react";
import { fmtPct0 } from "@/lib/format";
import { cx } from "@/lib/cx";
import { shortFamilyTail } from "@/lib/ids";
import { useSelection } from "@/lib/SelectionContext";
import {
  CAND_STUB,
  COL_W,
  HEADER_H,
  KIND_GLYPH,
  TRIGGER_GLYPH,
  LANE_H,
  LEFT_PAD,
  NODE_R,
  RIGHT_PAD,
  TOP_PAD,
  layout,
  placeNodes,
  type CycleNode,
  type DetailByCycle,
  type LaneLayout,
  type RoundNodePos,
} from "./layout";

// One expanded candidate node: parent→child slant is drawn as a seg upstream;
// this renders the lineage-style stub + label, clickable for candidate
// selection. Memo'd so unrelated lane toggles don't re-render every node.
const CandidateNode = memo(function CandidateNode({
  n,
  selected,
  onPick,
}: {
  n: RoundNodePos;
  selected: boolean;
  onPick: (n: RoundNodePos) => void;
}) {
  const isOrigin = n.round === 0;
  return (
    <g
      className={cx("lineage-node", selected && "selected")}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={`${isOrigin ? "Origin" : `Round ${n.round}`} candidate ${n.candidateLabel}, accuracy ${fmtPct0(n.accuracy)}${n.isWinner ? ", round winner" : ""}`}
      onClick={() => onPick(n)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onPick(n);
        }
      }}
      style={{ cursor: "pointer" }}
    >
      {isOrigin ? (
        <>
          <text
            x={n.x - 4}
            y={n.y + 3}
            className={cx("lineage-label", selected && "selected")}
            textAnchor="end"
          >
            {n.candidateLabel} {fmtPct0(n.accuracy)}
          </text>
          <rect x={n.x - 64} y={n.y - 10} width={64} height={20} fill="transparent" />
        </>
      ) : (
        <>
          <line
            x1={n.x - CAND_STUB}
            y1={n.y}
            x2={n.x}
            y2={n.y}
            className={cx("lineage-stub", n.isWinner && "winner")}
          />
          <text
            x={n.x + 4}
            y={n.y + 3}
            className={cx("lineage-label", n.isWinner && "winner", selected && "selected")}
          >
            {n.candidateLabel} {fmtPct0(n.accuracy)}
          </text>
          <rect x={n.x - CAND_STUB} y={n.y - 10} width={CAND_STUB + 110} height={20} fill="transparent" />
        </>
      )}
    </g>
  );
});

// One session's cladogram — its own <svg> so cross-session lane math
// never has to be reconciled. The session header appears only when the
// campaign has more than one session.
export function Forest({
  tree,
  campaignId,
  cycleId,
  detailByCycle,
  expanded,
  onLaneActivate,
  onSelectCycle,
  sessionLabel,
}: {
  tree: CycleNode;
  campaignId: string;
  cycleId: string | null;
  detailByCycle: DetailByCycle;
  expanded: ReadonlySet<string>;
  // A lane clicked away from a searchpoint node — toggles that cycle's lane
  // between its expanded candidate cladogram and the compact summary row, in
  // place. Never changes the dashboard's selected cycle.
  onLaneActivate: (cycleId: string) => void;
  // Navigate the dashboard to a cycle — fired when a candidate in a non-selected
  // lane is clicked (the inspector/samples follow the searchpoint).
  onSelectCycle: (campaignId: string, cycleId: string) => void;
  sessionLabel: string | null;
}) {
  const { candidate, setSelectionForCandidate } = useSelection();
  // Layout is pure + the inputs are content-stabilized upstream, so this memo
  // re-runs only on a real shape change (new round / candidate / winner flip /
  // lane toggle), never on a bare 2 s poll.
  const { laneByCycle, totalLaneRows, maxCol } = useMemo(
    () => layout(tree, detailByCycle, expanded),
    [tree, detailByCycle, expanded],
  );
  const { nodes, segs } = useMemo(() => placeNodes(laneByCycle), [laneByCycle]);
  const height = TOP_PAD + totalLaneRows * LANE_H + 8;
  const width = LEFT_PAD + (maxCol + 1) * COL_W + RIGHT_PAD;

  // Round-number header — one label per column across the whole family.
  const headerCols: number[] = [];
  for (let c = 1; LEFT_PAD + c * COL_W <= width - RIGHT_PAD + COL_W / 2; c += 1) {
    headerCols.push(c);
  }

  const laneList = [...laneByCycle.values()];
  // Band y/height for a lane (covers all its rows when expanded).
  const bandTop = (l: LaneLayout): number => TOP_PAD + l.laneOffset * LANE_H - LANE_H / 2 + 2;
  const bandH = (l: LaneLayout): number => l.laneSpan * LANE_H - 4;

  // Candidate click: inspect that searchpoint. A candidate in a non-selected
  // lane first navigates the dashboard to its cycle (so inspector/samples
  // follow, and the SelectionProvider doesn't clear the candidate on the cycle
  // change); within the selected lane it picks/toggles the candidate directly.
  const onPickCandidate = (n: RoundNodePos): void => {
    if (n.cycleId !== cycleId) {
      onSelectCycle(campaignId, n.cycleId);
      return;
    }
    const isSel =
      candidate != null && candidate.round === n.round && candidate.candidate_id === n.candidateId;
    setSelectionForCandidate(
      isSel
        ? null
        : {
            round: n.round,
            candidate_id: n.candidateId,
            label: n.candidateLabel,
            accuracy: n.accuracy,
            is_winner: n.isWinner,
          },
    );
  };

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

          {segs.map((s, i) => (
            <line
              key={`seg-${i}`}
              x1={s.x1}
              y1={s.y1}
              x2={s.x2}
              y2={s.y2}
              className={cx("family-cladogram-branch", s.variant === "fork" && "fork")}
            />
          ))}

          {/* Selected-lane highlight — covers the whole band when expanded. */}
          {laneList.map((l) => {
            if (l.cycle.cycle_id !== cycleId) return null;
            return (
              <rect
                key={`hl-${l.cycle.cycle_id}`}
                x={0}
                y={bandTop(l)}
                width={width}
                height={bandH(l)}
                className="family-cladogram-lane-selected"
              />
            );
          })}

          {/* Per-lane activation overlay — painted before nodes so their clicks
              win; clicks on the row background fall through to here. A different
              fork selects+expands it; the selected fork toggles expanded ↔
              compact. This is the row-background collapse target (a searchpoint
              node always wins over it). */}
          {laneList.map((l) => {
            const cyc = l.cycle;
            const isEmpty = cyc.rounds.length === 0;
            const verb = l.expanded ? "Collapse" : "Expand";
            return (
              <rect
                key={`lanehit-${cyc.cycle_id}`}
                x={0}
                y={bandTop(l)}
                width={width}
                height={bandH(l)}
                className="family-cladogram-lane-hit"
                role="button"
                tabIndex={0}
                aria-label={`${verb} ${cyc.sibling_kind === "root" ? cyc.dataset_name || cyc.cycle_id : shortFamilyTail(cyc.cycle_id)}`}
                onClick={() => onLaneActivate(cyc.cycle_id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onLaneActivate(cyc.cycle_id);
                  }
                }}
                style={{ cursor: "pointer" }}
              >
                <title>
                  {cyc.cycle_id}
                  {`\n${cyc.sibling_kind}`}
                  {cyc.trigger === "operator_steered"
                    ? ` · steered${cyc.steered_by ? ` by ${cyc.steered_by}` : ""}`
                    : ""}
                  {cyc.status ? ` · ${cyc.status}` : ""}
                  {cyc.best_accuracy != null ? ` · best ${fmtPct0(cyc.best_accuracy)}` : ""}
                  {isEmpty
                    ? "\nNo post-divergence rounds — use Clean up in the header to prune"
                    : `\n${cyc.rounds.length} round(s) · click row to ${l.expanded ? "collapse" : "expand"}`}
                </title>
              </rect>
            );
          })}

          {/* Collapsed summary nodes (circles). */}
          {nodes
            .filter((n) => !n.isExpanded)
            .map((n) => {
              const cycleSelected = n.cycleId === cycleId;
              const layoutEntry = laneByCycle.get(n.cycleId);
              const rowLabelText = (() => {
                if (!n.isLastInLane || !layoutEntry) return null;
                const cyc = layoutEntry.cycle;
                return cyc.sibling_kind === "root"
                  ? cyc.dataset_name || cyc.cycle_id
                  : shortFamilyTail(cyc.cycle_id);
              })();
              return (
                <g
                  key={`n-${n.cycleId}-${n.round}`}
                  className={cx("family-cladogram-node", cycleSelected && "selected")}
                  onClick={() => onLaneActivate(n.cycleId)}
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
                    <text x={n.x + 8} y={n.y + 3} className="family-cladogram-cyclelabel">
                      <tspan className="family-cladogram-glyph">
                        {KIND_GLYPH[n.sibling_kind]}
                        {TRIGGER_GLYPH[n.trigger] ?? ""}
                      </tspan>
                      <tspan dx="4">{rowLabelText}</tspan>
                    </text>
                  )}
                  <title>
                    {n.cycleId} · R{n.round} · {fmtPct0(n.accuracy)}
                    {n.candidateLabel ? `\n${n.candidateLabel}` : ""}
                  </title>
                </g>
              );
            })}

          {/* Expanded candidate nodes (lineage-style stubs). */}
          {nodes
            .filter((n) => n.isExpanded)
            .map((n) => (
              <CandidateNode
                key={`c-${n.cycleId}-${n.round}-${n.candidateId}`}
                n={n}
                selected={
                  n.cycleId === cycleId &&
                  candidate != null &&
                  candidate.round === n.round &&
                  candidate.candidate_id === n.candidateId
                }
                onPick={onPickCandidate}
              />
            ))}

          {/* Expanded lanes carry their cycle label beside the last winner. */}
          {nodes
            .filter((n) => n.isExpanded && n.isLastInLane)
            .map((n) => {
              const cyc = laneByCycle.get(n.cycleId)?.cycle;
              if (!cyc) return null;
              const label =
                cyc.sibling_kind === "root"
                  ? cyc.dataset_name || cyc.cycle_id
                  : shortFamilyTail(cyc.cycle_id);
              return (
                <text
                  key={`elabel-${n.cycleId}`}
                  x={n.x + CAND_STUB + 84}
                  y={n.y + 3}
                  className="family-cladogram-cyclelabel"
                >
                  <tspan className="family-cladogram-glyph">
                    {KIND_GLYPH[cyc.sibling_kind]}
                    {TRIGGER_GLYPH[cyc.trigger] ?? ""}
                  </tspan>
                  <tspan dx="4">{label}</tspan>
                </text>
              );
            })}

        </svg>
      </div>
    </div>
  );
}
