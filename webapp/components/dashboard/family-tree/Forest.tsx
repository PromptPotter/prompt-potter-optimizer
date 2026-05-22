"use client";
import { fmtPct0 } from "@/lib/format";
import { shortFamilyTail } from "@/lib/ids";
import {
  COL_W,
  HEADER_H,
  KIND_GLYPH,
  LANE_H,
  LEFT_PAD,
  NODE_R,
  RIGHT_PAD,
  TOP_PAD,
  layout,
  placeNodes,
  type CycleNode,
} from "./layout";

// One session's cladogram — its own <svg> so cross-session lane math
// never has to be reconciled. The session header appears only when the
// campaign has more than one session.
export function Forest({
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
