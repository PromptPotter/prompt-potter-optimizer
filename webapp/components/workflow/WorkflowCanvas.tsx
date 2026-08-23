"use client";
import { CANVAS_W, CANVAS_H, DOT_R, EDGES, INTENTIONALLY_UNPLACED, LAYOUT } from "./layout";
import { TERMS } from "@/lib/terms";
import { cx } from "@/lib/cx";
import { runPhaseLabel } from "@/lib/run-phase";
import { useSelection } from "@/lib/SelectionContext";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useRoundNodes } from "@/lib/hooks/useRoundNodes";
import { CopyButton } from "@/components/ui";
import { RoundAxis } from "./RoundAxis";
import { nodeKindLabel, type PipelineDoc } from "./types";

// Edge variants — collapses three parallel switches (stroke colour key,
// dasharray, arrowhead marker id) into one row per kind. Add a kind:
// extend the map, no other site changes.
type ColorKey = "txt" | "ok" | "acc" | "esc";
interface EdgeVariant {
  color: ColorKey;
  dash: string;
  marker: string;
}
const EDGE_FORWARD: EdgeVariant = { color: "txt", dash: "", marker: "arrh" };
const EDGE_VARIANTS: Record<string, EdgeVariant> = {
  forward:   EDGE_FORWARD,
  loop:      { color: "ok",  dash: "5 3", marker: "arrh-loop" },
  directive: { color: "acc", dash: "4 3", marker: "arrh-dir" },
  escalate:  { color: "esc", dash: "2 3", marker: "arrh-esc" },
};

interface Props {
  pipeline: PipelineDoc | null;
}

export function WorkflowCanvas({ pipeline }: Props) {
  // Self-sourced liveness from the cycle stream (poll age), not `dash`
  // truthiness — a frozen campaign still has a `dash` snapshot but is not live.
  const { dash, isLive } = useDashboard();
  const view = pipeline?.view;
  // One compact wide-short layout at every width — keeps the optimizer
  // card (and the dashboard) short on desktop as well as phone.
  const layout = LAYOUT;
  const edges = EDGES;
  const canvasW = CANVAS_W;
  // Served nodes this hand-drawn geometry has no position for. `output` is
  // deliberately absent; anything else means the optimizer manifest gained a
  // node LAYOUT was never told about. They get a stray row rather than being
  // dropped — four hand-maintained copies of this topology agree today, and the
  // only thing that would ever have reported them diverging was silence.
  const strayNodes = (view?.nodes ?? []).filter(
    (n) => !LAYOUT[n.id] && !INTENTIONALLY_UNPLACED.has(n.id),
  );
  const STRAY_ROW_H = 38;
  const canvasH = CANVAS_H + (strayNodes.length ? STRAY_ROW_H : 0);
  const activeId = dash?.current_round.active_node ?? null;
  // The optimizer can only ever depict ONE round, so the round axis is this
  // card's own scope — its picker sits in the toolbar and its dots, labels and
  // pulse all read the round it resolves. Node selection rides the shared
  // SelectionContext so the Now lane can swap in OptimizerNodeDetail below.
  const { node: selected, setSelectionForNode: setSelected } = useSelection();
  const {
    nodes: roundNodes,
    round: viewedRound,
    showsCurrent: viewingLive,
    loading: nodesLoading,
  } = useRoundNodes();
  // This is INLINE svg, not a canvas, so its presentation attributes resolve `var()` off the
  // document cascade — a theme flip repaints with no read, no subscription and no re-render.
  const colors = {
    txt: "var(--color-text-secondary)",
    ok: "var(--color-success)",
    acc: "var(--color-accent)",
    esc: "var(--color-accent-strong)",
    bg: "var(--color-background-tertiary)",
  };

  if (!view) {
    return (
      <div className="workflow-card">
        <div className="workflow-toolbar">
          <span className="workflow-title">Optimizer</span>
          <span id="wf-status" style={{ color: "var(--color-text-tertiary)" }}>● topology pending</span>
        </div>
        <div className="workflow-canvas-bg" style={{ minHeight: 200 }} />
      </div>
    );
  }

  // Node id → label, so a terminal edge (one whose target has no layout
  // position, e.g. `output`) can borrow that node's label ("Best SP")
  // instead of repeating it in the geometry. Keeps the copy in the
  // optimizer pipeline JSON, the single source of truth.
  const nodeLabel: Record<string, string> = Object.fromEntries(
    view.nodes.map((n) => [n.id, n.label]),
  );

  // The active node's human label — the accessible, color-independent echo of
  // the pulse. The SVG is aria-hidden, so this toolbar text (in an aria-live
  // region) is the channel that announces *which* node is live; it also makes
  // the signal legible at a glance and under prefers-reduced-motion (no pulse).
  // Gated on `viewingLive`: on a historical round there is no live node to name.
  const activeLabel = isLive && viewingLive && activeId ? nodeLabel[activeId] : null;
  // The card's own green: the RUN's state, not the connection's. `isLive` also requires a
  // fresh poll, so a throttled background tab greyed out a healthy run. Staleness is the
  // connection banner's job; `isLive` still gates the pulse, which must not animate stale data.
  const runIsRunning = viewingLive && dash?.run_phase === "running";
  // "idle" used to catch everything that wasn't live — a paused run, a run held at
  // the origin gate and a dead producer all read the same word. Name the phase the
  // server declares instead; only a genuinely phase-less payload falls through.
  const status = !dash
    ? "pending"
    : !viewingLive
      ? `round ${viewedRound}`
      : isLive
        ? activeLabel
          ? `live · ${activeLabel}`
          : "live"
        : runPhaseLabel(dash.run_phase, dash.stop_reason);

  const markerColors: Record<string, string> = {
    arrh: colors.txt,
    "arrh-loop": colors.ok,
    "arrh-dir": colors.acc,
    "arrh-esc": colors.esc,
  };

  return (
    <div className={cx("workflow-card", runIsRunning && "running")}>
      <div className="workflow-toolbar">
        <span className="workflow-title">Optimizer</span>
        <RoundAxis />
        <span
          style={{ color: runIsRunning ? colors.ok : colors.txt }}
          aria-live="polite"
        >
          ● {status}
        </span>
        <CopyButton data={roundNodes} title="Copy the viewed round's nodes as JSON" />
      </div>
      <div className="workflow-canvas-bg">
        {/* The CSS pins aspect-ratio 360/160; a stray row makes the viewBox
            taller, so the ratio has to follow or the diagram squashes. */}
        <div className="workflow-canvas" style={{ aspectRatio: `${canvasW} / ${canvasH}` }}>
          <svg
            width="100%"
            height="100%"
            viewBox={`0 0 ${canvasW} ${canvasH}`}
            preserveAspectRatio="xMidYMid meet"
            style={{ position: "absolute", inset: 0 }}
            aria-hidden="true"
          >
            <defs>
              {Object.entries(markerColors).map(([id, fill]) => (
                <marker key={id} id={id} markerWidth="9" markerHeight="9" refX="7.5" refY="4.5" orient="auto">
                  <path d="M0,0 L9,4.5 L0,9 z" fill={fill} />
                </marker>
              ))}
            </defs>
            {view.edges.map((e) => {
              const geom = edges[`${e.from}>${e.to}`];
              if (!geom) return null;
              const v = EDGE_VARIANTS[geom.kind] ?? EDGE_FORWARD;
              const stroke = colors[v.color];
              // Geometry label wins (brief / plan); otherwise, when the
              // target is a node with no dot, fall back to its label so
              // the terminal arrow reads "Best SP" straight from the JSON.
              const edgeLabel = geom.label ?? (layout[e.to] ? undefined : nodeLabel[e.to]);
              return (
                <g key={`${e.from}>${e.to}`}>
                  <path d={geom.d} fill="none" stroke={stroke} strokeWidth="2" strokeDasharray={v.dash || undefined} markerEnd={`url(#${v.marker})`} />
                  {edgeLabel && geom.labelXY && (
                    <text x={geom.labelXY[0]} y={geom.labelXY[1]} fontSize="12" fill={stroke} textAnchor="middle" fontFamily="ui-sans-serif,system-ui" paintOrder="stroke" stroke={colors.bg} strokeWidth="3">{edgeLabel}</text>
                  )}
                </g>
              );
            })}
            {strayNodes.map((n, i) => {
              // Evenly spaced along a row below the diagram, labelled, so a node
              // the geometry does not know about is impossible to miss.
              const x = ((i + 0.5) * canvasW) / strayNodes.length;
              const y = CANVAS_H + STRAY_ROW_H / 2;
              return (
                <g key={`stray-${n.id}`} className="wf-stray">
                  <title>{`${n.label} — served by the optimizer manifest, missing from LAYOUT`}</title>
                  <circle className="wf-dot kind-unplaced" cx={x} cy={y - 6} r={DOT_R - 3} />
                  <text className="wf-dot-label" x={x} y={y + 12} textAnchor="middle">
                    {n.label}
                  </text>
                </g>
              );
            })}
            {view.nodes.map((n) => {
              const pos = layout[n.id];
              if (!pos) return null;
              const data = roundNodes[n.id];
              const hasData = !!data;
              // Gated on `isLive` so a frozen canvas (process killed, freshness
              // lapsed) stops pulsing the last phase's node — `dash.state` stays
              // at the last phase but no node is "active". Gated on `viewingLive`
              // because the round axis now lives in this card's own toolbar: with
              // round 2 picked while round 5 runs, a pulsing dot would be claiming
              // round 5's liveness for round 2's picture.
              const isActive = isLive && viewingLive && activeId === n.id;
              const isSelected = selected?.scope === "optimizer" && selected.id === n.id;
              const isIo = n.kind === "io";
              const dotCls = [
                "wf-dot",
                `kind-${n.kind || "llm"}`,
                isActive ? "active" : "",
                isSelected ? "selected" : "",
                !hasData && !isActive && n.kind === "llm" ? "dim" : "",
              ]
                .filter(Boolean)
                .join(" ");
              // An audit twin still in flight is not an idle node. Every source flip spends at
              // least one round-trip with an empty map, and rendering that as "idle" told the
              // operator a node had never fired when the answer had simply not arrived.
              const sub =
                n.kind === "io"
                  ? ""
                  : n.kind === "measurement"
                    ? nodeKindLabel(n.kind)
                    : hasData
                      ? data.model || "—"
                      : nodesLoading
                        ? "…"
                        : "idle";
              const tip = TERMS[`node_${n.id}`] || undefined;
              // Label placement: default is centred below the dot; the
              // vertical layout overrides to sit beside the dot.
              const lDx = pos.labelDx ?? 0;
              const lDy = pos.labelDy ?? DOT_R + 12;
              const lAnchor = pos.labelAnchor ?? "middle";
              return (
                <g
                  key={n.id}
                  className="wf-node"
                  role="button"
                  tabIndex={isIo ? -1 : 0}
                  aria-label={isIo ? `${n.label} (I/O)` : `Node: ${n.label}`}
                  aria-pressed={isSelected || undefined}
                  aria-disabled={isIo || undefined}
                  onClick={() =>
                    !isIo && setSelected(isSelected ? null : { id: n.id, scope: "optimizer" })
                  }
                  onKeyDown={(e) => {
                    if (isIo) return;
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setSelected(isSelected ? null : { id: n.id, scope: "optimizer" });
                    }
                  }}
                >
                  {tip && <title>{tip}</title>}
                  {/* Full-node hit target — the dot plus its label band, so a
                      click anywhere on the node selects it (the 11px dot alone
                      leaves the gap between dot and label dead). Mirrors the
                      lineage node's transparent backing rect. Skipped for I/O
                      nodes, which carry no selection handler. */}
                  {!isIo && (() => {
                    // Spans dot AND label band, whichever way the label sits —
                    // a node labelling above its dot would otherwise get a rect
                    // of near-zero (or negative) height and lose the hit target.
                    const dotTop = -(DOT_R + 8);
                    const dotBottom = DOT_R + 8;
                    const labelTop = lDy - 11;
                    const labelBottom = lDy + (sub ? 16 : 4);
                    const top = Math.min(dotTop, labelTop);
                    return (
                      <rect
                        x={pos.cx - (DOT_R + 8)}
                        y={pos.cy + top}
                        width={2 * (DOT_R + 8)}
                        height={Math.max(dotBottom, labelBottom) - top}
                        fill="transparent"
                      />
                    );
                  })()}
                  <circle className={dotCls} cx={pos.cx} cy={pos.cy} r={DOT_R} />
                  <text
                    className="wf-dot-label"
                    x={pos.cx + lDx}
                    y={pos.cy + lDy}
                    textAnchor={lAnchor}
                  >
                    {n.label}
                  </text>
                  {sub && (
                    <text
                      className="wf-dot-sub"
                      x={pos.cx + lDx}
                      y={pos.cy + lDy + 12}
                      textAnchor={lAnchor}
                    >
                      {sub}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        </div>
      </div>
    </div>
  );
}

