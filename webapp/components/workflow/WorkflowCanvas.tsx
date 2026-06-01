"use client";
import { useEffect, useMemo, useState } from "react";
import {
  CANVAS_W,
  CANVAS_H,
  CANVAS_V_W,
  CANVAS_V_H,
  DOT_R,
  EDGES,
  EDGES_V,
  LAYOUT,
  LAYOUT_V,
  phaseToNodeId,
} from "./layout";
import { TERMS } from "@/lib/terms";
import { getCss } from "@/lib/theme";
import { roundOf, type DashboardSnapshot } from "@/lib/poll";
import { useIsPortraitPhone } from "@/lib/useMediaQuery";
import { useSelection } from "@/components/dashboard/SelectionContext";
import { cx } from "@/lib/cx";
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
  dash: DashboardSnapshot | null;
  // Real liveness from the cycle stream (poll age), not `dash` truthiness —
  // a frozen campaign still has a `dash` snapshot but is not live.
  isLive: boolean;
}

export function WorkflowCanvas({ pipeline, dash, isLive }: Props) {
  const view = pipeline?.view;
  // Portrait phone re-flows the pipeline top-to-bottom; everything else
  // keeps the wide left-to-right layout. Same graph, swapped geometry.
  const vertical = useIsPortraitPhone();
  const layout = vertical ? LAYOUT_V : LAYOUT;
  const edges = vertical ? EDGES_V : EDGES;
  const canvasW = vertical ? CANVAS_V_W : CANVAS_W;
  const canvasH = vertical ? CANVAS_V_H : CANVAS_H;
  const activeId = phaseToNodeId(dash?.state);
  // Node selection rides the shared SelectionContext so the Now lane can
  // swap the 3-col row for OptimizerNodeDetail when a node is picked.
  // The round picker in the toolbar writes `selection.round` — the same
  // viewed-round state the lineage tree, fitness chart, RoundTabsStrip,
  // RoundSamplesView, and OptimizerNodeDetail all read.
  const {
    node: selected,
    setSelectionForNode: setSelected,
    round: selectedRound,
    setSelectionForRound,
  } = useSelection();
  const liveRound = roundOf(dash);
  const completedRounds = useMemo(
    () => ((dash?.rounds ?? []).map((r) => r.round)).sort((a, b) => b - a),
    [dash?.rounds],
  );
  // `live` only appears when there's an in-flight round NOT yet summarized
  // into `dash.rounds[]` — otherwise the picker would carry both a "live"
  // entry and a completed entry for the same round number.
  const liveActive = liveRound != null && !completedRounds.includes(liveRound);
  const showPicker = completedRounds.length > 0 || liveActive;
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

  const markerColors: Record<string, string> = {
    arrh: colors.txt,
    "arrh-loop": colors.ok,
    "arrh-dir": colors.acc,
    "arrh-esc": colors.esc,
  };

  return (
    <div className="workflow-card">
      <div className="workflow-toolbar">
        <span style={{ flex: 1 }}>Optimizer</span>
        <span style={{ color: isLive ? colors.ok : colors.txt }}>
          ● {isLive ? "live" : dash ? "idle" : "pending"}
        </span>
        {showPicker && (
          <label className="workflow-round-pick">
            round
            <select
              value={selectedRound ?? ""}
              onChange={(e) =>
                setSelectionForRound(
                  e.target.value === "" ? null : Number(e.target.value),
                )
              }
              aria-label="Choose round to inspect"
              title="Switch the viewed round — drives the optimizer node detail, lineage, fitness, and samples"
            >
              {liveActive && <option value="">{liveRound} (live)</option>}
              {completedRounds.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <div className="workflow-canvas-bg">
        <div className={cx("workflow-canvas", vertical && "vertical")}>
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
                <marker key={id} id={id} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                  <path d="M0,0 L8,4 L0,8 z" fill={fill} />
                </marker>
              ))}
            </defs>
            {view.edges.map((e) => {
              const geom = edges[`${e.from}>${e.to}`];
              if (!geom) return null;
              const v = EDGE_VARIANTS[geom.kind] ?? EDGE_VARIANTS.forward;
              const stroke = colors[v.color];
              return (
                <g key={`${e.from}>${e.to}`}>
                  <path d={geom.d} fill="none" stroke={stroke} strokeWidth="1.5" strokeDasharray={v.dash || undefined} markerEnd={`url(#${v.marker})`} />
                  {geom.label && geom.labelXY && (
                    <text x={geom.labelXY[0]} y={geom.labelXY[1]} fontSize="12" fill={stroke} textAnchor="middle" fontFamily="ui-sans-serif,system-ui" paintOrder="stroke" stroke={colors.bg} strokeWidth="3">{geom.label}</text>
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

