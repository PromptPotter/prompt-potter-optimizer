"use client";
import { useEffect, useState } from "react";
import { CANVAS_W, CANVAS_H, EDGES, LAYOUT, phaseToNodeId } from "./layout";
import { TERMS } from "@/lib/terms";
import { getCss } from "@/lib/theme";
import { roundOf, type DashboardSnapshot } from "@/lib/poll";
import { useSelection } from "@/components/dashboard/SelectionContext";

export interface PipelineView {
  nodes: { id: string; label: string; kind?: string }[];
  edges: { from: string; to: string }[];
}

export interface PipelineDoc {
  view?: PipelineView;
  nodes?: Record<string, { type?: string; config?: Record<string, unknown>; model?: string }>;
}

export interface NodeDataLike {
  model?: string;
  duration_s?: number;
  round?: number;
  timestamp?: string;
  usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
  input?: { template_name?: string };
  output?: { candidates?: { idx?: number; stats?: Record<string, unknown> }[] };
}

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
}

export function WorkflowCanvas({ pipeline, dash }: Props) {
  const view = pipeline?.view;
  const activeId = phaseToNodeId(dash?.state);
  // Node selection rides the shared SelectionContext so the Now lane can
  // swap the 3-col row for OptimizerNodeDetail when a node is picked.
  const { node: selected, setNode: setSelected } = useSelection();
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
      ? { txt: "#9ca3af", ok: "#10b981", acc: "#f59e0b", esc: "#ea580c", bg: "#0d0d0d" }
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
        <span style={{ color: dash ? colors.ok : colors.txt }}>● {dash ? "live" : "pending"}</span>
        {(() => {
          const r = roundOf(dash);
          return r != null && r !== 0 ? (
            <span style={{ color: "var(--color-text-tertiary)", marginLeft: 6 }}>round {r}</span>
          ) : null;
        })()}
      </div>
      <div className="workflow-canvas-bg">
        <div className="workflow-canvas">
          <svg
            width="100%"
            height="100%"
            viewBox={`0 0 ${CANVAS_W} ${CANVAS_H}`}
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
              const geom = EDGES[`${e.from}>${e.to}`];
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
              const pos = LAYOUT[n.id];
              if (!pos) return null;
              const data = currentNodes[n.id];
              const hasData = !!data;
              const isActive = activeId === n.id;
              const cls = ["wf-box", `kind-${n.kind || "llm"}`];
              if (isActive) cls.push("active");
              if (!hasData && !isActive && n.kind === "llm") cls.push("dim");
              if (selected === n.id) cls.push("selected");
              const sub = n.kind === "io" ? ""
                : n.kind === "measurement" ? "system step"
                : hasData ? (data?.model || "—") : "idle";
              const tip = TERMS[`node_${n.id}`] || "";
              return (
                <foreignObject key={n.id} x={pos.x} y={pos.y} width={pos.w} height={pos.h} style={{ overflow: "visible" }}>
                  <div style={{ width: "100%", height: "100%" }}>
                    <button
                      type="button"
                      className="wf-node"
                      aria-label={n.kind === "io" ? `${n.label} (I/O)` : `Node: ${n.label}`}
                      aria-pressed={selected === n.id ? "true" : undefined}
                      tabIndex={n.kind === "io" ? -1 : 0}
                      disabled={n.kind === "io"}
                      onClick={() => n.kind !== "io" && setSelected(selected === n.id ? null : n.id)}
                      title={tip || undefined}
                    >
                      <div className={cls.join(" ")}>
                        <div className="wf-node-title">{n.label}</div>
                        {sub && <div className="wf-node-sub">{sub}</div>}
                      </div>
                    </button>
                  </div>
                </foreignObject>
              );
            })}
          </svg>
        </div>
      </div>
    </div>
  );
}

