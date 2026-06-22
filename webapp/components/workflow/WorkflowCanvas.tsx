"use client";
import { useEffect, useState } from "react";
import {
  CANVAS_W,
  CANVAS_H,
  DOT_R,
  EDGES,
  LAYOUT,
  phaseToNodeId,
} from "./layout";
import { TERMS } from "@/lib/terms";
import { cx } from "@/lib/cx";
import { getCss } from "@/lib/theme";
import { useSelection } from "@/lib/SelectionContext";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { CopyButton } from "@/components/ui";
import type { NodeDataLike, PipelineDoc } from "./types";

// Edge variants — collapses three parallel switches (stroke colour key,
// dasharray, arrowhead marker id) into one row per kind. Add a kind:
// extend the map, no other site changes.
type ColorKey = "txt" | "ok" | "acc" | "esc";
const EDGE_VARIANTS: Record<string, { color: ColorKey; dash: string; marker: string }> = {
  forward:   { color: "txt", dash: "",    marker: "arrh" },
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
  const canvasH = CANVAS_H;
  const activeId = phaseToNodeId(dash?.state);
  // Node selection rides the shared SelectionContext so the Now lane can
  // swap the 3-col row for OptimizerNodeDetail when a node is picked. The
  // viewed-round axis is owned by RoundTabsStrip (same tab) — the canvas
  // reads node selection only.
  const { node: selected, setSelectionForNode: setSelected } = useSelection();
  // Bumped by the MutationObserver below on `data-theme` flips; drives the
  // `colors` memo so SVG strokes/labels re-derive from the new CSS vars.
  const [themeTick, setThemeTick] = useState(0);

  useEffect(() => {
    const obs = new MutationObserver(() => setThemeTick((n) => n + 1));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);

  // Re-derived on every render — `themeTick` bumps cause the re-render,
  // which re-reads CSS vars fresh. No memo: 5 var reads is cheap, and
  // useMemo would need themeTick in its deps even though the body doesn't
  // reference it (tripping react-hooks/exhaustive-deps).
  void themeTick;
  const colors =
    typeof window === "undefined"
      ? { txt: "#525252", ok: "#1a8265", acc: "#090C9B", esc: "#060888", bg: "#F5F1EA" }
      : {
          txt: getCss("--color-text-secondary"),
          ok: getCss("--color-success"),
          acc: getCss("--color-accent"),
          esc: getCss("--color-accent-strong"),
          bg: getCss("--color-background-tertiary"),
        };

  if (!view) {
    return (
      <div className="workflow-card">
        <div className="workflow-toolbar">
          <span style={{ flex: 1 }}>Optimizer</span>
          <span id="wf-status" style={{ color: "var(--color-text-tertiary)" }}>● topology pending</span>
        </div>
        <div className="workflow-canvas-bg" style={{ minHeight: 200 }} />
      </div>
    );
  }

  const currentNodes = (dash?.current_round?.nodes ?? {}) as Record<string, NodeDataLike>;

  // Node id → label, so a terminal edge (one whose target has no layout
  // position, e.g. `output`) can borrow that node's label ("Best SP")
  // instead of repeating it in the geometry. Keeps the copy in the
  // optimizer pipeline JSON, the single source of truth.
  const nodeLabel: Record<string, string> = Object.fromEntries(
    view.nodes.map((n) => [n.id, n.label]),
  );

  const markerColors: Record<string, string> = {
    arrh: colors.txt,
    "arrh-loop": colors.ok,
    "arrh-dir": colors.acc,
    "arrh-esc": colors.esc,
  };

  return (
    <div className={cx("workflow-card", isLive && "running")}>
      <div className="workflow-toolbar">
        <span style={{ flex: 1 }}>Optimizer</span>
        <span style={{ color: isLive ? colors.ok : colors.txt }}>
          ● {isLive ? "live" : dash ? "idle" : "pending"}
        </span>
        <CopyButton
          data={dash?.current_round ?? view}
          title="Copy the optimizer round as JSON"
        />
      </div>
      <div className="workflow-canvas-bg">
        <div className="workflow-canvas">
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
              const v = EDGE_VARIANTS[geom.kind] ?? EDGE_VARIANTS.forward;
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
            {view.nodes.map((n) => {
              const pos = layout[n.id];
              if (!pos) return null;
              const data = currentNodes[n.id];
              const hasData = !!data;
              // Gated on `isLive` so a frozen canvas (process killed,
              // freshness lapsed) stops pulsing the last phase's node —
              // `dash.state` stays at the last phase but no node is "active".
              const isActive = isLive && activeId === n.id;
              const isSelected = selected === n.id;
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
              const sub =
                n.kind === "io"
                  ? ""
                  : n.kind === "measurement"
                    ? "system step"
                    : hasData
                      ? data?.model || "—"
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
                  onClick={() => !isIo && setSelected(isSelected ? null : n.id)}
                  onKeyDown={(e) => {
                    if (isIo) return;
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setSelected(isSelected ? null : n.id);
                    }
                  }}
                >
                  {tip && <title>{tip}</title>}
                  {/* Full-node hit target — the dot plus its label band, so a
                      click anywhere on the node selects it (the 11px dot alone
                      leaves the gap between dot and label dead). Mirrors the
                      lineage node's transparent backing rect. Skipped for I/O
                      nodes, which carry no selection handler. */}
                  {!isIo && (
                    <rect
                      x={pos.cx - (DOT_R + 8)}
                      y={pos.cy - (DOT_R + 8)}
                      width={2 * (DOT_R + 8)}
                      height={lDy + (sub ? 26 : 14) + (DOT_R + 8)}
                      fill="transparent"
                    />
                  )}
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

